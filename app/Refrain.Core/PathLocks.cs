namespace Refrain.Core;

/// <summary>A task holds files another task wanted (SPEC §35): the message says which item and which task.</summary>
public sealed class LockedException(string path, string owner) : Exception(PathLocks.BusyText(path, owner))
{
    public string Path { get; } = path;
    public string Owner { get; } = owner;
}

/// <summary>Path locks (SPEC §7, §16, §35), ported from the Python app's <c>core/path_locks.py</c>: a running task owns
/// its folder trees, a tag save its file. A lock on a folder covers everything below it; paths compare ignoring case,
/// as Windows does. Two locks can't overlap: the second is refused with the owner's name. Thread-safe: the tasks'
/// windows and the main window share one instance.</summary>
public sealed class PathLocks
{
    readonly Lock gate = new();
    readonly Dictionary<int, (string Owner, List<string> Keys)> locks = [];
    int next;

    /// <summary>A lock was taken or released (raised on the thread that did it).</summary>
    public event Action? Changed;

    /// <summary>The one refusal for files a task holds (Hugo, 2026-10-25): "Kid A is busy: ReplayGain is running on
    /// it."</summary>
    public static string BusyText(string path, string owner)
    {
        var name = System.IO.Path.GetFileName(System.IO.Path.TrimEndingDirectorySeparator(path));
        return $"{(name.Length > 0 ? name : path)} is busy: {owner} is running on it.";
    }

    static string Key(string path) =>
        System.IO.Path.TrimEndingDirectorySeparator(System.IO.Path.GetFullPath(path)).ToUpperInvariant();

    static bool Inside(string key, string folder) =>
        key.StartsWith(folder.TrimEnd('\\') + "\\", StringComparison.Ordinal);

    static bool Overlaps(string a, string b) => a == b || Inside(a, b) || Inside(b, a);

    /// <summary>Who holds a lock overlapping this path (above it, on it or inside it), or null.</summary>
    public string? Conflict(string path)
    {
        var key = Key(path);
        lock (gate)
            return locks.Values.FirstOrDefault(l => l.Keys.Any(k => Overlaps(key, k))).Owner;
    }

    /// <summary>Locks these paths (files or folder trees) for <paramref name="owner"/>; throws LockedException on any
    /// overlap.</summary>
    public int Acquire(IEnumerable<string> paths, string owner)
    {
        var list = paths.ToList();
        var keys = list.Select(Key).ToList();
        int token;
        lock (gate)
        {
            foreach (var (other, otherKeys) in locks.Values)
                for (var i = 0; i < keys.Count; i++)
                    if (otherKeys.Any(k => Overlaps(keys[i], k)))
                        throw new LockedException(list[i], other);
            token = ++next;
            locks[token] = (owner, keys);
        }
        Changed?.Invoke();
        return token;
    }

    public void Release(int token)
    {
        bool removed;
        lock (gate) removed = locks.Remove(token);
        if (removed) Changed?.Invoke();
    }

    /// <summary>Who holds a lock now.</summary>
    public HashSet<string> Owners()
    {
        lock (gate) return locks.Values.Select(l => l.Owner).ToHashSet();
    }
}
