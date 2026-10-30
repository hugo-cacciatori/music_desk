using System.Globalization;
using System.Text.Json;

namespace RefrainMenu;

/// <summary>What was right-clicked (SPEC §31 "The submenu"): a folder's empty space, one folder, one file, or a
/// selection of several items.</summary>
public enum Clicked { Background, Folder, File, Selection }

/// <summary>An entry of the Refrain submenu: its verb (also the app's <c>--entry</c>), label and help text. An empty
/// verb is a separator.</summary>
public sealed record Entry(string Verb, string Label, string Help)
{
    public bool IsSeparator => Verb.Length == 0;
}

/// <summary>The submenu's plain logic, apart from COM so tests can use it: which entries the clicked items get, and
/// what an entry starts. It only looks at the items' kinds and extensions; it never reads a file. The app compiles
/// this same file for its own right-click menus and the ReplayGain button (SPEC §35: one entry list).</summary>
public static class MenuLogic
{
    public const string SubmenuLabel = "Refrain";

    // The verbs: the app's --entry names, as the Python app's (SPEC §31 "As built").
    public const string ReplayGainAlbum = "replaygain-album", ReplayGainDiscography = "replaygain-discography",
        ReplayGainAlbums = "replaygain-albums", ReplayGainTracks = "replaygain-tracks", ReplayGainLoose = "replaygain-loose",
        RemoveReplayGain = "remove-replaygain", Tidy = "tidy", Split = "split", EditCue = "edit-cue",
        NewCue = "new-cue";  // New cue sheet… (the Metadata tab's, not a menu entry)

    public static readonly IReadOnlyList<string> ReplayGainVerbs =
        [ReplayGainAlbum, ReplayGainDiscography, ReplayGainAlbums, ReplayGainTracks, ReplayGainLoose];

    public static readonly Entry Open = new("open", "Open in Refrain", "Show this folder in Refrain, adding it to the library if needed");
    public static readonly Entry Settings = new("settings", "Settings", "Open Refrain's Options window");
    public static readonly Entry Separator = new("", "", "");

    /// <summary>What rsgain scans (SPEC §1), by extension; held equal to Refrain.Core's Formats.ReplayGain by a test.</summary>
    public static readonly IReadOnlySet<string> ReplayGainExtensions = new HashSet<string>(StringComparer.OrdinalIgnoreCase)
    {
        ".flac", ".ogg", ".oga", ".opus", ".spx", ".mp3", ".mp2", ".wav", ".aif", ".aiff", ".aifc", ".m4a", ".mp4",
        ".wv", ".mpc", ".tak", ".wma",
    };

    static readonly Dictionary<string, string> Help = new()
    {
        [ReplayGainAlbum] = "Runs ReplayGain on this folder as one album, with the target set in Refrain",
        [ReplayGainDiscography] = "Runs ReplayGain on each album in this folder, then levels them together",
        [ReplayGainAlbums] = "Runs ReplayGain on each folder as its own album, one after another",
        [ReplayGainTracks] = "Runs ReplayGain on the selected tracks as one album; the folder's other tracks are left as they are",
        [ReplayGainLoose] = "Runs ReplayGain on each selected track alone, as its own album, with no log",
        [RemoveReplayGain] = "Removes the selected tracks' ReplayGain tags, after a question",
        [Tidy] = "Plans Tidy (split, rename, ReplayGain) on every album here; nothing changes until you run it",
        [Split] = "Shows what splitting this cue sheet writes; nothing is split until you click Split",
        [EditCue] = "Opens this cue sheet in Refrain's cue sheet editor",
    };

    public static Clicked Classify(bool background, IReadOnlyList<(string Path, bool IsFolder)> items) =>
        background ? Clicked.Background
        : items.Count > 1 ? Clicked.Selection
        : items.Count == 1 && items[0].IsFolder ? Clicked.Folder
        : Clicked.File;

    /// <summary>The ReplayGain target as the labels show it: "−15 LUFS", "−18.5 LUFS" (a real minus sign).</summary>
    public static string TargetText(double target) =>
        $"{(target < 0 ? "−" : "")}{Math.Abs(target).ToString("G6", CultureInfo.InvariantCulture)} LUFS";

    /// <summary>The entries, in order (SPEC §31; each kept function's are added with its rebuild task, §36 "7.", "8."):
    /// Open in Refrain on one folder or empty space; the ReplayGain entries on folders only, or on files with tracks
    /// rsgain scans (no folder); Remove ReplayGain tags when every item is such a track; Tidy on empty space or folders
    /// only (never files); Split this cue sheet… and Edit this cue sheet… on one .cue alone; a separator; Settings,
    /// always.
    /// <paramref name="target"/>: the ReplayGain options' target, shown in the labels.</summary>
    public static List<Entry> Entries(Clicked clicked, IReadOnlyList<(string Path, bool IsFolder)> items, double target = -15)
    {
        var background = clicked == Clicked.Background;
        var folders = background ? 1 : items.Count(i => i.IsFolder);
        var files = background ? 0 : items.Count(i => !i.IsFolder);
        var audio = background ? 0 : items.Count(i => !i.IsFolder && ReplayGainExtensions.Contains(Path.GetExtension(i.Path)));
        var oneFolder = items.Where(i => !i.IsFolder).Select(i => Path.GetDirectoryName(i.Path)?.ToUpperInvariant())
            .Distinct().Count() <= 1;  // an album is one folder's tracks (a search's results may not be)
        var suffix = $" ({TargetText(target)})";
        Entry Make(string verb, string label) => new(verb, label + suffix, Help[verb]);

        var entries = new List<Entry>();
        if (background || (folders == 1 && files == 0))
        {
            entries.Add(Open);
            entries.Add(Make(ReplayGainAlbum, "ReplayGain as an album"));
            entries.Add(Make(ReplayGainDiscography, "ReplayGain as a discography"));
        }
        else if (folders > 1 && files == 0)
            entries.Add(Make(ReplayGainAlbums, $"ReplayGain: {folders} folders, each as an album"));
        else if (folders == 0 && audio == 1)
            entries.Add(Make(ReplayGainLoose, "ReplayGain this track"));
        else if (folders == 0 && audio > 1)
        {
            if (oneFolder) entries.Add(Make(ReplayGainTracks, $"ReplayGain: these {audio} tracks as an album"));
            entries.Add(Make(ReplayGainLoose, $"ReplayGain: each of these {audio} tracks on its own"));
        }
        if (!background && folders == 0 && files > 0 && audio == files)
            entries.Add(new Entry(RemoveReplayGain, "Remove ReplayGain tags…", Help[RemoveReplayGain]));
        if (background || (folders > 0 && files == 0))
            entries.Add(new Entry(Tidy, background || folders == 1 ? "Tidy this folder…" : $"Tidy {folders} folders…", Help[Tidy]));
        if (!background && folders == 0 && files == 1 && Path.GetExtension(items[0].Path).Equals(".cue", StringComparison.OrdinalIgnoreCase))
        {
            entries.Add(new Entry(Split, "Split this cue sheet…", Help[Split]));
            entries.Add(new Entry(EditCue, "Edit this cue sheet…", Help[EditCue]));
        }
        if (entries.Count > 0) entries.Add(Separator);
        entries.Add(Settings);
        return entries;
    }

    /// <summary>The preview's data folder (<c>%LOCALAPPDATA%\Refrain\next\</c>), where list files go.</summary>
    public static string DataFolder => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Refrain", "next");

    public static string MenuFolder => Path.Combine(DataFolder, "menu");

    /// <summary>The ReplayGain options' target in the app's settings.json (read when the menu opens); −15, the default,
    /// when it can't be read.</summary>
    public static double Target(string settingsFile)
    {
        try
        {
            using var document = JsonDocument.Parse(File.ReadAllBytes(settingsFile));
            return document.RootElement.TryGetProperty("replay_gain", out var options) &&
                   options.ValueKind == JsonValueKind.Object && options.TryGetProperty("target", out var target) &&
                   target.ValueKind == JsonValueKind.Number
                ? target.GetDouble()
                : -15;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or JsonException)
        {
            return -15;
        }
    }

    /// <summary>What an entry starts: Open is <c>Refrain.exe "folder"</c> (SPEC §29); every other entry writes the
    /// paths one per line to a new file in the data folder's <c>menu\</c> and starts <c>Refrain.exe --entry --list
    /// file</c>, so a large selection never meets the command line's length limit.</summary>
    public static (string Exe, string Arguments, string? ListText) Command(Entry entry, IReadOnlyList<string> paths,
        string appFolder)
    {
        var exe = Path.Combine(appFolder, "Refrain.exe");
        if (entry.Verb == Open.Verb) return (exe, Quote(paths[0]), null);
        return (exe, $"--{entry.Verb} --list ", string.Join("\n", paths) + "\n");
    }

    /// <summary>A path quoted for a command line (a folder path can't hold a quote; a trailing backslash, as in
    /// "D:\", is doubled so it doesn't escape the closing quote).</summary>
    public static string Quote(string path) => "\"" + (path.EndsWith('\\') ? path + "\\" : path) + "\"";
}
