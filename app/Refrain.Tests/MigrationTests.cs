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
    /// <summary>Writes the Python app's index (two library folders, one offline, and LRCLIB results) and its settings
    /// into <paramref name="data"/>; returns what the Python app reads back of the roots and the lyrics.</summary>
    static List<string> MakePythonData(string data, string music, string gone) => JsonSerializer.Deserialize<List<string>>(PythonApp.Run("""
        import json, sys
        from pathlib import Path
        from refrain.core.index.store import DbWriter, upsert_lyrics
        from refrain.core.index.roots import add_root
        from refrain.settings import Settings
        args = json.load(sys.stdin)
        data = Path(args["data"])
        writer = DbWriter(data / "library.db").start()
        add_root(writer, args["music"])
        add_root(writer, args["gone"])
        def lyrics(conn):
            upsert_lyrics(conn, "artist\x1fsong", {"status": "found", "source": "lrclib", "lrclib_id": 7, "plain": "words\nmore",
                                                   "synced": "[00:01.00] words", "checked": "2026-10-01"})
            upsert_lyrics(conn, "artist\x1fsolo", {"status": "instrumental", "lrclib_id": 8, "checked": "2026-10-02"})
            upsert_lyrics(conn, "élan\x1fnone", {"status": "not_found", "checked": "2026-09-03"})
            conn.execute("INSERT INTO lyrics (key, status, error) VALUES ('x\x1fy', 'error', 'boom')")
            conn.execute("UPDATE roots SET online = 0 WHERE path = ?", (args["gone"],))
        writer.submit(lyrics).result()
        writer.stop()
        s = Settings.load(data / "settings.json")
        s.name_masks = ["{track:02d} {title}", "{title}"]
        s.name_replacement = "-"
        s.replaygain_options = {"target": -18.0, "safety_threshold": 0.98, "safety_margin": 0.5, "subfolder_keywords": ["lite"],
                                "track_data_keywords": ["lite"], "track_data_excluded_keywords": [], "log_file": "rg.log",
                                "comment": "LUFS {target}"}
        s.rename_options = {"recursive": False, "audio_extensions": [".flac", ".mp3"]}
        s.cleanup_options = {"junk": True, "picture_extensions": [".jpg"], "video_extensions": [".mkv"]}
        s.trim_subfolder = "short"
        s.save_cue_beside = True
        s.tree_visible = True
        s.tree_width = 300
        s.lyrics_text_size = 16
        s.search_mode = "lyrics"
        s.last_folder = args["music"]
        s.expanded_folders = [args["music"]]
        s.window_geometry = "AQID"
        s.playlist_visible = True
        s.save()
        import sqlite3
        conn = sqlite3.connect(data / "library.db")
        out = [f"root {p} {a} {o}" for p, a, o in conn.execute("SELECT path, added_at, online FROM roots ORDER BY path")]
        out += [f"lyrics {k} {s} {i} {p!r} {y!r} {c}" for k, s, i, p, y, c in conn.execute(
            "SELECT key, status, lrclib_id, plain, synced, checked FROM lyrics WHERE status != 'error' ORDER BY key")]
        conn.close()
        print(json.dumps(out, ensure_ascii=False))
        """, JsonSerializer.Serialize(new { data, music, gone })))!;

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
        var (data, music, gone, binFolder) = (temp.Sub("data"), temp.Sub("music"), temp.Sub("gone"), temp.Sub("bin"));
        var expected = MakePythonData(data, music, gone);
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
        Assert.Equal(music, settings.LastFolder);
        Assert.Equal([music], settings.ExpandedFolders);
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
        var (data, music, gone) = (temp.Sub("data"), temp.Sub("music"), temp.Sub("gone"));
        MakePythonData(data, music, gone);
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
