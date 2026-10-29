namespace Refrain.Core;

/// <summary>Where the app keeps its own files (Ground rule 1). While the rebuild is a preview, that is
/// <c>%LOCALAPPDATA%\Refrain\next\</c>, never the Python app's files (SPEC §36 "4."). REFRAIN_DATA overrides it, so
/// tests and manual checks never touch the real one.</summary>
public static class AppPaths
{
    public static string DataFolder
    {
        get
        {
            var set = Environment.GetEnvironmentVariable("REFRAIN_DATA");
            var folder = string.IsNullOrEmpty(set)
                ? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Refrain", "next")
                : set;
            Directory.CreateDirectory(folder);
            return folder;
        }
    }

    public static string Database => Path.Combine(DataFolder, "library.db");
    public static string Settings => Path.Combine(DataFolder, "settings.json");
    public static string Logs => Directory.CreateDirectory(Path.Combine(DataFolder, "logs")).FullName;
}
