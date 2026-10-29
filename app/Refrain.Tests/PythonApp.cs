using System.Diagnostics;
using System.Text;

namespace Refrain.Tests;

/// <summary>The Python app's own code, run on the same input as the C# port (SPEC §36 "8.": parity measured, not
/// assumed). It uses the repository's <c>.venv</c> and <c>src/</c>; the script reads JSON on stdin and prints JSON.</summary>
static class PythonApp
{
    static string Repository
    {
        get
        {
            for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
                if (File.Exists(Path.Combine(dir.FullName, "pyproject.toml")) && Directory.Exists(Path.Combine(dir.FullName, "src", "refrain")))
                    return dir.FullName;
            throw new DirectoryNotFoundException("The Python app's repository wasn't found above the tests.");
        }
    }

    /// <summary>Runs <paramref name="script"/> with <paramref name="input"/> on stdin and returns stdout. Throws with
    /// stderr when it fails.</summary>
    public static string Run(string script, string input = "")
    {
        var repo = Repository;
        var start = new ProcessStartInfo(Path.Combine(repo, ".venv", "Scripts", "python.exe"))
        {
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            UseShellExecute = false,
            CreateNoWindow = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
            WorkingDirectory = repo,
        };
        start.ArgumentList.Add("-c");
        start.ArgumentList.Add("import sys; sys.stdin.reconfigure(encoding='utf-8'); sys.stdout.reconfigure(encoding='utf-8')\n" + script);
        start.Environment["PYTHONPATH"] = Path.Combine(repo, "src");
        using var process = Process.Start(start)!;
        process.StandardInput.Write(input);
        process.StandardInput.Close();
        var output = process.StandardOutput.ReadToEnd();
        var error = process.StandardError.ReadToEnd();
        process.WaitForExit();
        if (process.ExitCode != 0) throw new InvalidOperationException($"The Python app failed:\n{error}");
        return output;
    }
}
