namespace Refrain.Core.Index;

/// <summary>Live updates (SPEC §4): one watcher per online root. Events are coalesced per folder and flushed after
/// 0.5 s without events, or at the latest after 5 s, so a long copy shows up while it runs. A file event rescans its
/// folder; a created or renamed folder also gets its subtree scanned.</summary>
public sealed class Watcher : IDisposable
{
    static readonly TimeSpan Quiet = TimeSpan.FromMilliseconds(500);
    static readonly TimeSpan Longest = TimeSpan.FromSeconds(5);

    readonly Indexer indexer;
    readonly Dictionary<string, FileSystemWatcher> watchers = new(StringComparer.OrdinalIgnoreCase);
    readonly Dictionary<string, bool> pending = new(StringComparer.OrdinalIgnoreCase);  // folder → recursive
    readonly Lock gate = new();
    readonly Timer timer;
    DateTime first;

    /// <summary>A root that can't be watched; it relies on revalidation when a folder opens.</summary>
    public event Action<string>? Unwatchable;

    public Watcher(Indexer indexer)
    {
        this.indexer = indexer;
        timer = new Timer(_ => Flush(), null, Timeout.Infinite, Timeout.Infinite);
    }

    /// <summary>Keeps the watches equal to these roots.</summary>
    public void Watch(IEnumerable<string> roots)
    {
        var wanted = roots.ToHashSet(StringComparer.OrdinalIgnoreCase);
        lock (gate)
        {
            foreach (var gone in watchers.Keys.Where(k => !wanted.Contains(k)).ToList())
            {
                watchers[gone].Dispose();
                watchers.Remove(gone);
            }
            foreach (var root in wanted.Where(r => !watchers.ContainsKey(r)))
            {
                try
                {
                    var watcher = new FileSystemWatcher(root)
                    {
                        IncludeSubdirectories = true,
                        InternalBufferSize = 64 * 1024,
                        NotifyFilter = NotifyFilters.FileName | NotifyFilters.DirectoryName | NotifyFilters.Size |
                                       NotifyFilters.LastWrite,
                    };
                    watcher.Created += (_, e) => Changed(e.FullPath, created: true);
                    watcher.Changed += (_, e) => Changed(e.FullPath, created: false);
                    watcher.Deleted += (_, e) => Changed(e.FullPath, created: false);
                    watcher.Renamed += (_, e) =>
                    {
                        Changed(e.OldFullPath, created: false);
                        Changed(e.FullPath, created: true);
                    };
                    watcher.Error += (_, _) => Overflow(root);
                    watcher.EnableRaisingEvents = true;
                    watchers[root] = watcher;
                }
                catch (Exception e) when (e is ArgumentException or IOException or UnauthorizedAccessException)
                {
                    Log.Info($"Can't watch {root}: {e.Message}");
                    Unwatchable?.Invoke(root);
                }
            }
        }
    }

    void Changed(string path, bool created)
    {
        var folder = Path.GetDirectoryName(path);
        if (folder is null) return;
        // A created or moved-in folder: its own subtree too (a check on disk, off the UI thread here).
        var recursive = created && Directory.Exists(path);
        Queue(recursive ? path : folder, recursive);
        if (recursive) Queue(folder, false);
    }

    /// <summary>The buffer overflowed: events were lost, so the whole root is rescanned.</summary>
    void Overflow(string root)
    {
        Log.Info($"Watcher overflow on {root}: rescanning it");
        Queue(root, true);
    }

    void Queue(string folder, bool recursive)
    {
        lock (gate)
        {
            if (pending.Count == 0) first = DateTime.UtcNow;
            pending[folder] = pending.GetValueOrDefault(folder) || recursive;
            var wait = DateTime.UtcNow - first >= Longest ? TimeSpan.Zero : Quiet;
            timer.Change(wait, Timeout.InfiniteTimeSpan);
        }
    }

    void Flush()
    {
        List<KeyValuePair<string, bool>> batch;
        lock (gate)
        {
            batch = pending.ToList();
            pending.Clear();
        }
        // A folder already covered by a recursive scan in the same flush is dropped.
        var recursive = batch.Where(b => b.Value).Select(b => b.Key).ToList();
        foreach (var (folder, deep) in batch)
        {
            if (recursive.Any(r => !r.Equals(folder, StringComparison.OrdinalIgnoreCase) && RealPath.IsInside(folder, r)))
                continue;
            indexer.ScanPath(folder, deep);
        }
    }

    public void Dispose()
    {
        lock (gate)
        {
            foreach (var watcher in watchers.Values) watcher.Dispose();
            watchers.Clear();
        }
        timer.Dispose();
    }
}
