using Refrain.Core.Index;

namespace Refrain.Core;

/// <summary>The library as the window sees it (SPEC §3, §4): the index, its indexer and watcher, and the roots'
/// online check. Every root action runs off the UI thread; events come from background threads.</summary>
public sealed class Library : IDisposable
{
    static readonly TimeSpan OnlineCheck = TimeSpan.FromSeconds(30);

    public Database Db { get; }
    public Indexer Indexer { get; }
    public Lyrics.LyricsService Lyrics { get; }
    readonly Watcher watcher;
    readonly Timer online;
    long lastWake;

    /// <summary>Roots were added, removed, or went on or offline.</summary>
    public event Action? RootsChanged;

    public Library(string databasePath)
    {
        Db = new Database(databasePath);
        Indexer = new Indexer(Db);
        Lyrics = new Lyrics.LyricsService(Db, new Lyrics.LrclibClient());
        // New tracks may need lookups: as soon as their tags are written, not when the scan ends (Hugo, 2026-10-31),
        // at most once a second while a scan writes (each wake may query the index for what is pending).
        Indexer.TracksWritten += () =>
        {
            var now = Environment.TickCount64;
            if (now - Interlocked.Read(ref lastWake) < 1000) return;
            Interlocked.Exchange(ref lastWake, now);
            Lyrics.Wake();
        };
        Indexer.ScanFinished += Lyrics.Wake;
        Lyrics.Start();
        watcher = new Watcher(Indexer);
        watcher.Unwatchable += root => Log.Info($"{root} isn't watched; folders are checked when opened.");
        online = new Timer(_ => CheckOnline(), null, TimeSpan.Zero, OnlineCheck);
    }

    /// <summary>Every root is rescanned at start (from the first online check) and when it comes back online.</summary>
    void CheckOnline()
    {
        try
        {
            List<Root> roots;
            using (var conn = Db.Read()) roots = Roots.All(conn);
            var changed = false;
            foreach (var root in roots)
            {
                var now = Directory.Exists(root.Path);
                if (now == root.Online && !first) continue;
                if (now != root.Online)
                {
                    Db.Write((conn, tx) => Roots.SetOnline(conn, tx, root.Id, now)).Wait();
                    changed = true;
                }
                if (now) Indexer.ScanRoot(root.Id);
            }
            first = false;
            watcher.Watch(roots.Where(r => Directory.Exists(r.Path)).Select(r => r.Path));
            if (changed) RootsChanged?.Invoke();
        }
        catch (Exception e)
        {
            Log.Error("Online check failed", e);
        }
    }

    bool first = true;

    public async Task<AddResult> Add(string path)
    {
        var result = await Db.Write((conn, tx) => Roots.Add(conn, tx, path));
        if (result.Outcome is AddOutcome.Added or AddOutcome.Merged)
        {
            Indexer.ScanRoot(result.RootId!.Value);
            using (var conn = Db.Read())
                watcher.Watch(Roots.All(conn).Where(r => r.Online).Select(r => r.Path));
            RootsChanged?.Invoke();
        }
        return result;
    }

    public async Task Remove(long rootId)
    {
        await Db.Write((conn, tx) => Roots.Remove(conn, tx, rootId));
        using (var conn = Db.Read())
            watcher.Watch(Roots.All(conn).Where(r => r.Online).Select(r => r.Path));
        RootsChanged?.Invoke();
    }

    /// <summary>A folder was opened: it is checked against the disk at once (stale-while-revalidate, SPEC §4), which
    /// also covers roots that can't be watched.</summary>
    public void FolderOpened(long folderId) => Indexer.Urgent(folderId);

    public void Dispose()
    {
        online.Dispose();
        watcher.Dispose();
        Lyrics.Dispose();
        Indexer.Dispose();
        Db.Dispose();
    }
}
