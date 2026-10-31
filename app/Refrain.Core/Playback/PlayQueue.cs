namespace Refrain.Core.Playback;

/// <summary>What the player does when an album ends (SPEC §37, the Player page): start it over (the default), or stop.</summary>
public enum AlbumEnd { Loop, Once }

/// <summary>What the player does when a task starts on the folder of the song playing or queued (SPEC §37): stop and say
/// why (the default, the most cautious), or skip to the next song the task doesn't touch.</summary>
public enum BusyFolder { Stop, Skip }

/// <summary>The player's order through an album (SPEC §37 "The player"): the album's songs in order, never a song whose
/// file a task holds.</summary>
public static class PlayQueue
{
    /// <summary>The song after <paramref name="current"/> that may play, wrapping to the first when the album loops;
    /// null when there is none (the album played once, or every other song busy).</summary>
    public static int? Next(int count, int current, AlbumEnd end, Func<int, bool> playable)
    {
        for (var step = 1; step <= count; step++)
        {
            var i = current + step;
            if (i >= count)
            {
                if (end == AlbumEnd.Once) return null;
                i -= count;
            }
            if (i == current) return playable(i) ? i : null;
            if (playable(i)) return i;
        }
        return null;
    }

    /// <summary>The song before <paramref name="current"/> that may play, wrapping to the last when the album loops.</summary>
    public static int? Previous(int count, int current, AlbumEnd end, Func<int, bool> playable)
    {
        for (var step = 1; step < count; step++)
        {
            var i = current - step;
            if (i < 0)
            {
                if (end == AlbumEnd.Once) return null;
                i += count;
            }
            if (playable(i)) return i;
        }
        return null;
    }
}
