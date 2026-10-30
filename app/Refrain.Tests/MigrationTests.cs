using System.Text;
using System.Text.Json;
using Microsoft.Data.Sqlite;
using Refrain.Core;
using Refrain.Core.Index;
using Xunit;

namespace Refrain.Tests;

/// <summary>The first start after the Python app (SPEC §36 "10." step a): an index and a settings file written by the
/// Python app's own code are migrated, and the Python files leave for a stand-in Recycle Bin (never the real one).</summary>
public sealed class MigrationTests
{
    /// <summary>The Python app's data folder, written by its own code once (2026-10-30) and kept as a fixture: two library
    /// folders (D:\Music, and E:\Gone offline), LRCLIB results (one an error the migration leaves out) and its settings.
    /// Copied into <paramref name="data"/>; returns the roots and lyrics as the Python app read them back.</summary>
    static List<string> MakePythonData(string data)
    {
        foreach (var file in Directory.GetFiles(Path.Combine(Setup.Fixtures, "migration")))
            File.Copy(file, Path.Combine(data, Path.GetFileName(file)));
        return
        [
            @"root D:\Music 2026-01-02T03:04:05 1",
            @"root E:\Gone 2026-01-03T03:04:05 0",
            "lyrics artist\u001fsolo instrumental 8 None None 2026-10-02",
            "lyrics artist\u001fsong found 7 'words\\nmore' '[00:01.00] words' 2026-10-01",
            "lyrics élan\u001fnone not_found None None None 2026-09-03",
        ];
    }

    static string Py(string? text) => text is null ? "None" : "'" + text.Replace("\n", "\\n") + "'";

    static List<string> Read(string database)
    {
        using var conn = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = database, Mode = SqliteOpenMode.ReadOnly, Pooling = false }.ToString());
        conn.Open();
        var lines = new List<string>();
        using (var cmd = conn.Command("SELECT path, added_at, online FROM roots ORDER BY path"))
        using (var r = cmd.ExecuteReader())
            while (r.Read()) lines.Add($"root {r.GetString(0)} {r.GetString(1)} {r.GetInt64(2)}");
        using (var cmd = conn.Command("SELECT key, status, lrclib_id, plain, synced, checked FROM lyrics ORDER BY key"))
        using (var r = cmd.ExecuteReader())
            while (r.Read())
                lines.Add($"lyrics {r.GetString(0)} {r.GetString(1)} {(r.IsDBNull(2) ? "None" : r.GetInt64(2))} {Py(r.NullableString(3))} {Py(r.NullableString(4))} {r.GetString(5)}");
        // Every root has its top entry, as a root added in the app has.
        Assert.Equal(0L, (long)conn.Scalar("SELECT count(*) FROM roots r WHERE NOT EXISTS (SELECT 1 FROM entries e WHERE e.root_id = r.id AND e.parent_id IS NULL)")!);
        Assert.Equal((long)Schema.Version, (long)conn.Scalar("PRAGMA user_version")!);
        return lines;
    }

    sealed class Bin(string folder)
    {
        public List<string> Sent { get; } = [];

        public void Send(string path)
        {
            Sent.Add(Path.GetFileName(path));
            Directory.Move(path, Path.Combine(folder, Path.GetFileName(path) + "-" + Sent.Count));
        }
    }

    [Fact]
    public void The_python_apps_folders_lyrics_and_settings_move_over()
    {
        using var temp = new TempFolder();
        var (data, binFolder) = (temp.Sub("data"), temp.Sub("bin"));
        var expected = MakePythonData(data);
        Directory.CreateDirectory(Path.Combine(data, "next", "logs"));
        Directory.CreateDirectory(Path.Combine(data, "logs"));
        Assert.True(Migration.IsPythonIndex(Path.Combine(data, "library.db")));

        var bin = new Bin(binFolder);
        var result = Migration.Run(data, bin.Send);

        Assert.Equal(new MigrationResult(true, 2, 3, true), result);
        Assert.Equal(expected, Read(Path.Combine(data, "library.db")));
        Assert.Equal([Migration.Aside, "next"], bin.Sent);
        var recycled = Directory.GetFiles(Path.Combine(binFolder, Migration.Aside + "-1")).Select(f => Path.GetFileName(f)!).ToList();
        Assert.Contains("library.db", recycled);
        Assert.Contains("settings.json", recycled);
        Assert.Equal(["library.db", "logs", "settings.json"], Directory.GetFileSystemEntries(data).Select(f => Path.GetFileName(f)!).Order(StringComparer.Ordinal));

        var settings = Settings.Load(Path.Combine(data, "settings.json"));
        Assert.Equal(["{track:02d} {title}", "{title}"], settings.GetFileNames().Masks);
        Assert.Equal("-", settings.GetFileNames().Replacement);
        Assert.Equal(-18, settings.GetReplayGain().Target);
        Assert.Equal("rg.log", settings.GetReplayGain().LogFile);
        Assert.Equal([".flac", ".mp3"], settings.GetTidy().MusicTypes);
        Assert.Equal([".jpg"], settings.GetTidy().Pictures);
        Assert.Equal([".mkv"], settings.GetTidy().Videos);
        Assert.Equal(("short", true, true, 300.0, 16.0, "lyrics"),
            (settings.TrimSubfolder, settings.SaveCueBeside, settings.TreeVisible, settings.TreeWidth, settings.LyricsTextSize, settings.SearchMode));
        Assert.Equal(@"D:\Music", settings.LastFolder);
        Assert.Equal([@"D:\Music"], settings.ExpandedFolders);
        var text = File.ReadAllText(Path.Combine(data, "settings.json"));
        Assert.DoesNotContain("window_geometry", text);  // what the C# app has no use for is dropped
        Assert.DoesNotContain("playlist", text);

        using (var db = new Database(Path.Combine(data, "library.db")))  // the app opens it as its own
        using (var conn = db.Read())
            Assert.Equal(2, Roots.All(conn).Count);
        Assert.Equal(MigrationResult.Nothing, Migration.Run(data, bin.Send));  // once only
    }

    [Fact]
    public void An_open_index_or_no_recycle_bin_loses_nothing()
    {
        using var temp = new TempFolder();
        var data = temp.Sub("data");
        MakePythonData(data);
        // SQLite adds its empty -wal and -shm side files when it reads a WAL index; the files themselves must not change.
        static Dictionary<string, string> Snapshot(string folder) => Directory.GetFiles(folder).Where(f => !f.EndsWith("-wal") && !f.EndsWith("-shm"))
            .ToDictionary(f => Path.GetFileName(f)!, f => Convert.ToHexString(System.Security.Cryptography.SHA1.HashData(File.ReadAllBytes(f))));
        var before = Snapshot(data);

        // The Python app still running: its index is open without delete sharing, as SQLite opens it.
        MigrationResult open;
        using (new FileStream(Path.Combine(data, "library.db"), FileMode.Open, FileAccess.Read, FileShare.ReadWrite))
            open = Migration.Run(data, _ => throw new InvalidOperationException("nothing is recycled"));
        Assert.False(open.Migrated);
        Assert.Equal("The previous version of Refrain still has its files open. Close it, then start Refrain again.", open.Error);
        Assert.Equal(before, Snapshot(data));
        Assert.Empty(Directory.GetDirectories(data));

        // No Recycle Bin: the migration goes through and the Python files stay aside, never deleted.
        var kept = Migration.Run(data, _ => throw new CannotRecycleException("no Recycle Bin"));
        Assert.True(kept.Migrated);
        Assert.Equal(Path.Combine(data, Migration.Aside), kept.Kept);
        Assert.Equal(before["settings.json"], Snapshot(Path.Combine(data, Migration.Aside))["settings.json"]);
        Assert.False(Migration.IsPythonIndex(Path.Combine(data, "library.db")));
    }

    [Fact]
    public void Settings_are_read_as_the_python_app_read_them()
    {
        // Masks from before they were shared, mistyped values left at their defaults, an empty file.
        var old = Migration.FromPython(Encoding.UTF8.GetBytes("""
            {"rename_options": {"masks": ["{title}"], "replacement": "_", "audio_extensions": "flac"},
             "tree_width": "wide", "save_cue_beside": 1, "trim_subfolder": null, "replaygain_options": {"target": "loud"}}
            """))!;
        Assert.Equal(["{title}"], old.GetFileNames().Masks);
        Assert.Equal("_", old.GetFileNames().Replacement);
        Assert.Equal(Core.Options.TidyOptions.Default.MusicTypes, old.GetTidy().MusicTypes);
        Assert.Equal((240.0, false, "lite"), (old.TreeWidth, old.SaveCueBeside, old.TrimSubfolder));
        Assert.Equal(-15, old.GetReplayGain().Target);
        Assert.Equal(Core.Options.FileNamesOptions.Default.Masks, Migration.FromPython("{}"u8.ToArray())!.GetFileNames().Masks);
        Assert.Null(Migration.FromPython("""{"file_names": {"masks": []}}"""u8.ToArray()));  // already the C# app's
    }
}
