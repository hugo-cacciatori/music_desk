using System.Diagnostics;
using System.Runtime.InteropServices;

namespace RefrainHere;

/// <summary>Started by File Explorer's address bar with the shown folder as its working directory (SPEC §29): only
/// this start opens the working directory, so a start of Refrain.exe from anywhere else behaves as usual.</summary>
static partial class Program
{
    [LibraryImport("user32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool AllowSetForegroundWindow(int processId);

    static int Main()
    {
        var folder = Environment.CurrentDirectory;
        var app = Path.Combine(AppContext.BaseDirectory, "Refrain.exe");
        AllowSetForegroundWindow(-1);  // ASFW_ANY: Refrain may take the foreground
        try
        {
            var start = new ProcessStartInfo(app) { UseShellExecute = false, WorkingDirectory = AppContext.BaseDirectory };
            start.ArgumentList.Add(folder);
            Process.Start(start)?.Dispose();
            return 0;
        }
        catch (System.ComponentModel.Win32Exception)
        {
            return 1;
        }
    }
}
