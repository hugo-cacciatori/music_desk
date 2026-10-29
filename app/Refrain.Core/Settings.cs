using System.Text.Json;
using System.Text.Json.Serialization;

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
