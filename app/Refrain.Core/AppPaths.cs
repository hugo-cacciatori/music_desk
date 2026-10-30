namespace Refrain.Core;

/// <summary>Where the app keeps its own files (Ground rule 1): <c>%LOCALAPPDATA%\Refrain\</c> for the installer's build,
/// where the Python app's files are migrated (SPEC §36 "10."); <c>%LOCALAPPDATA%\Refrain\next\</c> for every other build,
/// so a preview never touches the Python app's live files. REFRAIN_DATA overrides it, so tests and manual checks never
/// touch the real one.</summary>
public static class AppPaths
{
#if REFRAIN_RELEASE
    public const bool Release = true;
    const string Sub = "";
#else
    public const bool Release = false;
    const string Sub = "next";
#endif

    public static string DataFolder
    {
        get
        {
            var set = Environment.GetEnvironmentVariable("REFRAIN_DATA");
            var folder = string.IsNullOrEmpty(set)
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Refrain", Sub)
                : set;
            Directory.CreateDirectory(folder);
            return folder;
        }
    }

    public static string Database => Path.Combine(DataFolder, "library.db");
    public static string Settings => Path.Combine(DataFolder, "settings.json");
    public static string Logs => Directory.CreateDirectory(Path.Combine(DataFolder, "logs")).FullName;
}
