using System.Diagnostics;
using System.Text;
using System.Text.RegularExpressions;

namespace Refrain.Core.ReplayGain;

/// <summary>What an rsgain run gave. <see cref="Error"/>: "not found" or "command line too long", when it didn't
/// run.</summary>
public sealed record ScanResult(int? ExitCode, string Stdout, string Stderr, double Duration, bool Cancelled = false,
    string? Error = null)
{
    public bool Ok => Error is null && !Cancelled && ExitCode == 0;
}

/// <summary>Scans files (names relative to the folder) as one album and writes their ReplayGain tags.</summary>
public delegate ScanResult Scanner(IReadOnlyList<string> files, string folder, string target, string exe,
    CancellationToken cancel, Action<string>? onLine);

/// <summary>The bundled rsgain (SPEC §13, Ground rule 5), ported from the Python app's <c>core/rsgain.py</c>: run with
/// the album folder as its working directory and relative names, below normal priority, without a console window;
/// its output read as it comes; a cancel kills it.</summary>
public static partial class Rsgain
{
    // CreateProcess's limit is 32,767 characters; stay a little under it.
    const int MaxCommandLine = 32_000;
    static readonly string[] ScanArgs = ["custom", "-s", "i"];  // scan and write the tags

    /// <summary>rsgain.exe beside the app (Ground rule 5: never configurable).</summary>
    public static string Exe => Path.Combine(AppContext.BaseDirectory, "rsgain", "rsgain.exe");

    [GeneratedRegex(@"\x1b\[[0-9;]*m")]
    private static partial Regex Ansi();

    /// <summary>Without the terminal colour codes.</summary>
    public static string StripAnsi(string? text) => Ansi().Replace(text ?? "", "").Trim();

    /// <summary>Python's <c>subprocess.list2cmdline</c>: the command line Windows gets.</summary>
    public static string CommandLine(IEnumerable<string> args)
    {
        var line = new StringBuilder();
        foreach (var arg in args)
        {
            if (line.Length > 0) line.Append(' ');
            var quote = arg.Length == 0 || arg.Any(c => c is ' ' or '\t');
            if (quote) line.Append('"');
            var backslashes = 0;
            foreach (var c in arg)
            {
                if (c == '\\')
                {
                    backslashes++;
                    continue;
                }
                line.Append('\\', c == '"' ? backslashes * 2 + 1 : backslashes);
                backslashes = 0;
                line.Append(c);
            }
            line.Append('\\', quote ? backslashes * 2 : backslashes);
            if (quote) line.Append('"');
        }
        return line.ToString();
    }

    public static ScanResult Scan(IReadOnlyList<string> files, string folder, string target, string exe,
        CancellationToken cancel, Action<string>? onLine)
    {
        List<string> command = [exe, .. ScanArgs, "-l", target, .. files];
        if (CommandLine(command).Length > MaxCommandLine) return new ScanResult(null, "", "", 0, Error: "command line too long");

        var start = new ProcessStartInfo(exe)
        {
            WorkingDirectory = folder,
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = true,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
            StandardOutputEncoding = Encoding.UTF8,
            StandardErrorEncoding = Encoding.UTF8,
        };
        foreach (var arg in command.Skip(1)) start.ArgumentList.Add(arg);
        var clock = Stopwatch.StartNew();
        Process process;
        try
        {
            process = Process.Start(start)!;
        }
        catch (System.ComponentModel.Win32Exception)
        {
            return new ScanResult(null, "", "", 0, Error: "not found");
        }
        using (process)
        {
            try
            {
                process.PriorityClass = ProcessPriorityClass.BelowNormal;
            }
            catch (Exception e) when (e is InvalidOperationException or System.ComponentModel.Win32Exception)
            {
                // It ended already.
            }
            process.StandardInput.Close();
            var stdout = new StringBuilder();
            var stderr = new StringBuilder();
            process.OutputDataReceived += (_, e) =>
            {
                if (e.Data is null) return;
                lock (stdout) stdout.Append(e.Data).Append('\n');
                var text = StripAnsi(e.Data);
                if (text.Length > 0) onLine?.Invoke(text);
            };
            process.ErrorDataReceived += (_, e) =>
            {
                if (e.Data is null) return;
                lock (stderr) stderr.Append(e.Data).Append('\n');
            };
            process.BeginOutputReadLine();
            process.BeginErrorReadLine();

            var cancelled = false;
            while (!process.WaitForExit(100))
            {
                if (!cancel.IsCancellationRequested) continue;
                try
                {
                    process.Kill();
                }
                catch (InvalidOperationException)
                {
                }
                cancelled = true;
                break;
            }
            process.WaitForExit();  // the output is read to its end
            Log.Info($"rsgain {string.Join(" ", ScanArgs)} in {folder}: exit {process.ExitCode}{(cancelled ? " (cancelled)" : "")}");
            return new ScanResult(process.ExitCode, stdout.ToString(), stderr.ToString(), clock.Elapsed.TotalSeconds, cancelled);
        }
    }

    /// <summary>The first line of <c>rsgain --version</c> ("rsgain 3.6"), or null when it can't run.</summary>
    public static string? Version(string exe)
    {
        try
        {
            var start = new ProcessStartInfo(exe, "--version")
            {
                UseShellExecute = false,
                CreateNoWindow = true,
                RedirectStandardOutput = true,
                RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
            };
            using var process = Process.Start(start)!;
            var output = process.StandardOutput.ReadToEndAsync();
            _ = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(10_000))
            {
                process.Kill();
                return null;
            }
            if (process.ExitCode != 0) return null;
            var first = StripAnsi(output.Result).Split('\n').FirstOrDefault()?.Trim();
            return string.IsNullOrEmpty(first) ? null : (first.EndsWith("- using:") ? first[..^"- using:".Length] : first).Trim();
        }
        catch (System.ComponentModel.Win32Exception e)
        {
            Log.Error($"rsgain self-check failed for {exe}", e);
            return null;
        }
    }
}
