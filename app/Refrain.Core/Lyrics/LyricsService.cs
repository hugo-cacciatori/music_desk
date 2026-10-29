using Refrain.Core.Index;

namespace Refrain.Core.Lyrics;

/// <summary>Where the lookups stand, for the status line: keys left, a pause after the breaker tripped, and keys
/// whose lookup failed and wait to be tried again.</summary>
public sealed record LyricsProgress(long Pending, DateTime? PausedUntil, bool Offline, int Failed, bool Busy);

/// <summary>LRCLIB lookups in the background (SPEC §9): every track under the roots, four at a time, the selected
/// track first, then the open folder, then the rest folder by folder. One lookup per key. Errors aren't cached: a
/// failed key sits out 10 minutes; after 10 failures in a row every lookup pauses 10 minutes (the circuit breaker).
/// Its state lives in the index, so it resumes across restarts.</summary>
public sealed class LyricsService : IDisposable
{
    const int Workers = 4;
    const int Batch = 200;
    const int BreakerThreshold = 10;
    public static readonly TimeSpan SitOut = TimeSpan.FromMinutes(10);

    readonly Database db;
    readonly LrclibClient? client;
    readonly Func<DateOnly> today;
    readonly Func<DateTime> now;
    readonly Lock gate = new();
    readonly SemaphoreSlim signal = new(0);
    readonly CancellationTokenSource stop = new();
    readonly List<Task> workers = [];

    (PendingTrack Track, bool Forced)? selected;
    List<PendingTrack> folderQueue = [];
    readonly Queue<PendingTrack> background = new();
    readonly HashSet<string> inFlight = [];
    readonly HashSet<string> done = [];
    readonly Dictionary<string, (DateTime At, string Error)> failed = [];
    int consecutive;
    DateTime? pausedUntil;
    long pending;
    bool refilling;
    bool exhausted;
    bool retryScheduled;
    int busy;

    public bool Offline { get; }

    /// <summary>A key's result was recorded (raised on a worker thread).</summary>
    public event Action<string>? EntryReady;
    /// <summary>Folders whose LRCLIB column changed.</summary>
    public event Action<IReadOnlyList<long>>? FoldersChanged;
    public event Action<LyricsProgress>? ProgressChanged;
    /// <summary>The breaker tripped: lookups pause until the given time.</summary>
    public event Action<DateTime>? Unreachable;

    /// <summary>No <paramref name="client"/>, or <paramref name="offline"/>: nothing is looked up (tests).</summary>
    public LyricsService(Database db, LrclibClient? client, bool offline = false, Func<DateOnly>? today = null,
        Func<DateTime>? now = null)
    {
        this.db = db;
        this.client = client;
        Offline = offline || client is null;
        this.today = today ?? (() => DateOnly.FromDateTime(DateTime.Now));
        this.now = now ?? (() => DateTime.Now);
    }

    public void Start()
    {
        for (var i = 0; i < Workers; i++) workers.Add(Task.Run(Work));
        Wake();
    }

    /// <summary>New tracks may need lookups (after a scan): the background list is refilled.</summary>
    public void Wake()
    {
        lock (gate) exhausted = false;
        signal.Release(Workers);
    }

    /// <summary>The selected track goes first, when it still needs a lookup.</summary>
    public void Prioritize(long entryId) => _ = Task.Run(() => Select(entryId, forced: false));

    /// <summary>One track is looked up again at once, whatever its cached entry says; the breaker's pause ends.</summary>
    public void Retry(long entryId)
    {
        lock (gate)
        {
            pausedUntil = null;
            consecutive = 0;
        }
        _ = Task.Run(() => Select(entryId, forced: true));
    }

    void Select(long entryId, bool forced)
    {
        PendingTrack? track;
        LyricsEntry? entry;
        using (var conn = db.Read())
        {
            track = LyricsIndex.TrackFor(conn, entryId);
            entry = track is null ? null : LyricsIndex.Get(conn, track.Key);
        }
        if (track is null || (!forced && Lrclib.IsFresh(entry, today()))) return;
        lock (gate)
        {
            if (forced) failed.Remove(track.Key);
            selected = (track, forced);
        }
        signal.Release();
    }

    /// <summary>The open folder's tracks come next.</summary>
    public void PrioritizeFolder(long folderId) => _ = Task.Run(() =>
    {
        HashSet<string> skip;
        lock (gate) skip = [.. inFlight, .. done];
        List<PendingTrack> tracks;
        using (var conn = db.Read()) tracks = LyricsIndex.Pending(conn, Lrclib.RetryBefore(today()), folderId, skip);
        lock (gate) folderQueue = tracks;
        signal.Release(Math.Min(Workers, Math.Max(1, tracks.Count)));
    });

    /// <summary>Retry now: the pause ends and every failed key is tried again.</summary>
    public void RetryNow()
    {
        lock (gate)
        {
            pausedUntil = null;
            consecutive = 0;
            failed.Clear();
        }
        Wake();
        Report();
    }

    /// <summary>Whether this key's last lookup failed this session (the LRCLIB column shows "error").</summary>
    public bool IsFailed(string? key)
    {
        if (key is null) return false;
        lock (gate) return failed.ContainsKey(key);
    }

    public string? LastError(string? key)
    {
        if (key is null) return null;
        lock (gate) return failed.TryGetValue(key, out var f) ? f.Error : null;
    }

    public IReadOnlySet<string> FailedKeys()
    {
        lock (gate) return failed.Keys.ToHashSet();
    }

    public DateTime? PausedUntil
    {
        get
        {
            lock (gate) return pausedUntil;
        }
    }

    async Task Work()
    {
        while (!stop.IsCancellationRequested)
        {
            var next = Next();
            if (next is null)
            {
                try
                {
                    await signal.WaitAsync(stop.Token);
                }
                catch (OperationCanceledException)
                {
                    return;
                }
                continue;
            }
            if (next.Value.Refill) Refill();
            else await Process(next.Value.Track!, next.Value.Forced);
        }
    }

    (PendingTrack? Track, bool Forced, bool Refill)? Next()
    {
        lock (gate)
        {
            var time = now();
            if (pausedUntil is { } until && time >= until)
            {
                pausedUntil = null;
                consecutive = 0;
            }
            bool Free(PendingTrack t) => !inFlight.Contains(t.Key) && !done.Contains(t.Key) &&
                                         !(failed.TryGetValue(t.Key, out var f) && time - f.At < SitOut);
            if (selected is { } chosen && !inFlight.Contains(chosen.Track.Key))
            {
                selected = null;
                if (chosen.Forced || pausedUntil is null) return Claim(chosen.Track, chosen.Forced);
            }
            if (pausedUntil is not null) return null;
            while (folderQueue.Count > 0)
            {
                var t = folderQueue[0];
                folderQueue.RemoveAt(0);
                if (Free(t)) return Claim(t, false);
            }
            while (background.Count > 0)
            {
                var t = background.Dequeue();
                if (Free(t)) return Claim(t, false);
            }
            if (exhausted || refilling) return null;
            refilling = true;
            return (null, false, true);
        }
    }

    (PendingTrack, bool, bool) Claim(PendingTrack track, bool forced)
    {
        inFlight.Add(track.Key);
        busy++;
        return (track, forced, false);
    }

    void Refill()
    {
        HashSet<string> skip;
        lock (gate)
        {
            var time = now();
            skip = [.. inFlight, .. done, .. failed.Where(f => time - f.Value.At < SitOut).Select(f => f.Key)];
        }
        List<PendingTrack> batch;
        long count;
        using (var conn = db.Read())
        {
            batch = LyricsIndex.Pending(conn, Lrclib.RetryBefore(today()), skip: skip, limit: Batch);
            count = LyricsIndex.PendingCount(conn, Lrclib.RetryBefore(today()));
        }
        var retryLater = false;
        lock (gate)
        {
            foreach (var t in batch) background.Enqueue(t);
            pending = count;
            exhausted = batch.Count == 0;
            refilling = false;
            // Only failed keys left: they are tried again once they have sat out.
            if (exhausted && failed.Count > 0 && !retryScheduled) retryLater = retryScheduled = true;
        }
        if (retryLater)
            _ = Task.Delay(SitOut, stop.Token).ContinueWith(_ =>
            {
                lock (gate) retryScheduled = false;
                Wake();
            }, TaskScheduler.Default);
        if (batch.Count > 0) signal.Release(Workers);
        Report();
    }

    async Task Process(PendingTrack track, bool forced)
    {
        try
        {
            LyricsEntry? cached;
            using (var conn = db.Read()) cached = LyricsIndex.Get(conn, track.Key);
            if (!forced && Lrclib.IsFresh(cached, today()))
            {
                lock (gate) done.Add(track.Key);  // another track's lookup answered it meanwhile
                return;
            }
            if (Offline)
            {
                lock (gate) done.Add(track.Key);
                return;
            }
            LyricsEntry entry;
            try
            {
                entry = await Lrclib.Fetch(track.Track, client!, today(), stop.Token);
            }
            catch (Exception e) when (e is not OperationCanceledException || !stop.IsCancellationRequested)
            {
                DateTime? tripped = null;
                lock (gate)
                {
                    failed[track.Key] = (now(), $"{e.GetType().Name}: {e.Message}");
                    if (++consecutive >= BreakerThreshold && pausedUntil is null)
                        tripped = pausedUntil = now() + SitOut;
                }
                Log.Info($"LRCLIB lookup failed for {track.Track.Artist} - {track.Track.Title}: {e.Message}");
                if (tripped is { } until)
                {
                    Unreachable?.Invoke(until);
                    _ = Task.Delay(SitOut, stop.Token).ContinueWith(_ => Wake(), TaskScheduler.Default);
                }
                return;
            }
            var folders = await db.Write((conn, tx) => LyricsIndex.Record(conn, tx, track.Key, entry));
            lock (gate)
            {
                done.Add(track.Key);
                failed.Remove(track.Key);
                consecutive = 0;
                if (pending > 0) pending--;
            }
            EntryReady?.Invoke(track.Key);
            FoldersChanged?.Invoke(folders);
        }
        catch (OperationCanceledException) when (stop.IsCancellationRequested)
        {
        }
        catch (Exception e)
        {
            Log.Error($"The lyrics lookup of {track.Track.Artist} - {track.Track.Title} failed", e);
        }
        finally
        {
            lock (gate)
            {
                inFlight.Remove(track.Key);
                busy--;
            }
            Report();
        }
    }

    void Report()
    {
        LyricsProgress progress;
        lock (gate) progress = new LyricsProgress(pending, pausedUntil, Offline, failed.Count, busy > 0 || !exhausted);
        ProgressChanged?.Invoke(progress);
    }

    /// <summary>Waits until nothing is left to look up and no lookup runs: for tests.</summary>
    public async Task WaitIdle(TimeSpan timeout)
    {
        var end = DateTime.UtcNow + timeout;
        while (DateTime.UtcNow < end)
        {
            lock (gate)
                if (exhausted && busy == 0 && !refilling && background.Count == 0 && folderQueue.Count == 0 && selected is null)
                    return;
            await Task.Delay(10);
        }
        throw new TimeoutException("The lyrics service didn't go idle.");
    }

    public void Dispose()
    {
        stop.Cancel();
        try
        {
            Task.WaitAll([.. workers], TimeSpan.FromSeconds(5));
        }
        catch (AggregateException)
        {
        }
        client?.Dispose();
    }
}
