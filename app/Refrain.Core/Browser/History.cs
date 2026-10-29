namespace Refrain.Core.Browser;

/// <summary>Back and forward through opened folders (SPEC §5 "As built (M2 task 6)"): opening a new folder clears
/// the forward stack; folders that left the index are skipped.</summary>
public sealed class History
{
    readonly Stack<long> back = new();
    readonly Stack<long> forward = new();

    public long? Current { get; private set; }
    public bool CanGoBack => back.Count > 0;
    public bool CanGoForward => forward.Count > 0;

    /// <summary>A folder opened by any way but Back and Forward.</summary>
    public void Open(long folderId)
    {
        if (Current == folderId) return;
        if (Current is { } current) back.Push(current);
        forward.Clear();
        Current = folderId;
    }

    /// <summary>The previous folder that still exists, or null (nothing changes then).</summary>
    public long? Back(Func<long, bool> exists) => Move(back, forward, exists);

    public long? Forward(Func<long, bool> exists) => Move(forward, back, exists);

    long? Move(Stack<long> from, Stack<long> to, Func<long, bool> exists)
    {
        while (from.Count > 0)
        {
            var target = from.Pop();
            if (!exists(target)) continue;
            if (Current is { } current) to.Push(current);
            Current = target;
            return target;
        }
        return null;
    }

    /// <summary>The current folder left the index and another one replaced it (its nearest surviving ancestor).</summary>
    public void Replace(long folderId) => Current = folderId;
}
