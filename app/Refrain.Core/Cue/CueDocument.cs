using System.Text;
using System.Text.RegularExpressions;

namespace Refrain.Core.Cue;

/// <summary>Characters the sheet's encoding can't hold: Save asks to write UTF-8 with a BOM instead (SPEC §22).</summary>
public sealed class CueEncodingException(string encoding, IReadOnlyList<string> characters)
    : Exception($"{encoding} can't hold {string.Join(" ", characters)}")
{
    public string Encoding { get; } = encoding;
    public IReadOnlyList<string> Characters { get; } = characters;
}

/// <summary>A line of the sheet: its text, its line ending, and the bytes it was read from (null once edited), written
/// back as they were.</summary>
public sealed class CueLine(string text, string end = "", byte[]? raw = null)
{
    string text = text;
    public string Text
    {
        get => text;
        set
        {
            if (value == text) return;
            text = value;
            Raw = null;
        }
    }
    public string End { get; set; } = end;
    public byte[]? Raw { get; private set; } = raw;

    /// <summary>Written from its text from now on.</summary>
    public void Forget() => Raw = null;
    public string Tag => CueDocument.TagOf(Text);
    public string Indent => Text[..(Text.Length - Text.TrimStart().Length)];
}

/// <summary>One audio track as the editor's table shows it: text values, as typed or read.</summary>
public sealed record TrackView(int Row, int? Number, IReadOnlyDictionary<string, string> Values, string Other);

/// <summary>A bad value, for the editor to mark: the track's row (null for the album or the sheet) and its column.</summary>
public sealed record CueProblem(int? Row, string Column, string Message, bool BlocksSave = true);

/// <summary>The editable cue sheet (SPEC §22 "Saving the sheet"), ported from the Python app's <c>core/cue_edit.py</c>.
/// It keeps every line in order with its own line ending; an edit rewrites, adds or removes only the lines it concerns,
/// and every other line is written back exactly as it was read, so a sheet saved unedited is byte-identical. The lines
/// are the only state: values are parsed from them each time they're asked for.</summary>
public sealed partial class CueDocument
{
    public static readonly byte[] Utf8Bom = [0xEF, 0xBB, 0xBF];

    /// <summary>The encoding picker (SPEC §22): (code, label); null is Auto: a BOM, then UTF-8, then Windows-1252.</summary>
    public static readonly IReadOnlyList<(string? Code, string Label)> Encodings =
    [
        (null, "Auto"), ("utf-8", "UTF-8"), ("cp1252", "Windows-1252 (Western)"), ("cp1251", "Windows-1251 (Cyrillic)"),
        ("shift_jis", "Shift-JIS (Japanese)"), ("gbk", "GBK (Chinese, simplified)"), ("big5", "Big5 (Chinese, traditional)"),
        ("euc_kr", "EUC-KR (Korean)"),
    ];

    // field → (tag, quoted). A tag is a line's keyword, with REM's second word and INDEX's number.
    public static readonly IReadOnlyDictionary<string, (string Tag, bool Quoted)> AlbumFields = new Dictionary<string, (string, bool)>
    {
        ["performer"] = ("PERFORMER", true), ["title"] = ("TITLE", true), ["genre"] = ("REM GENRE", true), ["date"] = ("REM DATE", false),
        ["disc"] = ("REM DISCNUMBER", false), ["disc_total"] = ("REM TOTALDISCS", false), ["catalog"] = ("CATALOG", false),
        ["songwriter"] = ("SONGWRITER", true),
    };

    public static readonly IReadOnlyDictionary<string, (string Tag, bool Quoted)> TrackFields = new Dictionary<string, (string, bool)>
    {
        ["title"] = ("TITLE", true), ["performer"] = ("PERFORMER", true), ["songwriter"] = ("SONGWRITER", true), ["isrc"] = ("ISRC", false),
        ["flags"] = ("FLAGS", false), ["gap"] = ("INDEX 00", false), ["start"] = ("INDEX 01", false),
    };

    static readonly List<string> TrackOrder = [.. TrackFields.Values.Select(f => f.Tag)];  // as EAC writes them
    static readonly HashSet<string> AlbumTags = [.. AlbumFields.Values.Select(f => f.Tag)];
    static readonly HashSet<string> TrackTags = [.. TrackOrder];

    public List<CueLine> Lines { get; }
    public Encoding Codec { get; private set; }
    public string EncodingName { get; private set; }
    public byte[] Bom { get; private set; }
    public bool FellBack { get; private set; }
    public bool NotCp1252 { get; private set; }
    public string? Path { get; set; }
    public byte[]? Saved { get; set; }  // the bytes on disk, to tell unsaved edits

    CueDocument(List<CueLine> lines, Encoding codec, string name, byte[] bom, bool fellBack = false, bool notCp1252 = false)
    {
        Lines = lines;
        Codec = codec;
        EncodingName = name;
        Bom = bom;
        FellBack = fellBack;
        NotCp1252 = notCp1252;
    }

    static Encoding Strict(Encoding encoding) => Encoding.GetEncoding(encoding.CodePage, EncoderFallback.ExceptionFallback, DecoderFallback.ReplacementFallback);

    /// <summary>The .NET encoding for a picker code.</summary>
    public static Encoding EncodingOf(string code) => Strict(code switch
    {
        "utf-8" => new UTF8Encoding(false),
        "cp1252" => Encoding.GetEncoding(1252),
        "cp1251" => Encoding.GetEncoding(1251),
        "shift_jis" => Encoding.GetEncoding(932),
        "gbk" => Encoding.GetEncoding(936),
        "big5" => Encoding.GetEncoding(950),
        "euc_kr" => Encoding.GetEncoding(51949),
        _ => throw new ArgumentException($"Unknown encoding {code}"),
    });

    // -- reading --

    public static string TagOf(string text)
    {
        var words = text.Split((char[]?)null, 3, StringSplitOptions.RemoveEmptyEntries);
        if (words.Length == 0) return "";
        var keyword = words[0].ToUpperInvariant();
        if (keyword == "REM" && words.Length > 1) return "REM " + words[1].ToUpperInvariant();
        if (keyword == "INDEX" && words.Length > 1 && words[1].All(char.IsAsciiDigit) && words[1].Length > 0)
            return $"INDEX {int.Parse(words[1]):00}";
        return keyword;
    }

    /// <summary>The value after a line's tag: between its first and last quotes (a quote inside is then visible to
    /// <see cref="Validate"/>), or the rest of the line.</summary>
    static string LineValue(string text, string tag)
    {
        var count = tag.Split(' ').Length;
        var words = text.Split((char[]?)null, count + 1, StringSplitOptions.RemoveEmptyEntries);
        if (words.Length <= count) return "";
        var rest = words[^1].Trim();
        if (rest.Length > 1 && rest[0] == '"' && rest[^1] == '"') return rest[1..^1];
        if (!rest.StartsWith('"')) return rest;
        var end = rest.IndexOf('"', 1);
        return end > 0 ? rest[1..end] : rest[1..];
    }

    /// <summary>CD frames → "mm:ss:ff", two digits each (minutes may have more).</summary>
    public static string FormatTime(long frames)
    {
        var (seconds, ff) = (frames / CueSheet.FramesPerSecond, frames % CueSheet.FramesPerSecond);
        return $"{seconds / 60:00}:{seconds % 60:00}:{ff:00}";
    }

    sealed record Region(int Start, int End, bool Audio);

    /// <summary>The file's own line ending, for new lines: its most common one (CRLF for a file without any).</summary>
    public string Newline
    {
        get
        {
            var ends = Lines.Where(l => l.End.Length > 0).Select(l => l.End).ToList();
            return ends.Count == 0 ? "\r\n" : new[] { "\r\n", "\n", "\r" }.MaxBy(e => ends.Count(x => x == e))!;
        }
    }

    public string Text() => string.Concat(Lines.Select(l => l.Text + l.End));

    (int HeaderEnd, List<int> Files, List<Region> Tracks) Structure()
    {
        var headerEnd = Lines.Count;
        var files = new List<int>();
        var tracks = new List<Region>();
        for (var i = 0; i < Lines.Count; i++)
        {
            var tag = Lines[i].Tag;
            if (tag is not ("FILE" or "TRACK")) continue;
            headerEnd = Math.Min(headerEnd, i);
            if (tracks.Count > 0 && tracks[^1].End == Lines.Count) tracks[^1] = tracks[^1] with { End = i };
            if (tag == "FILE") files.Add(i);
            else
            {
                var words = Lines[i].Text.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
                tracks.Add(new Region(i, Lines.Count, words.Length > 2 && words[2].ToUpperInvariant() == "AUDIO"));
            }
        }
        return (headerEnd, files, tracks);
    }

    List<Region> AudioRegions() => Structure().Tracks.Where(r => r.Audio).ToList();

    /// <summary>The first FILE line's name.</summary>
    public string File
    {
        get
        {
            var files = Structure().Files;
            if (files.Count == 0) return "";
            var rest = Lines[files[0]].Text.Trim()[4..].Trim();
            if (rest.StartsWith('"'))
            {
                var end = rest.IndexOf('"', 1);
                return end > 0 ? rest[1..end] : rest[1..];
            }
            var words = rest.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            return rest.Contains(' ') ? rest[..rest.LastIndexOf(words[^1], StringComparison.Ordinal)].TrimEnd() : rest;
        }
    }

    public int FileCount => Structure().Files.Count;

    /// <summary>The album fields as text, and "other": the album lines with no field of their own.</summary>
    public Dictionary<string, string> Album()
    {
        var headerEnd = Structure().HeaderEnd;
        var values = AlbumFields.ToDictionary(kv => kv.Key, kv => ValueIn(0, headerEnd, kv.Value.Tag));
        values["other"] = string.Join("\n", Lines.Take(headerEnd).Where(l => !AlbumTags.Contains(l.Tag)).Select(l => l.Text));
        return values;
    }

    public List<TrackView> Tracks()
    {
        var views = new List<TrackView>();
        var regions = AudioRegions();
        for (var row = 0; row < regions.Count; row++)
        {
            var region = regions[row];
            var words = Lines[region.Start].Text.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            int? number = words.Length > 1 && words[1].Length > 0 && words[1].All(char.IsAsciiDigit) ? int.Parse(words[1]) : null;
            var values = TrackFields.ToDictionary(kv => kv.Key, kv => ValueIn(region.Start + 1, region.End, kv.Value.Tag));
            var other = string.Join("\n", Lines.Skip(region.Start + 1).Take(region.End - region.Start - 1)
                .Where(l => !TrackTags.Contains(l.Tag)).Select(l => l.Text));
            views.Add(new TrackView(row, number, values, other));
        }
        return views;
    }

    string ValueIn(int start, int end, string tag)
    {
        for (var i = start; i < end; i++)
            if (Lines[i].Tag == tag) return LineValue(Lines[i].Text, tag);
        return "";
    }

    /// <summary>The parsed sheet, for planning. Throws CueException (<see cref="Validate"/> says where).</summary>
    public CueSheet Sheet()
    {
        var sheet = CueSheet.Parse(Text(), EncodingName, Path);
        sheet.FellBack = FellBack;
        sheet.NotCp1252 = NotCp1252;
        return sheet;
    }

    public bool Modified
    {
        get
        {
            try
            {
                return Saved is null || !Format().AsSpan().SequenceEqual(Saved);
            }
            catch (CueEncodingException)
            {
                return true;
            }
        }
    }

    // -- edits --

    /// <summary>The first FILE line's name; its type word (WAVE, AIFF) stays.</summary>
    public void SetFile(string name)
    {
        var files = Structure().Files;
        if (files.Count == 0)
        {
            Insert(Structure().HeaderEnd, $"FILE \"{name}\" WAVE");
            return;
        }
        var line = Lines[files[0]];
        var rest = line.Text.Trim()[4..].Trim();
        var kind = new[] { "WAVE", "AIFF", "BINARY", "MP3", "MOTOROLA" }.Any(k => rest.EndsWith(k, StringComparison.Ordinal))
            ? rest.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries)[^1] : "WAVE";
        var text = $"{line.Indent}FILE \"{name}\" {kind}";
        if (text != line.Text && name != File) line.Text = text;
    }

    public void SetAlbum(string name, string value)
    {
        var (tag, quoted) = AlbumFields[name];
        var headerEnd = Structure().HeaderEnd;
        int position;
        if (tag.StartsWith("REM ", StringComparison.Ordinal))
        {
            var rems = Enumerable.Range(0, headerEnd).Where(i => Lines[i].Tag.StartsWith("REM ", StringComparison.Ordinal)).ToList();
            position = rems.Count > 0 ? rems[^1] + 1 : 0;
        }
        else position = headerEnd;
        Set(0, headerEnd, tag, quoted, value.Trim(), position, "");
    }

    public void SetTrack(int row, string name, string value)
    {
        var (tag, quoted) = TrackFields[name];
        var region = AudioRegions()[row];
        value = value.Trim();
        if (name is "gap" or "start" && value.Length > 0)
        {
            try
            {
                value = FormatTime(CueSheet.FramesFromTime(value));
            }
            catch (CueException)
            {
                // kept as typed: Validate marks it
            }
        }
        var order = TrackOrder.IndexOf(tag);
        var position = region.Start + 1;  // after the last field EAC writes before this one
        for (var i = region.Start + 1; i < region.End; i++)
            if (TrackTags.Contains(Lines[i].Tag) && TrackOrder.IndexOf(Lines[i].Tag) < order) position = i + 1;
        Set(region.Start + 1, region.End, tag, quoted, value, position, ContentIndent(region));
    }

    /// <summary>Rewrites the first <paramref name="tag"/> line of [start, end) (dropping repeats), adds one, or removes
    /// them when the value is empty.</summary>
    void Set(int start, int end, string tag, bool quoted, string value, int position, string indent)
    {
        var found = Enumerable.Range(start, end - start).Where(i => Lines[i].Tag == tag).ToList();
        if (found.Count == 1 && LineValue(Lines[found[0]].Text, tag) == value) return;  // unchanged: the line keeps its bytes
        for (var i = found.Count - 1; i >= 1; i--) Lines.RemoveAt(found[i]);
        if (value.Length == 0)
        {
            if (found.Count > 0) Lines.RemoveAt(found[0]);
            return;
        }
        var text = quoted ? $"{tag} \"{value}\"" : $"{tag} {value}";
        if (found.Count > 0) Lines[found[0]].Text = Lines[found[0]].Indent + text;
        else Insert(position, indent + text);
    }

    public void SetAlbumOther(string text)
    {
        var headerEnd = Structure().HeaderEnd;
        var others = Enumerable.Range(0, headerEnd).Where(i => !AlbumTags.Contains(Lines[i].Tag)).ToList();
        ReplaceBlock(others, text, others.Count > 0 ? others[0] : 0, "");
    }

    public void SetTrackOther(int row, string text)
    {
        var region = AudioRegions()[row];
        var others = Enumerable.Range(region.Start + 1, region.End - region.Start - 1).Where(i => !TrackTags.Contains(Lines[i].Tag)).ToList();
        ReplaceBlock(others, text, others.Count > 0 ? others[0] : region.End, ContentIndent(region));
    }

    void ReplaceBlock(List<int> indexes, string text, int position, string indent)
    {
        var lines = text.Trim().Length > 0 ? text.Split('\n').Select(l => l.TrimEnd('\r')).ToList() : [];
        if (lines.SequenceEqual(indexes.Select(i => Lines[i].Text))) return;
        for (var k = indexes.Count - 1; k >= 0; k--)
        {
            Lines.RemoveAt(indexes[k]);
            if (indexes[k] < position) position--;
        }
        for (var offset = 0; offset < lines.Count; offset++)
        {
            var line = lines[offset];
            Insert(position + offset, line.Length > 0 && char.IsWhiteSpace(line[0]) || indent.Length == 0 ? line : indent + line.TrimStart());
        }
    }

    /// <summary>Inserts a track after row <paramref name="after"/>, starting halfway through it (to the next start, or
    /// the end of the audio for the last track). Returns the new row. Throws CueException when there's no room.</summary>
    public int AddTrack(int? after, long? audioFrames = null)
    {
        var regions = AudioRegions();
        int position, row;
        long start;
        string indent;
        if (after is null || regions.Count == 0)
        {
            var all = Structure().Tracks;
            position = all.Count > 0 ? all[^1].End : Lines.Count;
            (start, indent, row) = (0, "  ", regions.Count);
        }
        else
        {
            var views = Tracks();
            long begin, end;
            try
            {
                begin = CueSheet.FramesFromTime(views[after.Value].Values["start"]);
                if (after.Value + 1 < views.Count) end = CueSheet.FramesFromTime(views[after.Value + 1].Values["start"]);
                else end = audioFrames ?? throw new CueException("the end of the audio isn't known");
            }
            catch (CueException e)
            {
                throw new CueException($"A track can't be added after track {after.Value + 1}: {e.Message}");
            }
            if (end - begin < 2) throw new CueException($"Track {after.Value + 1} is too short to add a track inside it.");
            start = (begin + end) / 2;
            position = regions[after.Value].End;
            indent = Lines[regions[after.Value].Start].Indent;
            row = after.Value + 1;
        }
        var content = indent.Length > 0 ? indent + "  " : "    ";
        Insert(position, $"{indent}TRACK 00 AUDIO");
        Insert(position + 1, $"{content}INDEX 01 {FormatTime(start)}");
        Renumber();
        return row;
    }

    /// <summary>Removes tracks: their audio joins the track before, or, for track 1, the next one kept, which takes its
    /// start.</summary>
    public void DeleteTracks(IEnumerable<int> rows)
    {
        var sorted = rows.Distinct().Order().ToList();
        var views = Tracks();
        if (sorted.Count > 0 && sorted[0] == 0)
        {
            var keep = views.FirstOrDefault(v => !sorted.Contains(v.Row));
            if (keep is not null && views[0].Values["start"].Length > 0)
            {
                SetTrack(keep.Row, "start", views[0].Values["start"]);
                SetTrack(keep.Row, "gap", "");  // it would come after the new start
            }
        }
        var regions = AudioRegions();
        for (var k = sorted.Count - 1; k >= 0; k--)
            Lines.RemoveRange(regions[sorted[k]].Start, regions[sorted[k]].End - regions[sorted[k]].Start);
        Renumber();
    }

    /// <summary>Fill from track lengths… (SPEC §22): the CD-frame lengths of every track but the last, which runs to
    /// the end of the audio. Track 1 keeps its start; tracks are added or deleted at the end to fit; a moved track loses
    /// its INDEX 00. Throws CueException when track 1 has no valid start.</summary>
    public void FillFromLengths(IReadOnlyList<long> lengths)
    {
        var views = Tracks();
        if (views.Count == 0) throw new CueException("The sheet has no track.");
        long start;
        try
        {
            start = CueSheet.FramesFromTime(views[0].Values["start"]);
        }
        catch (CueException e)
        {
            throw new CueException($"Track 1's start isn't valid: {e.Message}");
        }
        var count = lengths.Count + 1;
        if (views.Count > count) DeleteTracks(Enumerable.Range(count, views.Count - count));
        var regions = AudioRegions();
        while (regions.Count < count)
        {
            Insert(regions[^1].End, $"{Lines[regions[^1].Start].Indent}TRACK 00 AUDIO");
            Renumber();
            regions = AudioRegions();
        }
        for (var row = 1; row < count; row++)
        {
            start += lengths[row - 1];
            if (Tracks()[row].Values["start"] != FormatTime(start))
            {
                SetTrack(row, "gap", "");
                SetTrack(row, "start", FormatTime(start));
            }
        }
    }

    /// <summary>TRACK lines numbered 1…N in order; only lines whose number changes are rewritten.</summary>
    void Renumber()
    {
        var number = 0;
        foreach (var region in Structure().Tracks)
        {
            number++;
            var line = Lines[region.Start];
            var words = line.Text.Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries);
            if (words.Length > 1 && words[1].Length > 0 && words[1].All(char.IsAsciiDigit) && int.Parse(words[1]) == number) continue;
            line.Text = $"{line.Indent}TRACK {number:00} {string.Join(" ", words.Skip(2))}".TrimEnd();
        }
    }

    string ContentIndent(Region region)
    {
        for (var i = region.Start + 1; i < region.End; i++)
            if (Lines[i].Text.Trim().Length > 0) return Lines[i].Indent;
        return Lines[region.Start].Indent + "  ";
    }

    void Insert(int index, string text)
    {
        var line = new CueLine(text, Newline);
        if (index >= Lines.Count && Lines.Count > 0 && Lines[^1].End.Length == 0)
        {
            Lines[^1].End = Newline;  // the file had no final line ending: keep it so
            line.End = "";
        }
        Lines.Insert(Math.Min(index, Lines.Count), line);
    }

    /// <summary>Writes UTF-8 with a BOM from now on (a character didn't fit the sheet's encoding).</summary>
    public void UseUtf8Bom()
    {
        (Codec, Bom, EncodingName, FellBack, NotCp1252) = (Strict(new UTF8Encoding(false)), Utf8Bom, "UTF-8 (BOM)", false, false);
        foreach (var line in Lines) line.Forget();  // the original bytes were in the old encoding
    }

    // -- reading and writing --

    [GeneratedRegex("\r\n|\r|\n")]
    private static partial Regex NewlineRegex();

    /// <summary>A line's text as shown: bytes the encoding can't decode show as �, as in the Python app (.NET reads the
    /// five bytes Windows-1252 leaves unassigned as control characters).</summary>
    static string Shown(Encoding codec, byte[] raw)
    {
        var text = codec.GetString(raw);
        return codec.CodePage == 1252 ? Regex.Replace(text, "[\u0081\u008D\u008F\u0090\u009D]", "�") : text;
    }

    /// <summary><paramref name="code"/> null is Auto: a BOM, then UTF-8, then Windows-1252.</summary>
    public static CueDocument Parse(byte[] data, string? code = null, string? path = null)
    {
        byte[] bom = [];
        Encoding? codec = null;
        string? label = null;
        var fellBack = false;
        foreach (var (mark, encoding, name) in new (byte[], Encoding, string)[]
                 {
                     (Utf8Bom, new UTF8Encoding(false), "UTF-8 (BOM)"), ([0xFF, 0xFE], new UnicodeEncoding(false, false), "UTF-16 LE"),
                     ([0xFE, 0xFF], new UnicodeEncoding(true, false), "UTF-16 BE"),
                 })
            if (data.AsSpan().StartsWith(mark) && (code is null || (code == "utf-8" && mark == Utf8Bom)))
            {
                (bom, codec, label) = (mark, Strict(encoding), name);
                break;
            }
        if (codec is null && code is null)
        {
            try
            {
                new UTF8Encoding(false, true).GetString(data);
                (codec, label) = (Strict(new UTF8Encoding(false)), "UTF-8");
            }
            catch (DecoderFallbackException)
            {
                (codec, label, fellBack) = (EncodingOf("cp1252"), "Windows-1252", true);
            }
        }
        else if (codec is null)
        {
            codec = EncodingOf(code!);
            label = Encodings.First(e => e.Code == code).Label;
        }
        var body = data.AsSpan(bom.Length);
        var lines = new List<CueLine>();
        var byteSplit = codec!.CodePage is not (1200 or 1201);  // in UTF-16, CR and LF aren't single bytes
        if (byteSplit)
        {
            var position = 0;
            while (position < body.Length)
            {
                var next = body[position..].IndexOfAny((byte)'\r', (byte)'\n');
                if (next < 0)
                {
                    lines.Add(Line(body[position..].ToArray(), ""));
                    break;
                }
                var at = position + next;
                var end = body[at] == '\r' && at + 1 < body.Length && body[at + 1] == '\n' ? "\r\n" : body[at] == '\r' ? "\r" : "\n";
                lines.Add(Line(body[position..at].ToArray(), end));
                position = at + end.Length;
            }
        }
        else
        {
            var text = codec.GetString(body);
            var position = 0;
            foreach (Match match in NewlineRegex().Matches(text))
            {
                lines.Add(new CueLine(text[position..match.Index], match.Value));
                position = match.Index + match.Length;
            }
            if (position < text.Length) lines.Add(new CueLine(text[position..]));
        }
        CueLine Line(byte[] raw, string end) => new(Shown(codec, raw), end, raw);
        var notCp1252 = fellBack && data.Any(b => b is 0x81 or 0x8D or 0x8F or 0x90 or 0x9D);
        return new CueDocument(lines, codec, label!, bom, fellBack, notCp1252) { Path = path, Saved = data };
    }

    /// <summary>New cue sheet… (SPEC §22): a sheet for one audio file, laid out as EAC does, in UTF-8 with a BOM and CRLF
    /// line endings. A missing tag leaves its line out; track 1 starts at 00:00:00. Not on disk until saved.</summary>
    public static CueDocument New(string audioName, string performer = "", string title = "")
    {
        static string Clean(string text) => string.Join(" ", text.Replace('"', '\'').Split((char[]?)null, StringSplitOptions.RemoveEmptyEntries));
        var texts = new List<string>();
        if (performer.Trim().Length > 0) texts.Add($"PERFORMER \"{Clean(performer)}\"");
        if (title.Trim().Length > 0) texts.Add($"TITLE \"{Clean(title)}\"");
        texts.AddRange([$"FILE \"{audioName}\" WAVE", "  TRACK 01 AUDIO", "    INDEX 01 00:00:00"]);
        var doc = new CueDocument(texts.Select(t => new CueLine(t, "\r\n")).ToList(), Strict(new UTF8Encoding(false)), "UTF-8 (BOM)", Utf8Bom);
        doc.Saved = doc.Format();
        return doc;
    }

    public static CueDocument Read(string path, string? code = null) => Parse(System.IO.File.ReadAllBytes(path), code, path);

    /// <summary>The sheet's bytes: unedited lines exactly as read. Throws CueEncodingException naming the characters
    /// the encoding can't hold.</summary>
    public byte[] Format()
    {
        using var output = new MemoryStream();
        output.Write(Bom);
        var bad = new SortedSet<string>(StringComparer.Ordinal);
        foreach (var line in Lines)
        {
            if (line.Raw is { } raw) output.Write(raw);
            else
            {
                try
                {
                    output.Write(Codec.GetBytes(line.Text));
                }
                catch (EncoderFallbackException)
                {
                    foreach (var c in line.Text.EnumerateRunes())
                    {
                        try
                        {
                            Codec.GetBytes(c.ToString());
                        }
                        catch (EncoderFallbackException)
                        {
                            bad.Add(c.ToString());
                        }
                    }
                }
            }
            output.Write(Codec.GetBytes(line.End));
        }
        if (bad.Count > 0) throw new CueEncodingException(EncodingName, [.. bad]);
        return output.ToArray();
    }

    /// <summary>Save (over the sheet's own file) and Save as. The file it replaces goes to the Recycle Bin first, then
    /// the sheet is written in its place. Where Windows can't recycle it, CannotRecycleException unless the user agreed
    /// to overwrite it for good. Throws CueEncodingException or IOException with nothing changed on disk.</summary>
    public void Save(string path, Action<string>? recycle = null, Func<string, string?>? canRecycle = null, bool overwriteForGood = false)
    {
        var data = Format();
        if (System.IO.File.Exists(path))
        {
            canRecycle ??= Recycle.CanRecycle;
            if (canRecycle(path) is { } reason)
            {
                if (!overwriteForGood) throw new CannotRecycleException(reason);
            }
            else (recycle ?? Recycle.Send)(path);
        }
        System.IO.File.WriteAllBytes(path, data);
        Path = path;
        Saved = data;
    }

    /// <summary>Save as onto a name the user chose not to replace: "image (2).cue", "image (3).cue"…</summary>
    public static string NumberedPath(string path)
    {
        var folder = System.IO.Path.GetDirectoryName(path)!;
        HashSet<string> taken;
        try
        {
            taken = Directory.EnumerateFileSystemEntries(folder).Select(p => Tags.LyricsKey.CaseFold(System.IO.Path.GetFileName(p))).ToHashSet();
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            taken = [];
        }
        return System.IO.Path.Combine(folder, SplitPlanner.FreeName(System.IO.Path.GetFileName(path), taken));
    }

    // -- Fill from track lengths… --

    [GeneratedRegex(@"^(\d+):(\d{1,2})(?::(\d{1,2}))?$")]
    private static partial Regex LengthRegex();

    /// <summary>A tracklist's length, "m:ss" or "m:ss:ff", in CD frames; null when malformed.</summary>
    public static long? ParseLength(string text)
    {
        var match = LengthRegex().Match(text.Trim());
        if (!match.Success) return null;
        var (minutes, seconds, frames) = (long.Parse(match.Groups[1].Value), int.Parse(match.Groups[2].Value),
            match.Groups[3].Success ? int.Parse(match.Groups[3].Value) : 0);
        if (seconds >= 60 || frames >= CueSheet.FramesPerSecond) return null;
        return (minutes * 60 + seconds) * CueSheet.FramesPerSecond + frames;
    }

    /// <summary>"m:ss", or "m:ss:ff" when it isn't a whole second.</summary>
    public static string FormatLength(long frames)
    {
        var (seconds, ff) = (frames / CueSheet.FramesPerSecond, frames % CueSheet.FramesPerSecond);
        var text = $"{seconds / 60}:{seconds % 60:00}";
        return ff > 0 ? $"{text}:{ff:00}" : text;
    }

    /// <summary>The lengths typed for every track but the last, in frames, and "" — or null and why OK is disabled.</summary>
    public static (List<long>? Lengths, string Why) CheckLengths(IReadOnlyList<string> texts, long firstStart, long audioFrames)
    {
        var lengths = new List<long>();
        for (var n = 1; n <= texts.Count; n++)
        {
            if (ParseLength(texts[n - 1]) is not { } frames) return (null, $"Track {n}'s length isn't m:ss or m:ss:ff.");
            if (frames == 0) return (null, $"Track {n} has no length.");
            lengths.Add(frames);
        }
        var end = firstStart + lengths.Sum();
        if (end >= audioFrames)
            return (null, $"The lengths before the last track reach {FormatLength(end)}, past the end of the audio ({FormatLength(audioFrames)}).");
        return (lengths, "");
    }

    // -- live validation --

    /// <summary>The sheet's bad values: malformed times, starts that don't increase, a start past the end of the audio
    /// (with <paramref name="info"/>), a gap after its track's start, fewer than two tracks, double quotes in a value,
    /// a disc number that isn't a number.</summary>
    public List<CueProblem> Validate(SourceInfo? info = null)
    {
        var problems = new List<CueProblem>();
        var album = Album();
        foreach (var (name, (_, quoted)) in AlbumFields)
            if (quoted && album[name].Contains('"')) problems.Add(new CueProblem(null, name, "A cue value can't contain a double quote (\")."));
        foreach (var name in new[] { "disc", "disc_total" })
        {
            var head = album[name].Split('/')[0].Trim();
            if (album[name].Length > 0 && !(head.Length > 0 && head.All(char.IsAsciiDigit)))
                problems.Add(new CueProblem(null, name, $"'{album[name]}' isn't a number."));
        }
        var views = Tracks();
        if (views.Count < 2) problems.Add(new CueProblem(null, "", "A sheet needs at least two tracks to be split."));
        var starts = new List<long?>();
        foreach (var view in views)
        {
            var n = view.Row + 1;
            foreach (var (name, (_, quoted)) in TrackFields)
                if (quoted && view.Values[name].Contains('"')) problems.Add(new CueProblem(view.Row, name, "A cue value can't contain a double quote (\")."));
            long? start = null, gap = null;
            if (view.Values["start"].Length == 0) problems.Add(new CueProblem(view.Row, "start", $"Track {n} has no start time (INDEX 01)."));
            else
            {
                try
                {
                    start = CueSheet.FramesFromTime(view.Values["start"]);
                }
                catch (CueException e)
                {
                    problems.Add(new CueProblem(view.Row, "start", $"{e.Message}."));
                }
            }
            if (view.Values["gap"].Length > 0)
            {
                try
                {
                    gap = CueSheet.FramesFromTime(view.Values["gap"]);
                }
                catch (CueException e)
                {
                    problems.Add(new CueProblem(view.Row, "gap", $"{e.Message}."));
                }
            }
            if (gap is not null && start is not null && gap > start)
                problems.Add(new CueProblem(view.Row, "gap", $"Track {n}'s gap (INDEX 00) must come before its start."));
            starts.Add(start);
        }
        for (var i = 1; i < starts.Count; i++)
            if (starts[i] is { } s && starts[i - 1] is { } p && s <= p) problems.Add(new CueProblem(i, "start", $"Track {i + 1} must start after track {i}."));
        if (info is not null)
            for (var i = 0; i < starts.Count; i++)
                if (starts[i] is { } s && SplitPlanner.SamplesFromFrames(s, info.Rate) >= info.Samples)
                    problems.Add(new CueProblem(i, "start",
                        $"Track {i + 1} starts after the end of the audio ({FormatTime(s)}, the audio lasts {FormatTime(info.Samples * CueSheet.FramesPerSecond / info.Rate)})."));
        return problems;
    }
}
