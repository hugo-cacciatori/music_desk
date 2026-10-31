using System.Text.Json;
using Microsoft.Data.Sqlite;
using Refrain.Core.Index;
using Refrain.Core.Options;

namespace Refrain.Core;

/// <summary>What the first start did with the Python app's files.</summary>
public sealed record MigrationResult(bool Migrated, int Roots = 0, int Lyrics = 0, bool SettingsMoved = false,
    string? Error = null, string? Kept = null)
{
    public static readonly MigrationResult Nothing = new(false);
}

/// <summary>The first start after the Python app (SPEC §36 "10."): its library folders, its LRCLIB results and the
/// settings the C# app uses move into the C# app's own files in the same data folder; the index itself is rebuilt by
/// the start's rescan. The Python files then go to the Recycle Bin together, and the preview's <c>next\</c> folder too.
/// Settings with no place in the C# app are dropped quietly.
/// Nothing is lost on the way: the new files are written under temporary names first, the Python files are moved
/// aside in one go (a Python app still running keeps its index open, so the move fails and nothing changes), and
/// only then do the new files take their names. Where Windows can't recycle, the moved-aside folder stays.</summary>
public static class Migration
{
    public const string Aside = "python-app";  // the Python files, moved aside before they go to the Recycle Bin
    const string Temporary = ".migrating";
    // playlist.json: the Python app's playlist builder, which the C# app doesn't have.
    static readonly string[] PythonFiles = ["library.db", "library.db-wal", "library.db-shm", "settings.json", "playlist.json"];
    static readonly string[] LyricsStatuses = ["found", "instrumental", "not_found"];

    /// <summary>The Python app's index: a schema version past this app's, with the Python app's own columns.</summary>
    public static bool IsPythonIndex(string database)
    {
        if (!File.Exists(database)) return false;
        try
        {
            using var conn = Open(database, SqliteOpenMode.ReadOnly);
            if ((long)conn.Scalar("PRAGMA user_version")! <= Schema.Version) return false;
            return (long)conn.Scalar("SELECT count(*) FROM pragma_table_info('tracks') WHERE name = 'tag_family'")! > 0;
        }
        catch (SqliteException e)
        {
            Log.Error($"{database} couldn't be read", e);
            return false;
        }
    }

    static SqliteConnection Open(string path, SqliteOpenMode mode)
    {
        var conn = new SqliteConnection(new SqliteConnectionStringBuilder { DataSource = path, Mode = mode, Pooling = false }.ToString());
        conn.Open();
        return conn;
    }

    /// <summary>MusicDesk's data folder (<c>%LOCALAPPDATA%\MusicDesk</c>), the app's name up to 0.11 (SPEC §36 "10.").</summary>
    public static string OldDataFolder =>
        Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "MusicDesk");

    /// <summary>An upgrade straight from MusicDesk (0.11 and earlier, before the rename): its data folder's items move
    /// into <paramref name="folder"/>, each one <paramref name="folder"/> doesn't have yet (an item it has is never
    /// overwritten: the menu may have made <c>menu\</c> first), so <see cref="Run"/> then migrates them as the Python
    /// app's. What is left (such duplicates, logs) goes to the Recycle Bin with the folder. A file in use (MusicDesk
    /// still running) moves nothing further and says so; the next start finishes. Null when all went well.</summary>
    public static string? MoveOldDataFolder(string oldFolder, string folder, Action<string>? recycle = null)
    {
        recycle ??= Recycle.Send;
        if (!Directory.Exists(oldFolder)) return null;
        Directory.CreateDirectory(folder);
        try
        {
            // The index first: if MusicDesk still holds it, nothing has moved yet.
            var items = Directory.GetFileSystemEntries(oldFolder)
                .OrderBy(p => Path.GetFileName(p).StartsWith("library.db", StringComparison.OrdinalIgnoreCase) ? 0 : 1);
            foreach (var item in items)
            {
                var target = Path.Combine(folder, Path.GetFileName(item));
                if (File.Exists(target) || Directory.Exists(target)) continue;
                if (Directory.Exists(item)) Directory.Move(item, target);
                else File.Move(item, target);
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            Log.Error($"Moving {oldFolder} into {folder} stopped", e);
            return "MusicDesk still has its files open. Close it, then use Refrain's right-click menu again; nothing was lost.";
        }
        try
        {
            recycle(oldFolder);
        }
        catch (Exception e) when (e is IOException or CannotRecycleException or UnauthorizedAccessException)
        {
            Log.Info($"{oldFolder} wasn't sent to the Recycle Bin and stays: {e.Message}");
        }
        Log.Info($"Moved MusicDesk's data from {oldFolder} into {folder}");
        return null;
    }

    /// <summary>Migrates <paramref name="folder"/>'s Python files, if it holds any. <paramref name="recycle"/> sends a
    /// folder to the Recycle Bin (tests pass their own; never the real bin there).</summary>
    public static MigrationResult Run(string folder, Action<string>? recycle = null)
    {
        recycle ??= Recycle.Send;
        var database = Path.Combine(folder, "library.db");
        if (!IsPythonIndex(database)) return MigrationResult.Nothing;
        var newDatabase = database + Temporary;
        var settingsFile = Path.Combine(folder, "settings.json");
        var newSettings = settingsFile + Temporary;
        var aside = Path.Combine(folder, Aside);
        int roots, lyrics;
        Settings? settings = null;
        try
        {
            foreach (var leftover in new[] { newDatabase, newSettings }) File.Delete(leftover);
            using (var old = Open(database, SqliteOpenMode.ReadOnly))
            using (var conn = Open(newDatabase, SqliteOpenMode.ReadWriteCreate))
            {
                conn.Execute("PRAGMA foreign_keys = ON");
                Schema.Migrate(conn);
                using var tx = conn.BeginTransaction();
                (roots, lyrics) = (CopyRoots(old, conn, tx), CopyLyrics(old, conn, tx));
                tx.Commit();
            }
            // A settings file already the C# app's stays as it is; no file gives the defaults.
            settings = File.Exists(settingsFile) ? FromPython(File.ReadAllBytes(settingsFile)) : new Settings();
            if (settings is not null)
            {
                settings.Save(newSettings);
                if (!File.Exists(newSettings)) throw new IOException($"{newSettings} couldn't be written");
            }
        }
        catch (Exception e) when (e is SqliteException or IOException or UnauthorizedAccessException or JsonException)
        {
            Log.Error("Reading the Python app's files failed; nothing was changed", e);
            Clean(newDatabase, newSettings);
            return new MigrationResult(false, Error: $"Refrain couldn't read the previous version's files ({e.Message}). Nothing was changed.");
        }

        // The Python files aside, all or none: a running Python app holds its index, and the move then fails.
        var moving = PythonFiles.Where(n => n != "settings.json" || settings is not null).ToList();
        var moved = new List<string>();
        void PutBack()
        {
            foreach (var name in Enumerable.Reverse(moved))
            {
                try
                {
                    File.Move(Path.Combine(aside, name), Path.Combine(folder, name));
                }
                catch (Exception back) when (back is IOException or UnauthorizedAccessException)
                {
                    Log.Error($"{name} couldn't be put back from {aside}", back);
                }
            }
            Clean(newDatabase, newSettings);
            try
            {
                Directory.Delete(aside);  // only if empty
            }
            catch (IOException)
            {
            }
        }
        try
        {
            if (Directory.Exists(aside)) aside = Numbered(aside);
            Directory.CreateDirectory(aside);
            foreach (var name in moving.Where(n => File.Exists(Path.Combine(folder, n))))
            {
                File.Move(Path.Combine(folder, name), Path.Combine(aside, name));
                moved.Add(name);
            }
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            Log.Error("Moving the Python app's files aside failed; they were put back", e);
            PutBack();
            return new MigrationResult(false, Error: "The previous version of Refrain still has its files open. Close it, then use Refrain's right-click menu again.");
        }
        try
        {
            File.Move(newDatabase, database);
            if (settings is not null) File.Move(newSettings, settingsFile);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            Log.Error("The migrated files couldn't take their names; the previous ones were put back", e);
            foreach (var placed in new[] { database, settingsFile }.Where(p => moved.Contains(Path.GetFileName(p)) && File.Exists(p)))
                Clean(placed);
            PutBack();
            return new MigrationResult(false, Error: $"Refrain couldn't put its new files in place ({e.Message}). Nothing was changed.");
        }

        string? kept = null;
        foreach (var gone in new[] { aside, Path.Combine(folder, "next") }.Where(Directory.Exists))
        {
            try
            {
                recycle(gone);
            }
            catch (Exception e) when (e is IOException or CannotRecycleException or UnauthorizedAccessException)
            {
                Log.Info($"{gone} wasn't sent to the Recycle Bin and stays: {e.Message}");
                kept ??= gone;
            }
        }
        Log.Info($"Migrated the Python app's files: {roots} library folders, {lyrics} LRCLIB results, settings {(moved.Contains("settings.json") ? "moved" : "kept or defaults")}");
        return new MigrationResult(true, roots, lyrics, moved.Contains("settings.json"), Kept: kept);
    }

    static void Clean(params string[] paths)
    {
        foreach (var path in paths)
        {
            try
            {
                File.Delete(path);
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                Log.Error($"{path} couldn't be removed", e);
            }
        }
    }

    static string Numbered(string path)
    {
        var n = 2;
        while (Directory.Exists($"{path} ({n})") || File.Exists($"{path} ({n})")) n++;
        return $"{path} ({n})";
    }

    static int CopyRoots(SqliteConnection old, SqliteConnection conn, SqliteTransaction tx)
    {
        using var cmd = old.Command("SELECT path, added_at, online FROM roots ORDER BY id");
        using var r = cmd.ExecuteReader();
        var count = 0;
        while (r.Read())
        {
            Roots.Restore(conn, tx, r.GetString(0), r.GetString(1), r.GetInt64(2) != 0);
            count++;
        }
        return count;
    }

    /// <summary>LRCLIB's results, as the Python app stored them (the same keys, statuses and dates).</summary>
    static int CopyLyrics(SqliteConnection old, SqliteConnection conn, SqliteTransaction tx)
    {
        using var cmd = old.Command($"""
            SELECT key, status, lrclib_id, plain, synced, checked FROM lyrics
            WHERE status IN ({string.Join(", ", LyricsStatuses.Select(s => $"'{s}'"))})
            """);
        using var r = cmd.ExecuteReader();
        var count = 0;
        while (r.Read())
        {
            conn.Execute("""
                INSERT INTO lyrics (key, status, lrclib_id, plain, synced, checked) VALUES ($k, $s, $i, $p, $y, $c)
                """, tx, ("$k", r.GetString(0)), ("$s", r.GetString(1)), ("$i", r.IsDBNull(2) ? null : r.GetInt64(2)),
                ("$p", r.NullableString(3)), ("$y", r.NullableString(4)), ("$c", r.NullableString(5)));
            count++;
        }
        return count;
    }

    /// <summary>The C# app's settings from the Python app's <c>settings.json</c>: what the C# app uses, each value only
    /// when it has the right type (as the Python app read it); everything else is dropped. Null when the file is already
    /// the C# app's.</summary>
    public static Settings? FromPython(byte[] json)
    {
        using var document = JsonDocument.Parse(json);
        var root = document.RootElement;
        if (root.ValueKind != JsonValueKind.Object) return new Settings();
        if (root.TryGetProperty("file_names", out _) || root.TryGetProperty("replay_gain", out _)) return null;
        JsonElement? Get(JsonElement from, string key) =>
            from.ValueKind == JsonValueKind.Object && from.TryGetProperty(key, out var v) ? v : null;
        static List<string>? Strings(JsonElement? value) =>
            value is { ValueKind: JsonValueKind.Array } v && v.EnumerateArray().All(e => e.ValueKind == JsonValueKind.String)
                ? v.EnumerateArray().Select(e => e.GetString()!).ToList()
                : null;
        static string? Text(JsonElement? value) => value is { ValueKind: JsonValueKind.String } v ? v.GetString() : null;
        static bool? Flag(JsonElement? value) => value?.ValueKind switch
        {
            JsonValueKind.True => true,
            JsonValueKind.False => false,
            _ => null,
        };
        static long? Whole(JsonElement? value) => value is { ValueKind: JsonValueKind.Number } v && v.TryGetInt64(out var n) ? n : null;

        var settings = new Settings();
        // File names: the shared masks; files from before they were shared kept them in the Rename options.
        var rename = Get(root, "rename_options") ?? default;
        var masks = Strings(Get(root, "name_masks")) ?? Strings(Get(rename, "masks"));
        var replacement = Text(Get(root, "name_replacement")) ?? Text(Get(rename, "replacement"));
        settings.Set(new FileNamesOptions(masks ?? FileNamesOptions.Default.Masks, replacement ?? FileNamesOptions.Default.Replacement));
        if (Get(root, "replaygain_options") is { ValueKind: JsonValueKind.Object } replayGain)
            settings.Set(ReplayGainOptions.From(replayGain));
        // Tidy: Rename's music types, Clean up's picture and video lists.
        var cleanup = Get(root, "cleanup_options") ?? default;
        settings.Set(new TidyOptions(
            Strings(Get(rename, "audio_extensions")) ?? TidyOptions.Default.MusicTypes,
            Strings(Get(cleanup, "picture_extensions")) ?? TidyOptions.Default.Pictures,
            Strings(Get(cleanup, "video_extensions")) ?? TidyOptions.Default.Videos,
            TidyOptions.Default.Others));
        settings.SaveCueBeside = Flag(Get(root, "save_cue_beside")) ?? settings.SaveCueBeside;
        settings.TrimSubfolder = Text(Get(root, "trim_subfolder")) ?? settings.TrimSubfolder;
        settings.TreeVisible = Flag(Get(root, "tree_visible")) ?? settings.TreeVisible;
        settings.TreeWidth = Whole(Get(root, "tree_width")) ?? settings.TreeWidth;
        settings.LyricsTextSize = Whole(Get(root, "lyrics_text_size")) ?? settings.LyricsTextSize;
        settings.SearchMode = Text(Get(root, "search_mode")) ?? settings.SearchMode;
        if (Text(Get(root, "last_folder")) is { Length: > 0 } last) settings.LastFolder = last;
        settings.ExpandedFolders = Strings(Get(root, "expanded_folders")) ?? settings.ExpandedFolders;
        return settings;
    }
}
