using System.Runtime.CompilerServices;
using Microsoft.Data.Sqlite;
using Refrain.Core.Index;

namespace Refrain.Tests;

static class Setup
{
    /// <summary>The app's own files (the log) go to a temporary folder, never the real data folder.</summary>
    [ModuleInitializer]
    internal static void IsolateData() =>
        Environment.SetEnvironmentVariable("REFRAIN_DATA",
            Directory.CreateTempSubdirectory("refrain-tests-data-").FullName);

    /// <summary>app/Refrain.Tests/Fixtures of the repository, found upward from the test binaries.</summary>
    public static string Fixtures
    {
        get
        {
            for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
            {
                var candidate = Path.Combine(dir.FullName, "app", "Refrain.Tests", "Fixtures");
                if (Directory.Exists(candidate)) return candidate;
            }
            throw new DirectoryNotFoundException("app/Refrain.Tests/Fixtures not found");
        }
    }

    public static void Copy(string from, string to)
    {
        Directory.CreateDirectory(to);
        foreach (var file in Directory.GetFiles(from)) File.Copy(file, Path.Combine(to, Path.GetFileName(file)));
        foreach (var dir in Directory.GetDirectories(from)) Copy(dir, Path.Combine(to, Path.GetFileName(dir)));
    }
}

/// <summary>A temporary folder holding an index file; deleted afterwards.</summary>
sealed class TempFolder : IDisposable
{
    public string Path { get; } = Directory.CreateTempSubdirectory("refrain-tests-").FullName;

    public string Sub(params string[] names)
    {
        var path = System.IO.Path.Combine([Path, .. names]);
        Directory.CreateDirectory(path);
        return path;
    }

    public void Dispose()
    {
        SqliteConnection.ClearAllPools();
        try
        {
            Directory.Delete(Path, recursive: true);
        }
        catch (IOException)
        {
        }
    }
}

/// <summary>A hand-built index (as the Python tests' Library): one root, folders, and tracks with their search
/// rows, written directly.</summary>
sealed class HandIndex : IDisposable
{
    readonly TempFolder temp = new();
    public SqliteConnection Conn { get; }
    public long Root { get; }
    public long Top { get; }

    public HandIndex()
    {
        Conn = new SqliteConnection($"Data Source={System.IO.Path.Combine(temp.Path, "library.db")}");
        Conn.Open();
        Schema.Configure(Conn, write: true);
        Schema.Migrate(Conn);
        Root = (long)Scalar("INSERT INTO roots (path, added_at) VALUES ('C:\\Music', 'x') RETURNING id")!;
        Top = Folder(null, "C:\\Music");
    }

    public object? Scalar(string sql, params (string, object?)[] values)
    {
        using var cmd = Conn.CreateCommand();
        cmd.CommandText = sql;
        foreach (var (name, value) in values) cmd.Parameters.AddWithValue(name, value ?? DBNull.Value);
        return cmd.ExecuteScalar();
    }

    public long Folder(long? parent, string name)
    {
        var id = (long)Scalar("INSERT INTO entries (root_id, parent_id, name, is_dir) VALUES ($r, $p, $n, 1) RETURNING id",
            ("$r", Root), ("$p", parent), ("$n", name))!;
        Scalar("INSERT INTO search (rowid, name) VALUES ($id, $n)", ("$id", id), ("$n", name));
        return id;
    }

    public long Unread(long parent, string name)
    {
        var dot = name.LastIndexOf('.');
        var id = (long)Scalar("INSERT INTO entries (root_id, parent_id, name, is_dir, ext) VALUES ($r, $p, $n, 0, $x) RETURNING id",
            ("$r", Root), ("$p", parent), ("$n", name), ("$x", dot < 0 ? null : name[dot..].ToLowerInvariant()))!;
        Scalar("INSERT INTO search (rowid, name) VALUES ($id, $n)", ("$id", id), ("$n", name));
        return id;
    }

    public long Track(long parent, string name, string? title = null, string? artist = null, string? album = null,
        string? albumArtist = null, string? genre = null, string? date = null, string? key = null, int rg = 0,
        string? readError = null)
    {
        var id = Unread(parent, name);
        var gains = Enumerable.Range(0, 4).Select(i => i < rg ? "-1.00 dB" : null).ToArray();
        Scalar("""
            INSERT INTO tracks (entry_id, title, artist, album, albumartist, genre, date, lyrics_key, read_error,
                rg_track_gain, rg_track_peak, rg_album_gain, rg_album_peak)
            VALUES ($id, $t, $a, $al, $aa, $g, $d, $k, $e, $g1, $g2, $g3, $g4)
            """, ("$id", id), ("$t", title), ("$a", artist), ("$al", album), ("$aa", albumArtist), ("$g", genre),
            ("$d", date), ("$k", key), ("$e", readError), ("$g1", gains[0]), ("$g2", gains[1]), ("$g3", gains[2]),
            ("$g4", gains[3]));
        Scalar("UPDATE search SET title = $t, artist = $a, albumartist = $aa, album = $al, genre = $g WHERE rowid = $id",
            ("$id", id), ("$t", title), ("$a", artist), ("$aa", albumArtist), ("$al", album), ("$g", genre));
        return id;
    }

    public void Dispose()
    {
        Conn.Dispose();
        temp.Dispose();
    }
}
