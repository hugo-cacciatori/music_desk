using Microsoft.Data.Sqlite;
using Refrain.Core.Index;
using Refrain.Core.Search;

namespace Refrain.Core.Playback;

/// <summary>A song the player holds (SPEC §37): what the bar shows, where the file is, and its synced lyrics.</summary>
public sealed record PlayerSong(long Id, string Title, string Album, string Artist, string ArtistKey, string Path, string? Synced)
{
    /// <summary>The player's queue for an album of the discography view: its songs in order, those whose file the index
    /// no longer has left out; <c>Start</c> is <paramref name="song"/>'s place in it (0 when it was left out).</summary>
    public static (List<PlayerSong> Queue, int Start) QueueOf(SqliteConnection conn, ArtistAlbums data, int album, int song)
    {
        var each = data.Albums[album];
        var queue = new List<PlayerSong>();
        var start = 0;
        for (var i = 0; i < each.Songs.Count; i++)
        {
            var item = each.Songs[i];
            if (Queries.PathOf(conn, item.Id) is not { } path) continue;
            if (i == song) start = queue.Count;
            queue.Add(new PlayerSong(item.Id, item.Title, each.Name, data.Artist, data.Key, path, item.Synced));
        }
        return (queue, start);
    }

    /// <summary>Where Refrain reopens (SPEC §37 "Kept between sessions"): the album of the track at <paramref name="path"/>
    /// as the player's queue, on that track; null when the index no longer has it, or it has no artist.</summary>
    public static (List<PlayerSong> Queue, int Start)? Reopen(SqliteConnection conn, string path)
    {
        if (Queries.Find(conn, path) is not { Exact: true } hit || ArtistAlbums.For(conn, hit.Id) is not { } data) return null;
        var (queue, start) = QueueOf(conn, data, data.StartAlbum, data.StartSong);
        return queue.Count > 0 && queue[start].Id == hit.Id ? (queue, start) : null;
    }
}
