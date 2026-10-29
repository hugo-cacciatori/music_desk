using System.Buffers.Binary;
using Refrain.Core.Options;
using Refrain.Core.ReplayGain;

namespace Refrain.Core.Cue;

/// <summary>The audio file a sheet points at: its format, rate, length in samples, channels, bits, the FLAC
/// STREAMINFO MD5 when set, the album values the sheet may lack, and its front cover.</summary>
public sealed record SourceInfo(string Format, int Rate, long Samples, int Channels, int Bits, string? Md5,
    IReadOnlyDictionary<string, object> Tags, byte[]? Cover);

/// <summary>A track the split writes (number 0: a hidden track before track 1), in samples.</summary>
public sealed class PlannedTrack(int number, long start, long end, Dictionary<string, object> fields, string isrc = "", int? row = null)
{
    public int Number { get; } = number;
    public long Start { get; set; } = start;
    public long End { get; } = end;
    public Dictionary<string, object> Fields { get; } = fields;  // rename fields, as the masks take them
    public string Isrc { get; } = isrc;
    public int? Row { get; } = row;  // its row among the sheet's audio tracks; null for a hidden track
    public string Name { get; set; } = "";
    public string? Path { get; set; }
    public long Length => End - Start;
}

/// <summary>What splitting a sheet would write, or why it can't be split.</summary>
public sealed class AlbumPlan(string cue)
{
    public string Cue { get; } = cue;
    public string? Source { get; set; }
    public CueSheet? Sheet { get; set; }
    public SourceInfo? Info { get; set; }
    public List<PlannedTrack> Tracks { get; set; } = [];  // every track: they all set the boundaries
    public string? Refusal { get; set; }  // why the album is left alone; null when it can be split
    public List<string> Warnings { get; } = [];
    public IReadOnlySet<int>? Ticked { get; init; }  // the track numbers to write; null for all
    public string? Destination { get; init; }  // where the tracks go; null: the .cue's folder
    public List<(int Track, string Why)> Problems { get; set; } = [];  // a path too long
    public List<(int Track, string Why)> Notes { get; set; } = [];  // how a track's name was changed

    public string Folder => Destination ?? System.IO.Path.GetDirectoryName(Cue)!;
    public bool IsTicked(int number) => Ticked is null || Ticked.Contains(number);
    public List<PlannedTrack> Written => Tracks.Where(t => IsTicked(t.Number)).ToList();
    public bool AllTicked => Written.Count == Tracks.Count;
}

/// <summary>The split's plan (SPEC §22), ported from the Python app's <c>core/cue.py</c>: each track runs from its
/// INDEX 01 to the next track's, so a gap (INDEX 00) stays at the end of the track before it; every sample of the
/// source lands in exactly one track. Nothing here writes.</summary>
public static class SplitPlanner
{
    public const string HiddenTitle = "Hidden track";
    const string FallbackName = "Track";  // a track no mask fits is named "03 Track.flac"
    const int HiddenKeepSeconds = 2;  // audio before track 1 shorter than this (or silent) joins track 1
    public const int MaxPath = 259;  // characters in a full path that Windows tools reliably open

    public static readonly IReadOnlyDictionary<string, string> Supported = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase)
    {
        [".flac"] = "FLAC", [".wav"] = "WAV", [".aif"] = "AIFF", [".aiff"] = "AIFF",
    };

    /// <summary>CD frames → samples at <paramref name="rate"/>, rounded half up (exact at 44.1 and 48 kHz).</summary>
    public static long SamplesFromFrames(long frames, int rate) =>
        (2 * frames * rate + CueSheet.FramesPerSecond) / (2 * CueSheet.FramesPerSecond);

    /// <summary>The refusal for a source the split can't read.</summary>
    public static string UnsupportedMessage(string path)
    {
        var name = System.IO.Path.GetExtension(path).TrimStart('.').ToUpperInvariant();
        return $"{(name.Length > 0 ? name : "This file type")} isn't supported: the split reads FLAC, WAV and AIFF.";
    }

    /// <summary>Throws CueException for a format the split doesn't read, IOException.</summary>
    public static SourceInfo ReadSource(string path)
    {
        if (!Supported.TryGetValue(System.IO.Path.GetExtension(path), out var kind)) throw new CueException(UnsupportedMessage(path));
        int rate, channels, bits;
        long samples;
        string? md5 = null;
        if (kind == "FLAC") (rate, samples, channels, bits, md5) = FlacInfo(path);
        else if (kind == "WAV") (rate, samples, channels, bits) = WavInfo(path);
        else (rate, samples, channels, bits) = AiffInfo(path);
        var tags = new Dictionary<string, object>();
        byte[]? cover = null;
        try
        {
            var read = Tags.TagFile.Read(path);
            foreach (var (key, value) in RenameFields.Of(read))
                if (key is "album" or "albumartist" or "year" or "genre") tags[key] = value;
            using var file = TagLib.File.Create(new Tags.SharedRead(path), TagLib.ReadStyle.None);
            var pictures = file.Tag.Pictures;
            var front = pictures.FirstOrDefault(p => p.Type == TagLib.PictureType.FrontCover) ?? pictures.FirstOrDefault();
            cover = front?.Data.Data;
        }
        catch (Exception)
        {
            // a source without readable tags just gives no fallback values
        }
        return new SourceInfo(kind, rate, samples, channels, bits, md5, tags, cover);
    }

    static (int, long, int, int, string?) FlacInfo(string path)
    {
        using var stream = File.OpenRead(path);
        Span<byte> head = stackalloc byte[4 + 4 + 34];
        stream.ReadExactly(head);
        if (!head[..4].SequenceEqual("fLaC"u8) || (head[4] & 0x7F) != 0)
            throw new CueException($"{System.IO.Path.GetFileName(path)} isn't a FLAC file.");
        var info = head[8..];
        var packed = BinaryPrimitives.ReadUInt64BigEndian(info[10..18]);
        var rate = (int)(packed >> 44);
        var channels = (int)((packed >> 41) & 0x7) + 1;
        var bits = (int)((packed >> 36) & 0x1F) + 1;
        var samples = (long)(packed & 0xFFFFFFFFFL);
        var md5 = Convert.ToHexStringLower(info[18..34]);
        return (rate, samples, channels, bits, md5.Trim('0').Length == 0 ? null : md5);
    }

    static (int, long, int, int) WavInfo(string path)
    {
        var name = System.IO.Path.GetFileName(path);
        using var reader = new BinaryReader(File.OpenRead(path));
        string Fail(string why) => $"{name} can't be read as a WAV file ({why}).";
        if (reader.BaseStream.Length < 12 || new string(reader.ReadChars(4)) != "RIFF") throw new CueException(Fail("file does not start with RIFF id"));
        reader.ReadUInt32();
        if (new string(reader.ReadChars(4)) != "WAVE") throw new CueException(Fail("not a WAVE file"));
        int? rate = null, channels = null, width = null;
        while (reader.BaseStream.Position + 8 <= reader.BaseStream.Length)
        {
            var id = new string(reader.ReadChars(4));
            var size = reader.ReadUInt32();
            var next = reader.BaseStream.Position + size + (size & 1);
            if (id == "fmt ")
            {
                var format = reader.ReadUInt16();
                channels = reader.ReadUInt16();
                rate = (int)reader.ReadUInt32();
                reader.ReadUInt32();
                reader.ReadUInt16();
                var bitsPerSample = reader.ReadUInt16();
                if (format == 0xFFFE && size >= 40)
                {
                    reader.ReadBytes(8);  // cbSize, valid bits, channel mask
                    format = reader.ReadUInt16();  // the sub-format GUID's first two bytes
                    if (format != 1)
                        throw new CueException(format == 3 ? "32-bit float WAV isn't supported: FLAC can't hold floating-point audio." : Fail("unknown extended format"));
                }
                else if (format == 3) throw new CueException("32-bit float WAV isn't supported: FLAC can't hold floating-point audio.");
                else if (format != 1) throw new CueException(Fail($"unknown format: {format}"));
                width = (bitsPerSample + 7) / 8;
            }
            else if (id == "data")
            {
                if (rate is null || channels is null || width is null) throw new CueException(Fail("data chunk before fmt chunk"));
                return (rate.Value, size / (channels.Value * width.Value), channels.Value, width.Value * 8);
            }
            reader.BaseStream.Position = next;
        }
        throw new CueException(Fail("fmt chunk and/or data chunk missing"));
    }

    static (int, long, int, int) AiffInfo(string path)
    {
        var name = System.IO.Path.GetFileName(path);
        using var stream = File.OpenRead(path);
        Span<byte> header = stackalloc byte[12];
        if (stream.Read(header) < 12 || !header[..4].SequenceEqual("FORM"u8) ||
            !(header[8..12].SequenceEqual("AIFF"u8) || header[8..12].SequenceEqual("AIFC"u8)))
            throw new CueException($"{name} isn't an AIFF file.");
        Span<byte> chunk = stackalloc byte[8];
        while (stream.Read(chunk) == 8)
        {
            var size = BinaryPrimitives.ReadUInt32BigEndian(chunk[4..]);
            if (chunk[..4].SequenceEqual("COMM"u8))
            {
                var data = new byte[size];
                stream.ReadExactly(data);
                var channels = BinaryPrimitives.ReadInt16BigEndian(data);
                var frames = BinaryPrimitives.ReadUInt32BigEndian(data.AsSpan(2));
                var bits = BinaryPrimitives.ReadInt16BigEndian(data.AsSpan(6));
                var exponent = BinaryPrimitives.ReadUInt16BigEndian(data.AsSpan(8));
                var mantissa = BinaryPrimitives.ReadUInt64BigEndian(data.AsSpan(10));  // an 80-bit extended float
                var rate = (int)Math.Round(mantissa * Math.Pow(2, (exponent & 0x7FFF) - 16383 - 63), MidpointRounding.ToEven);
                return (rate, frames, channels, bits);
            }
            stream.Seek(size + (size & 1), SeekOrigin.Current);
        }
        throw new CueException($"{name} has no COMM chunk.");
    }

    /// <summary>The FILE line's audio file, matched ignoring case as Windows does.</summary>
    static string? SourceInFolder(string folder, string name)
    {
        try
        {
            var wanted = Tags.LyricsKey.CaseFold(name);
            return Directory.EnumerateFileSystemEntries(folder).FirstOrDefault(p => Tags.LyricsKey.CaseFold(System.IO.Path.GetFileName(p)) == wanted);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return null;
        }
    }

    /// <summary>A track's rename fields from the sheet (SPEC §22), with the source's album values where the sheet has
    /// none: numbers as numbers, text only when not blank, so the masks name a split track as a rename would.</summary>
    public static Dictionary<string, object> TrackFields(CueSheet sheet, CueTrack? track, int number, int total,
        IReadOnlyDictionary<string, object> fallback)
    {
        string Fall(string key) => fallback.TryGetValue(key, out var v) ? v.ToString() ?? "" : "";
        var text = new (string Key, string Value)[]
        {
            ("title", track is not null ? track.Title : HiddenTitle),
            ("artist", FirstNonEmpty(track?.Performer ?? "", sheet.Performer, Fall("albumartist"))),
            ("albumartist", FirstNonEmpty(sheet.Performer, Fall("albumartist"))),
            ("album", FirstNonEmpty(sheet.Title, Fall("album"))),
            ("year", FirstNonEmpty(sheet.Date, Fall("year"))),
            ("genre", FirstNonEmpty(sheet.Genre, Fall("genre"))),
            ("composer", track?.Songwriter ?? ""),
        };
        var result = new Dictionary<string, object> { ["track"] = (long)number, ["track_total"] = (long)total };
        if (sheet.Disc is { } disc) result["disc"] = (long)disc;
        if (sheet.DiscTotal is { } discTotal) result["disc_total"] = (long)discTotal;
        foreach (var (key, value) in text)
            if (value.Trim().Length > 0) result[key] = value;
        return result;
    }

    static string FirstNonEmpty(params string[] values) => values.FirstOrDefault(v => v.Length > 0) ?? "";

    /// <summary>What splitting this sheet would write, or why it can't be split; nothing on disk changes.
    /// <paramref name="isSilent"/>(source, start, end) decodes that range; without it, audio before track 1 that is
    /// 2 s or longer is kept as a hidden track.</summary>
    public static AlbumPlan Plan(CueSheet sheet, string cue, IReadOnlyList<string> masks, string replacement,
        IEnumerable<int>? ticked = null, Func<string, long, long, bool>? isSilent = null,
        Func<string, SourceInfo>? readInfo = null, string? destination = null)
    {
        readInfo ??= ReadSource;
        var plan = new AlbumPlan(cue) { Ticked = ticked?.ToHashSet(), Destination = destination, Sheet = sheet };
        if (sheet.NotCp1252)
            plan.Warnings.Add("The sheet isn't in Windows-1252 (it holds bytes that code page lacks): pick its encoding.");
        if (sheet.DataTracks > 0) plan.Warnings.Add($"{sheet.DataTracks} data track{(sheet.DataTracks > 1 ? "s" : "")} skipped.");
        var names = sheet.Files.Select(Tags.LyricsKey.CaseFold).Distinct().ToList();
        if (names.Count != 1)
        {
            plan.Refusal = names.Count > 0 ? "Not a single-file album: the sheet names several files." : "Not a single-file album: the sheet names no file.";
            return plan;
        }
        var source = SourceInFolder(System.IO.Path.GetDirectoryName(cue)!, sheet.Files[0]);
        if (source is null)
        {
            plan.Refusal = $"Not a single-file album: {sheet.Files[0]} isn't in the folder.";
            return plan;
        }
        plan.Source = source;
        SourceInfo info;
        try
        {
            info = readInfo(source);
        }
        catch (CueException e)
        {
            plan.Refusal = e.Message;
            return plan;
        }
        catch (Exception e)
        {
            plan.Refusal = $"{System.IO.Path.GetFileName(source)} can't be read ({e.GetType().Name}: {e.Message}).";
            return plan;
        }
        plan.Info = info;
        if (destination is not null && !Directory.Exists(destination))
        {
            plan.Refusal = $"The destination folder {destination} doesn't exist. Choose an existing folder.";
            return plan;
        }
        if (sheet.Tracks.Count < 2)
        {
            plan.Refusal = "The sheet has fewer than two audio tracks.";
            return plan;
        }
        var starts = sheet.Tracks.Select(t => SamplesFromFrames(t.Index01!.Value, info.Rate)).ToList();
        for (var i = 1; i < starts.Count; i++)
            if (starts[i] <= starts[i - 1])
            {
                plan.Refusal = $"Track {sheet.Tracks[i].Number} doesn't start after track {sheet.Tracks[i - 1].Number}: the times must increase.";
                return plan;
            }
        if (starts[^1] >= info.Samples)
        {
            var late = sheet.Tracks[starts.FindIndex(s => s >= info.Samples)];
            plan.Refusal = $"Track {late.Number} starts after the end of the audio ({Clock(starts[^1], info.Rate)} of {Clock(info.Samples, info.Rate)}).";
            return plan;
        }
        var total = sheet.Tracks.Count;
        var tracks = sheet.Tracks.Select((t, row) => new PlannedTrack(t.Number, starts[row],
            row + 1 < starts.Count ? starts[row + 1] : info.Samples, TrackFields(sheet, t, t.Number, total, info.Tags), t.Isrc, row)).ToList();
        if (starts[0] > 0)
        {
            var audible = starts[0] >= HiddenKeepSeconds * info.Rate && !(isSilent?.Invoke(source, 0, starts[0]) ?? false);
            if (audible) tracks.Insert(0, new PlannedTrack(0, 0, starts[0], TrackFields(sheet, null, 0, total, info.Tags)));
            else tracks[0].Start = 0;  // a short or silent pregap joins track 1
        }
        plan.Tracks = tracks;
        if (NameTracks(plan, masks, replacement) is { } problem) plan.Refusal = problem;
        return plan;
    }

    /// <summary>Plans a .cue on disk (read in Auto).</summary>
    public static AlbumPlan Plan(string cue, IReadOnlyList<string> masks, string replacement,
        Func<string, long, long, bool>? isSilent = null)
    {
        CueSheet sheet;
        try
        {
            sheet = CueSheet.Read(cue);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or CueException)
        {
            return new AlbumPlan(cue) { Refusal = $"The cue sheet can't be read: {e.Message}" };
        }
        return Plan(sheet, cue, masks, replacement, isSilent: isSilent);
    }

    /// <summary>Gives each track its file name with the masks (SPEC §21). Nothing is ever overwritten: a ticked
    /// track whose name is taken gets " (2)", " (3)"…, and a track no mask fits is named from its number. Returns why
    /// the album can't be split (a path too long, no track ticked), or null.</summary>
    public static string? NameTracks(AlbumPlan plan, IReadOnlyList<string> masks, string replacement, IEnumerable<string>? existing = null)
    {
        var folder = plan.Folder;
        if (existing is null)
        {
            try
            {
                existing = Directory.EnumerateFileSystemEntries(folder).Select(p => System.IO.Path.GetFileName(p)!).ToList();
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                existing = [];
            }
        }
        var taken = existing.Select(Tags.LyricsKey.CaseFold).ToHashSet();
        var planned = new HashSet<string>();
        var refusals = new List<string>();
        plan.Problems = [];
        plan.Notes = [];
        foreach (var track in plan.Tracks)
        {
            var ticked = plan.IsTicked(track.Number);
            string name;
            if (Masks.NameFor(track.Fields, masks, replacement, ".flac") is var (found, _)) name = found;
            else
            {
                name = $"{track.Number:00} {FallbackName}.flac";
                if (ticked)
                    plan.Notes.Add((track.Number, $"No file name mask fits (missing: {Masks.MissingFrom(masks[0], track.Fields)}), so it's named {name}."));
            }
            if (ticked)
            {
                var free = FreeName(name, taken.Union(planned).ToHashSet());
                if (free != name)
                {
                    var why = planned.Contains(Tags.LyricsKey.CaseFold(name)) ? "another ticked track has that name" : "it already exists";
                    plan.Notes.Add((track.Number, $"{name} is taken ({why}), so it's named {free}."));
                    name = free;
                }
                planned.Add(Tags.LyricsKey.CaseFold(name));
            }
            track.Name = name;
            track.Path = System.IO.Path.Combine(folder, name);
            if (ticked && track.Path.Length > MaxPath)
            {
                refusals.Add($"Track {track.Number}'s file would have a path of {track.Path.Length} characters, longer than " +
                             $"Windows tools can open ({MaxPath}): {name}. Shorten the masks or move the album.");
                plan.Problems.Add((track.Number, $"The path would have {track.Path.Length} characters, longer than Windows tools can open ({MaxPath})."));
            }
        }
        if (refusals.Count > 0) return refusals[0];
        return plan.Written.Count == 0 ? "No track is ticked." : null;
    }

    /// <summary><paramref name="name"/>, or "name (2).ext", "name (3).ext"… (Explorer's style for copies): the first
    /// not taken (casefolded names).</summary>
    public static string FreeName(string name, IReadOnlySet<string> taken)
    {
        if (!taken.Contains(Tags.LyricsKey.CaseFold(name))) return name;
        var (stem, ext) = (System.IO.Path.GetFileNameWithoutExtension(name), System.IO.Path.GetExtension(name));
        var n = 2;
        while (taken.Contains(Tags.LyricsKey.CaseFold($"{stem} ({n}){ext}"))) n++;
        return $"{stem} ({n}){ext}";
    }

    static string Clock(long samples, int rate)
    {
        var seconds = (double)samples / rate;
        return $"{(int)Math.Floor(seconds / 60)}:{Python.Fixed(seconds % 60, 2).PadLeft(5, '0')}";
    }
}
