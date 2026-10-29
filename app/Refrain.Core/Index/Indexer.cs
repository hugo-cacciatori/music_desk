using System.Collections.Concurrent;
using System.Diagnostics;
using Refrain.Core.Tags;

namespace Refrain.Core.Index;

/// <summary>Background indexing (SPEC §4): one thread runs one scan at a time from a queue. Phase A walks the
/// folders breadth-first, so names are browsable and searchable at once; phase B reads the tags in a pool of four.
/// What the user asks for (the folder on screen, F5) is served first, even inside a running scan.</summary>
public sealed class Indexer : IDisposable
{
    const int Readers = 4;
    const int TagBatch = 100;
    static readonly TimeSpan BatchTime = TimeSpan.FromMilliseconds(200);

    readonly Database db;
    readonly BlockingCollection<Job> jobs = new();
    readonly ConcurrentQueue<long> urgent = new();
    readonly CancellationTokenSource stop = new();
    readonly Thread thread;
    int filesRead;
    int foldersListed;

    /// <summary>A folder's children or their tags changed (raised on the indexer thread).</summary>
    public event Action<long>? FolderChanged;
    /// <summary>A scan finished; folder totals are up to date (raised on the indexer thread).</summary>
    public event Action? ScanFinished;
    /// <summary>Progress: folders listed, files read, and whether work remains (throttled to 10 a second).</summary>
    public event Action<int, int, bool>? Progress;

    abstract record Job;
    sealed record RootJob(long RootId) : Job;
    sealed record PathJob(string Path, bool Recursive) : Job;

    public Indexer(Database db)
    {
        this.db = db;
        thread = new Thread(Run) { IsBackground = true, Name = "Indexer", Priority = ThreadPriority.BelowNormal };
        thread.Start();
    }

    /// <summary>A whole root, recursively.</summary>
    public void ScanRoot(long rootId) => Add(new RootJob(rootId));

    /// <summary>A path's folder (one level) or subtree; a path not indexed yet scans its nearest indexed ancestor's
    /// subtree. Resolved when the job runs.</summary>
    public void ScanPath(string path, bool recursive) => Add(new PathJob(path, recursive));

    /// <summary>This folder, one level, before anything else, including inside a running scan.</summary>
    public void Urgent(long folderId)
    {
        urgent.Enqueue(folderId);
        Add(new PathJob("", false));  // wakes an idle indexer; an empty path does nothing
    }

    void Add(Job job)
    {
        Interlocked.Increment(ref pendingJobs);
        jobs.Add(job);
    }

    /// <summary>Jobs queued or running.</summary>
    public bool Busy => Volatile.Read(ref pendingJobs) > 0;
    int pendingJobs;

    void Run()
    {
        try
        {
            foreach (var job in jobs.GetConsumingEnumerable(stop.Token))
            {
                try
                {
                    ServeUrgent();
                    switch (job)
                    {
                        case RootJob root:
                            RunRoot(root.RootId);
                            break;
                        case PathJob { Path.Length: > 0 } path:
                            RunPath(path.Path, path.Recursive);
                            break;
                    }
                }
                catch (Exception e) when (e is not OperationCanceledException)
                {
                    Log.Error("Indexing failed", e);
                }
                finally
                {
                    Interlocked.Decrement(ref pendingJobs);
                    ReportProgress(force: true);
                }
            }
        }
        catch (OperationCanceledException)
        {
        }
    }

    void RunRoot(long rootId)
    {
        string? path;
        long? top;
        using (var conn = db.Read())
        {
            var root = Roots.All(conn).FirstOrDefault(r => r.Id == rootId);
            if (root is null || !root.Online) return;  // an unreachable root isn't scanned
            path = root.Path;
            top = Queries.TopEntry(conn, rootId);
        }
        if (top is null) return;
        Walk(top.Value, path, recursive: true);
        Finish(rootId);
    }

    void RunPath(string path, bool recursive)
    {
        long id;
        long rootId;
        string folderPath;
        using (var conn = db.Read())
        {
            var found = Queries.Find(conn, path);
            if (found is null) return;
            id = found.Value.Id;
            if (!Queries.IsDir(conn, id)) id = Queries.ParentOf(conn, id) ?? id;
            recursive |= !found.Value.Exact;  // not indexed yet: the nearest ancestor's whole subtree
            folderPath = Queries.PathOf(conn, id)!;
            rootId = (long)conn.Scalar("SELECT root_id FROM entries WHERE id = $id", null, ("$id", id))!;
        }
        Walk(id, folderPath, recursive);
        Finish(rootId);
    }

    void Finish(long rootId)
    {
        db.Write((conn, tx) => ScanWrite.RecomputeStats(conn, tx, rootId)).Wait();
        ScanFinished?.Invoke();
    }

    /// <summary>Phase A over the folder (and its subtree), then phase B over the files it found.</summary>
    void Walk(long folderId, string path, bool recursive)
    {
        var queue = new Queue<(long, string)>();
        queue.Enqueue((folderId, path));
        var toRead = new List<ToRead>();
        while (queue.Count > 0)
        {
            stop.Token.ThrowIfCancellationRequested();
            ServeUrgent();
            var (id, folder) = queue.Dequeue();
            var found = Reconcile(id, folder);
            if (found is null) continue;
            toRead.AddRange(found.ToRead);
            if (recursive)
                foreach (var sub in found.Subfolders)
                    queue.Enqueue(sub);
        }
        ReadTags(toRead);
    }

    Reconciled? Reconcile(long folderId, string folderPath)
    {
        var listing = DiskListing.List(folderPath);
        Interlocked.Increment(ref foldersListed);
        ReportProgress();
        if (listing is null) return null;  // can't be listed: its index is kept
        var result = db.Write((conn, tx) => ScanWrite.Reconcile(conn, tx, folderId, folderPath, listing)).Result;
        if (result.Changed) FolderChanged?.Invoke(folderId);
        return result;
    }

    void ReadTags(List<ToRead> files)
    {
        var results = new ConcurrentQueue<(ToRead, TrackInfo)>();
        var pending = new Queue<ToRead>(files);
        var timer = Stopwatch.StartNew();
        while (pending.Count > 0)
        {
            stop.Token.ThrowIfCancellationRequested();
            ServeUrgent();
            var batch = new List<ToRead>();
            while (pending.Count > 0 && batch.Count < TagBatch) batch.Add(pending.Dequeue());
            Parallel.ForEach(batch, new ParallelOptions { MaxDegreeOfParallelism = Readers, CancellationToken = stop.Token },
                file => results.Enqueue((file, TagReader.Read(file.Path, file.Size, file.Mtime))));
            if (results.Count >= TagBatch || timer.Elapsed >= BatchTime || pending.Count == 0)
            {
                Flush(results);
                timer.Restart();
            }
        }
        Flush(results);
    }

    void Flush(ConcurrentQueue<(ToRead, TrackInfo)> results)
    {
        var batch = new List<(ToRead, TrackInfo)>();
        while (results.TryDequeue(out var item)) batch.Add(item);
        if (batch.Count == 0) return;
        var folders = db.Write((conn, tx) => ScanWrite.WriteTracks(conn, tx, batch)).Result;
        Interlocked.Add(ref filesRead, batch.Count);
        ReportProgress();
        foreach (var folder in folders) FolderChanged?.Invoke(folder);
    }

    /// <summary>The folders the user asked for: listed and read now, one level.</summary>
    void ServeUrgent()
    {
        while (urgent.TryDequeue(out var folderId))
        {
            string? path;
            using (var conn = db.Read()) path = Queries.PathOf(conn, folderId);
            if (path is null) continue;
            var found = Reconcile(folderId, path);
            if (found is null) continue;
            var results = new ConcurrentQueue<(ToRead, TrackInfo)>();
            Parallel.ForEach(found.ToRead, new ParallelOptions { MaxDegreeOfParallelism = Readers },
                file => results.Enqueue((file, TagReader.Read(file.Path, file.Size, file.Mtime))));
            Flush(results);
        }
    }

    long lastProgress;

    void ReportProgress(bool force = false)
    {
        var now = Environment.TickCount64;
        if (!force && now - Interlocked.Read(ref lastProgress) < 100) return;
        Interlocked.Exchange(ref lastProgress, now);
        var working = Busy;
        Progress?.Invoke(foldersListed, filesRead, working);
        if (!working)
        {
            Interlocked.Exchange(ref foldersListed, 0);
            Interlocked.Exchange(ref filesRead, 0);
        }
    }

    /// <summary>Stops after the current step; queued scans are dropped (the next start rescans every root).</summary>
    public void Dispose()
    {
        stop.Cancel();
        jobs.CompleteAdding();
        thread.Join(TimeSpan.FromSeconds(5));
    }

    /// <summary>Waits until the queue is empty and nothing runs: for tests.</summary>
    public void WaitIdle()
    {
        while (Busy) Thread.Sleep(10);
    }
}
