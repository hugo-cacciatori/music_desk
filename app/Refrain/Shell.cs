using System.Diagnostics;
using Refrain.Core;

namespace Refrain;

/// <summary>Handing things to Windows: the app itself never opens or plays files (SPEC §14).</summary>
static class Shell
{
    /// <summary>A file with its default app, whatever the type.</summary>
    public static void Open(string path) => Start(new ProcessStartInfo(path) { UseShellExecute = true });

    /// <summary>A file with its default app; false when Windows has no app for its type.</summary>
    public static bool TryOpen(string path)
    {
        try
        {
            Process.Start(new ProcessStartInfo(path) { UseShellExecute = true })?.Dispose();
            return true;
        }
        catch (System.ComponentModel.Win32Exception e)
        {
            Log.Info($"No app opens {path}: {e.Message}");
            return false;
        }
    }

    /// <summary>File Explorer on a folder itself, or on a file's folder with the file selected.</summary>
    public static void Reveal(string path, bool select) =>
        Start(new ProcessStartInfo("explorer.exe", select ? $"/select,\"{path}\"" : $"\"{path}\"") { UseShellExecute = false });

    static void Start(ProcessStartInfo info)
    {
        try
        {
            Process.Start(info)?.Dispose();
        }
        catch (Exception e) when (e is System.ComponentModel.Win32Exception or InvalidOperationException)
        {
            Log.Error($"Couldn't open {info.FileName} {info.Arguments}", e);
        }
    }
}
