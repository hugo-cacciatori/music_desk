using Microsoft.Data.Sqlite;
using Refrain.Core.Index;

namespace Refrain.Core.Search;

/// <summary>The matches of a query: the first <see cref="LibrarySearch.Limit"/> ids in folder and name order, and
/// how many there are in all.</summary>
public sealed record SearchResult(List<long> Ids, int Total);

/// <summary>The two searches (SPEC §6, §33): File search's free text matches names, tags and the date; Lyrics
/// search's only LRCLIB's plain text.</summary>
public enum SearchMode { Files, Lyrics }

/// <summary>Library search (SPEC §6, §33): a case- and accent-insensitive substring, plus the field filters. Names are
/// found from the folder walk on, tags once read, lyrics once LRCLIB answered.</summary>
public static class LibrarySearch
{
    public const int Limit = 5000;
    const int FtsMin = 3;  // trigram needs 3 characters; shorter text is matched with LIKE
    const string Date = "date";  // tracks.date, not a column of the search table: LIKE, and only text with a digit
    static readonly string[] FileColumns = ["name", "title", "artist", "albumartist", "album", Date];
    static readonly string[] LyricsColumns = ["lrclib"];
    const int ExtractContext = 1;  // a card's extract: the matching line and one line on each side

    /// <summary>The LRCLIB column's states (Queries' LrclibState), in SQL.</summary>
    const string LrclibState = """
        CASE
            WHEN t.read_error IS NOT NULL THEN ''
            WHEN t.lyrics_key IS NULL THEN 'notags'
            WHEN l.status = 'found' THEN CASE WHEN coalesce(l.synced, '') != '' THEN 'synced' ELSE 'plain' END
            WHEN l.status = 'instrumental' THEN 'instrumental'
            WHEN l.status = 'not_found' THEN 'notfound'
            ELSE 'pending' END
        """;

    static readonly string AudioList = string.Join(", ", Formats.Audio.Select(e => $"'{e}'"));

    /// <summary>The results: folders with songs below them (SPEC §37; not a root's top entry, whose name is the root
    /// path) and audio files, read or not. A folder has songs when the subtree totals count some, or, before a scan has
    /// counted them, when it holds an audio file itself.</summary>
    static readonly string Results = "((e.is_dir = 1 AND e.parent_id IS NOT NULL AND (" +
        "EXISTS (SELECT 1 FROM folder_stats fs WHERE fs.entry_id = e.id AND fs.n_tracks > 0) OR " +
        $"EXISTS (SELECT 1 FROM entries c WHERE c.parent_id = e.id AND c.ext IN ({AudioList})))) " +
        $"OR e.ext IN ({AudioList}))";

    const string RgCount = "((t.rg_track_gain IS NOT NULL) + (t.rg_track_peak IS NOT NULL) + " +
                           "(t.rg_album_gain IS NOT NULL) + (t.rg_album_peak IS NOT NULL))";

    /// <summary>The matches, in folder and name order. <paramref name="errorKeys"/> are the keys whose last lookup
    /// failed this session (<c>lrclib:error</c>).</summary>
    public static SearchResult Find(SqliteConnection conn, SearchQuery query, int limit = Limit,
        SearchMode mode = SearchMode.Files, IReadOnlySet<string>? errorKeys = null)
    {
        var columns = mode == SearchMode.Files ? FileColumns : LyricsColumns;
        conn.CreateFunction("md_contains", (string? text, string needle) =>
            text is not null && Folding.Fold(text).Contains(needle, StringComparison.Ordinal), isDeterministic: true);
        var parameters = new List<(string, object?)>();
        string Param(object? value)
        {
            var name = "$p" + parameters.Count;
            parameters.Add((name, value));
            return name;
        }

        // Atoms are (columns, text); a group ORs its atoms; groups are ANDed.
        var groups = query.Text.Select(part => new List<(string[] Cols, string Text)> { (columns, part) }).ToList();
        foreach (var (name, cols) in SearchQuery.TextFields)
            if (query.Filters.TryGetValue(name, out var values))
                groups.Add(values.Select(v => (cols, v)).ToList());

        var match = new List<string>();
        var where = new List<string>();
        foreach (var group in groups)
        {
            // The date can't be in the MATCH: a group that may match it ("1997") ORs a MATCH subquery with a LIKE.
            var dated = group.Where(a => a.Cols.Contains(Date) && a.Text.Any(char.IsDigit)).ToList();
            var atoms = group.Select(a => (Cols: a.Cols.Where(c => c != Date).ToArray(), a.Text)).ToList();
            if (dated.Count == 0 && atoms.All(a => a.Text.Length >= FtsMin))
            {
                match.Add("(" + string.Join(" OR ", atoms.Select(a => Fts(a.Cols, a.Text))) + ")");
                continue;
            }
            var alternatives = new List<string>();
            foreach (var (cols, text) in atoms)
            {
                if (cols.Length == 0) continue;
                if (text.Length >= FtsMin)
                    alternatives.Add($"search.rowid IN (SELECT rowid FROM search WHERE search MATCH {Param(Fts(cols, text))})");
                else if (text.All(c => c < 0x80))
                    alternatives.AddRange(cols.Select(c => $"search.{c} LIKE {Param(Like(text))} ESCAPE '\\'"));
                else
                    alternatives.AddRange(cols.Select(c => $"md_contains(search.{c}, {Param(Folding.Fold(text))})"));
            }
            foreach (var (_, text) in dated)
                alternatives.Add($"t.date LIKE {Param(Like(text))} ESCAPE '\\'");
            where.Add("(" + (alternatives.Count > 0 ? string.Join(" OR ", alternatives) : "0") + ")");
        }
        if (query.Filters.TryGetValue("year", out var years))
            where.Add("(" + string.Join(" OR ", years.Select(y => $"t.date LIKE {Param(Escape(y) + "%")} ESCAPE '\\'")) + ")");
        if (query.Filters.TryGetValue("ext", out var exts))
            where.Add("(" + string.Join(" OR ", exts.Select(x => $"e.ext = {Param(x)}")) + ")");
        if (query.Filters.TryGetValue("rg", out var rg))
            where.Add("(" + string.Join(" OR ", rg.Select(v => v switch
            {
                "none" => $"{RgCount} = 0",
                "full" => $"{RgCount} = 4",
                _ => $"{RgCount} BETWEEN 1 AND 3",
            })) + ")");
        if (query.Filters.TryGetValue("lrclib", out var states))
        {
            var errors = $"t.lyrics_key IN (SELECT value FROM json_each({Param(Json(errorKeys))}))";
            where.Add("(" + string.Join(" OR ", states.Select(v => v switch
            {
                "error" => $"(({LrclibState}) = 'pending' AND {errors})",
                "pending" => $"(({LrclibState}) = 'pending' AND NOT {errors})",
                _ => $"({LrclibState}) = {Param(v)}",
            })) + ")");
        }
        if (query.Filters.Keys.Any(SearchQuery.TagFilters.Contains))
            where.Add("t.entry_id IS NOT NULL");  // rg:none must not take a folder's missing tags for "none"
        where.Add(Results);
        if (match.Count > 0) where.Insert(0, $"search MATCH {Param(string.Join(" AND ", match))}");

        var sql = "SELECT e.id, e.parent_id, e.name FROM search JOIN entries e ON e.id = search.rowid " +
                  "LEFT JOIN tracks t ON t.entry_id = e.id LEFT JOIN lyrics l ON l.key = t.lyrics_key WHERE " +
                  string.Join(" AND ", where);
        var rows = new List<(long Id, long Parent, string Name)>();
        using (var tx = conn.BeginTransaction(deferred: true))  // one snapshot for the matches and their folders
        {
            using (var cmd = conn.Command(sql, tx, parameters.ToArray()))
            using (var r = cmd.ExecuteReader())
                while (r.Read())
                    rows.Add((r.GetInt64(0), r.GetInt64(1), r.GetString(2)));
            var folders = new FolderPaths(conn, tx);
            var keys = rows.ToDictionary(row => row.Id, row => folders.Key(row.Parent));
            rows.Sort((a, b) =>
            {
                var byFolder = CompareKeys(keys[a.Id], keys[b.Id]);
                return byFolder != 0 ? byFolder : NaturalOrder.Instance.Compare(a.Name, b.Name);
            });
        }
        return new SearchResult(rows.Take(limit).Select(r => r.Id).ToList(), rows.Count);
    }

    static int CompareKeys(string[] a, string[] b)
    {
        for (var i = 0; i < Math.Min(a.Length, b.Length); i++)
        {
            var c = NaturalOrder.Instance.Compare(a[i], b[i]);
            if (c != 0) return c;
        }
        return a.Length.CompareTo(b.Length);
    }

    static string Json(IReadOnlySet<string>? keys) =>
        System.Text.Json.JsonSerializer.Serialize((keys ?? new HashSet<string>()).ToArray(), SearchJson.Default.StringArray);

    static string Fts(string[] cols, string text) => "{" + string.Join(" ", cols) + "} : \"" + text.Replace("\"", "\"\"") + "\"";
    static string Escape(string text) => text.Replace("\\", "\\\\").Replace("%", "\\%").Replace("_", "\\_");
    static string Like(string text) => "%" + Escape(text) + "%";

    /// <summary>The shown results in <paramref name="ids"/> order, as file list rows with their folder and what to
    /// highlight. File search highlights the free text in Title and Artist; Lyrics search, in its card's extract only.
    /// The <c>artist:</c> values are highlighted in Artist. Ids gone since are dropped.</summary>
    public static List<FileRow> Rows(SqliteConnection conn, IReadOnlyList<long> ids, SearchQuery query,
        SearchMode mode = SearchMode.Files, CancellationToken cancel = default)
    {
        if (ids.Count == 0) return [];
        using var tx = conn.BeginTransaction(deferred: true);
        var rows = new Dictionary<long, (FileRow Row, long Parent)>();
        using (var cmd = conn.Command(Queries.ListingSelect(", e.parent_id") +
                                      " WHERE e.id IN (SELECT value FROM json_each($ids))", tx,
                   ("$ids", "[" + string.Join(",", ids) + "]")))
        using (var r = cmd.ExecuteReader())
            while (r.Read())
            {
                var row = Queries.ReadRow(r);
                rows[row.Id] = (row, r.GetInt64(Queries.ListingColumnCount));
            }
        var lyrics = new Dictionary<long, string?>();
        if (mode == SearchMode.Lyrics)
        {
            using var cmd = conn.Command("SELECT rowid, lrclib FROM search WHERE rowid IN (SELECT value FROM json_each($ids))",
                tx, ("$ids", "[" + string.Join(",", ids) + "]"));
            using var r = cmd.ExecuteReader();
            while (r.Read()) lyrics[r.GetInt64(0)] = r.NullableString(1);
        }
        var folders = new FolderPaths(conn, tx);
        var free = query.Needles();
        var titleNeedles = mode == SearchMode.Files ? free : [];
        var artist = mode == SearchMode.Files
            ? query.Needles("artist")
            : query.Filters.GetValueOrDefault("artist", []).Select(Folding.Fold).Where(n => n.Length > 0).ToList();
        var results = new List<FileRow>(ids.Count);
        foreach (var id in ids)
        {
            cancel.ThrowIfCancellationRequested();
            if (!rows.TryGetValue(id, out var found)) continue;
            var row = found.Row;
            if (row.IsDir) row = row with { TrackCount = null, Duration = null, Mtime = null };  // its name and parent
            var extract = mode == SearchMode.Lyrics && free.Count > 0 ? Extract(lyrics.GetValueOrDefault(id), free) : "";
            results.Add(row with
            {
                FolderId = found.Parent,
                Folder = folders.Path(found.Parent),
                TitleSpans = Folding.Spans(row.Title, titleNeedles),
                ArtistSpans = Folding.Spans(row.Artist, artist),
                Extract = extract,
                ExtractSpans = Folding.Spans(extract, free),
            });
        }
        return results;
    }

    /// <summary>A Lyrics search card's extract (SPEC §6 "Direction task 6"): the first line of the lyrics holding a
    /// needle, with up to one line before and after it, timestamps stripped, joined by line breaks.</summary>
    public static string Extract(string? text, IReadOnlyList<string> needles)
    {
        if (string.IsNullOrEmpty(text)) return "";
        var lines = Lyrics.Lrclib.StripTimestamps(text).Split('\n').Select(l => l.Trim()).Where(l => l.Length > 0).ToList();
        var at = lines.FindIndex(line => needles.Any(n => Folding.Fold(line).Contains(n, StringComparison.Ordinal)));
        if (at < 0) return "";
        var from = Math.Max(0, at - ExtractContext);
        return string.Join("\n", lines[from..Math.Min(lines.Count, at + ExtractContext + 1)]);
    }

    /// <summary>Folder paths and sort keys, built once per folder from its parent's.</summary>
    sealed class FolderPaths(SqliteConnection conn, SqliteTransaction tx)
    {
        readonly Dictionary<long, string[]> keys = [];

        public string[] Key(long folderId)
        {
            if (keys.TryGetValue(folderId, out var key)) return key;
            var chain = new Stack<(long Id, string Name)>();
            long? current = folderId;
            while (current is { } id && !keys.ContainsKey(id))
            {
                using var cmd = conn.Command("SELECT parent_id, name FROM entries WHERE id = $id", tx, ("$id", id));
                using var r = cmd.ExecuteReader();
                if (!r.Read()) break;
                chain.Push((id, r.GetString(1)));
                current = r.IsDBNull(0) ? null : r.GetInt64(0);
            }
            var parent = current is { } p && keys.TryGetValue(p, out var known) ? known : [];
            while (chain.Count > 0)
            {
                var (id, name) = chain.Pop();
                parent = [.. parent, name];
                keys[id] = parent;
            }
            return keys.TryGetValue(folderId, out key) ? key : keys[folderId] = [];
        }

        public string Path(long folderId) => System.IO.Path.Combine(Key(folderId));
    }
}

[System.Text.Json.Serialization.JsonSerializable(typeof(string[]))]
internal sealed partial class SearchJson : System.Text.Json.Serialization.JsonSerializerContext;
