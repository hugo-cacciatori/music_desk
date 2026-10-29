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
/// what an entry starts. It only looks at the items' kinds and extensions; it never reads a file.</summary>
public static class MenuLogic
{
    public const string SubmenuLabel = "Refrain";

    public static readonly Entry Open = new("open", "Open in Refrain", "Show this folder in Refrain, adding it to the library if needed");
    public static readonly Entry Settings = new("settings", "Settings", "Open Refrain's Options window");
    public static readonly Entry Separator = new("", "", "");

    public static Clicked Classify(bool background, IReadOnlyList<(string Path, bool IsFolder)> items) =>
        background ? Clicked.Background
        : items.Count > 1 ? Clicked.Selection
        : items.Count == 1 && items[0].IsFolder ? Clicked.Folder
        : Clicked.File;

    /// <summary>The kept functions' entries, each added with its rebuild task (SPEC §36 "7.", "8."): Open in Refrain on
    /// one folder or empty space; a separator; Settings, always (SPEC §31 "Settings").</summary>
    public static List<Entry> Entries(Clicked clicked, IReadOnlyList<(string Path, bool IsFolder)> items)
    {
        var entries = new List<Entry>();
        if (clicked is Clicked.Background or Clicked.Folder) entries.Add(Open);
        if (entries.Count > 0) entries.Add(Separator);
        entries.Add(Settings);
        return entries;
    }

    /// <summary>The preview's data folder (<c>%LOCALAPPDATA%\Refrain\next\</c>), where list files go.</summary>
    public static string MenuFolder => Path.Combine(
        Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Refrain", "next", "menu");

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
