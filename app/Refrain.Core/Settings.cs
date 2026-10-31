using System.Text.Json;
using System.Text.Json.Serialization;
using Refrain.Core.Options;
using Refrain.Core.Playback;

namespace Refrain.Core;

/// <summary>A column's place in a list: its key, width and whether it shows.</summary>
public sealed class ColumnState
{
    public string Key { get; set; } = "";
    public double Width { get; set; }
    public bool Visible { get; set; } = true;
}

/// <summary>A list's header: the columns in order, and the sort.</summary>
public sealed class HeaderState
{
    public List<ColumnState> Columns { get; set; } = [];
    public string SortKey { get; set; } = "name";
    public bool Descending { get; set; }
}

/// <summary>The window's remembered state (SPEC §5 "As built (M2 task 5)", §33): <c>settings.json</c> in the data
/// folder. UI state, not options: there is no Options window yet.</summary>
public sealed class Settings
{
    public int? WindowX { get; set; }
    public int? WindowY { get; set; }
    public int? WindowWidth { get; set; }
    public int? WindowHeight { get; set; }
    public bool Maximized { get; set; }
    public bool TreeVisible { get; set; }
    public double TreeWidth { get; set; } = 240;
    public double PanelWidth { get; set; } = 380;
    public double LyricsTextSize { get; set; } = 14;
    public string SearchMode { get; set; } = "files";
    /// <summary>The discography view's info panel was closed (SPEC §37), counted only by the version that recorded it
    /// (<see cref="InfoPanelClosedIn"/>): remembered between sessions, not between installs.</summary>
    public bool InfoPanelClosed { get; set; }
    public string? InfoPanelClosedIn { get; set; }

    // The Player page (SPEC §12, §37): what happens when an album ends, whether Refrain reopens on the last song, and
    // what a task on the playing song's folder does. Unknown values read as the defaults.
    public string PlayerAtEnd { get; set; } = "loop";
    public bool PlayerKeep { get; set; } = true;
    /// <summary>ReplayGain around the player (SPEC §37): which songs have their tags (a mark), and the player applying
    /// their gain, the album's by default, the track's when this page says so; all off by default.</summary>
    public bool PlayerGainMarks { get; set; }
    public bool PlayerApplyGain { get; set; }
    public string PlayerGainMode { get; set; } = "album";
    public string PlayerOnBusy { get; set; } = "stop";
    public AlbumEnd GetAlbumEnd() => PlayerAtEnd == "once" ? AlbumEnd.Once : AlbumEnd.Loop;
    public BusyFolder GetBusyFolder() => PlayerOnBusy == "skip" ? BusyFolder.Skip : BusyFolder.Stop;
    /// <summary>The player's own state, not options: its volume, and where it was when Refrain closed (SPEC §37).</summary>
    public double PlayerVolume { get; set; } = 0.8;
    /// <summary>The preview's excerpts play with their sound (SPEC §37): the preview's speaker icon and the Player page
    /// set the same value. Never over the player: while it plays, previews are silent anyway.</summary>
    public bool PreviewSound { get; set; } = true;
    /// <summary>The Tidy page (SPEC §37 "Tidy in the preview"): the preview's Tidy section alerts (its colour, a red dot)
    /// when Tidy has something to do in the folder.</summary>
    public bool PreviewTidyAlert { get; set; } = true;
    public string? PlayerPath { get; set; }
    public double PlayerSeconds { get; set; }

    // The Options window's pages (SPEC §12, §36 "8."), kept as JSON so a mistyped value falls back alone.
    public JsonElement? FileNames { get; set; }
    public JsonElement? ReplayGain { get; set; }
    public JsonElement? Tidy { get; set; }
    public string OptionsPage { get; set; } = "";
    /// <summary>The Split page (SPEC §12, §22): Save puts a new cue sheet beside its audio file without asking.</summary>
    public bool SaveCueBeside { get; set; }
    /// <summary>The Trim page (SPEC §12, §23): the subfolder the trimmed copy goes in.</summary>
    public string TrimSubfolder { get; set; } = Cue.Trim.DefaultSubfolder;

    public FileNamesOptions GetFileNames() => FileNamesOptions.From(FileNames);
    public void Set(FileNamesOptions options) => FileNames = Element(options.Write);
    public ReplayGainOptions GetReplayGain() => ReplayGainOptions.From(ReplayGain);
    public void Set(ReplayGainOptions options) => ReplayGain = Element(options.Write);
    public TidyOptions GetTidy() => TidyOptions.From(Tidy);
    public void Set(TidyOptions options) => Tidy = Element(options.Write);

    static JsonElement Element(Action<Utf8JsonWriter> write)
    {
        var buffer = new System.Buffers.ArrayBufferWriter<byte>();
        using (var json = new Utf8JsonWriter(buffer)) write(json);
        using var document = JsonDocument.Parse(buffer.WrittenMemory);
        return document.RootElement.Clone();
    }
    public string? LastFolder { get; set; }
    public List<string> ExpandedFolders { get; set; } = [];
    public HeaderState? FileHeader { get; set; }
    public HeaderState? SearchHeader { get; set; }

    public static Settings Load(string path)
    {
        try
        {
            return File.Exists(path)
                ? JsonSerializer.Deserialize(File.ReadAllText(path), SettingsJson.Default.Settings) ?? new Settings()
                : new Settings();
        }
        catch (Exception e) when (e is IOException or JsonException or UnauthorizedAccessException)
        {
            Log.Error($"{path} couldn't be read; the defaults are used", e);
            return new Settings();
        }
    }

    /// <summary>Written to a temporary file first, so a crash never leaves half a file.</summary>
    public void Save(string path)
    {
        try
        {
            var temp = path + ".tmp";
            File.WriteAllText(temp, JsonSerializer.Serialize(this, SettingsJson.Default.Settings));
            File.Move(temp, path, overwrite: true);
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            Log.Error($"{path} couldn't be saved", e);
        }
    }
}

[JsonSourceGenerationOptions(WriteIndented = true, PropertyNamingPolicy = JsonKnownNamingPolicy.SnakeCaseLower)]
[JsonSerializable(typeof(Settings))]
internal sealed partial class SettingsJson : JsonSerializerContext;
