using System.Diagnostics;
using System.Text;

namespace Refrain.Core.Cue;

/// <summary>flac failed or was stopped; the message is flac's own when it gave one.</summary>
public class FlacException(string message) : Exception(message);

public sealed class FlacCancelledException() : FlacException("Stopped.");

/// <summary>The bundled flac.exe (SPEC §13, §22), ported from the Python app's <c>core/flac.py</c>: cutting a track,
/// and decoding to raw PCM for the proof and the silence check. It runs as rsgain does: no console window, below
/// normal priority, killed on Stop. A range of any source is read by flac itself (<c>flac -c -0</c> piped into
/// <c>flac -d</c>), exactly as the tracks were cut from it.</summary>
public sealed class Flac(string exe)
{
    const int Chunk = 1 << 20;  // bytes of raw PCM read at a time; Stop is checked between chunks
    static readonly string[] Raw = ["--force-raw-format", "--endian=little", "--sign=signed"];  // what STREAMINFO's MD5 hashes

    /// <summary>flac.exe beside the app (never configurable).</summary>
    public static string Exe => Path.Combine(AppContext.BaseDirectory, "flac", "flac.exe");

    Process Start(IEnumerable<string> args, bool input = false)
    {
        var start = new ProcessStartInfo(exe)
        {
            UseShellExecute = false,
            CreateNoWindow = true,
            RedirectStandardInput = input,
            RedirectStandardOutput = true,
            RedirectStandardError = true,
        };
        foreach (var arg in args) start.ArgumentList.Add(arg);
        var process = Process.Start(start) ?? throw new FlacException("flac couldn't be started");
        try
        {
            process.PriorityClass = ProcessPriorityClass.BelowNormal;
        }
        catch (Exception e) when (e is InvalidOperationException or System.ComponentModel.Win32Exception)
        {
        }
        return process;
    }

    /// <summary>flac's last meaningful line.</summary>
    static string Message(string stderr) =>
        stderr.Split('\n').Select(l => l.Trim()).LastOrDefault(l => l.Length > 0) ?? "flac failed without a message";

    static void Kill(Process process)
    {
        try
        {
            if (!process.HasExited) process.Kill();
            process.WaitForExit();
        }
        catch (InvalidOperationException)
        {
        }
    }

    /// <summary>Cuts samples [start, end) of <paramref name="source"/> into <paramref name="output"/> (FLAC, -8,
    /// verified while encoding with -V).</summary>
    public void Encode(string source, long start, long? end, string output, CancellationToken cancel = default)
    {
        List<string> args = ["-8", "-V", "--silent", $"--skip={start}"];
        if (end is { } until) args.Add($"--until={until}");
        args.AddRange(["-o", output, source]);
        using var process = Start(args);
        process.StandardOutput.Close();
        var stderr = process.StandardError.ReadToEndAsync(CancellationToken.None);
        while (!process.WaitForExit(100))
        {
            if (!cancel.IsCancellationRequested) continue;
            Kill(process);
            throw new FlacCancelledException();
        }
        process.WaitForExit();
        if (process.ExitCode != 0) throw new FlacException(Message(stderr.Result));
    }

    /// <summary>A FLAC file's samples as raw PCM (little-endian, signed, interleaved), in chunks.</summary>
    public IEnumerable<byte[]> Decode(string path, CancellationToken cancel = default) =>
        Stream([["-d", "-c", "--silent", .. Raw, path]], cancel);

    /// <summary>Samples [start, end) of any source flac reads, as raw PCM, through flac itself.</summary>
    public IEnumerable<byte[]> DecodeRange(string source, long start = 0, long? end = null, CancellationToken cancel = default)
    {
        List<string> encode = ["-c", "-0", "--silent", $"--skip={start}"];
        if (end is { } until) encode.Add($"--until={until}");
        encode.Add(source);
        return Stream([encode, ["-d", "-c", "--silent", .. Raw, "-"]], cancel);
    }

    IEnumerable<byte[]> Stream(List<List<string>> commands, CancellationToken cancel)
    {
        var processes = new List<Process>();
        var errors = new List<Task<string>>();
        Task? pump = null;
        try
        {
            foreach (var command in commands)
            {
                var process = Start(command, input: processes.Count > 0);
                errors.Add(process.StandardError.ReadToEndAsync(CancellationToken.None));
                if (processes.Count > 0)
                {
                    // The previous process's output feeds this one's input.
                    var from = processes[^1].StandardOutput.BaseStream;
                    var to = process.StandardInput.BaseStream;
                    pump = Task.Run(() =>
                    {
                        try
                        {
                            from.CopyTo(to, Chunk);
                        }
                        catch (IOException)
                        {
                        }
                        finally
                        {
                            to.Close();
                        }
                    }, CancellationToken.None);
                }
                processes.Add(process);
            }
            var output = processes[^1].StandardOutput.BaseStream;
            var buffer = new byte[Chunk];
            while (true)
            {
                var filled = 0;
                int read;
                while (filled < Chunk && (read = output.Read(buffer, filled, Chunk - filled)) > 0) filled += read;
                if (filled == 0) break;
                if (cancel.IsCancellationRequested) throw new FlacCancelledException();
                yield return buffer[..filled];
                if (filled < Chunk) break;
            }
            pump?.Wait(CancellationToken.None);
            foreach (var process in processes) process.WaitForExit();
            for (var i = 0; i < processes.Count; i++)
                if (processes[i].ExitCode != 0) throw new FlacException(Message(errors[i].Result));
        }
        finally
        {
            foreach (var process in processes)
            {
                Kill(process);
                process.Dispose();
            }
        }
    }

    /// <summary>"flac 1.5.0", or null when flac can't run.</summary>
    public static string? Version(string exe)
    {
        try
        {
            using var process = Process.Start(new ProcessStartInfo(exe, "--version")
            {
                UseShellExecute = false, CreateNoWindow = true, RedirectStandardOutput = true, RedirectStandardError = true,
                StandardOutputEncoding = Encoding.UTF8,
            })!;
            var output = process.StandardOutput.ReadToEndAsync();
            _ = process.StandardError.ReadToEndAsync();
            if (!process.WaitForExit(10_000))
            {
                Kill(process);
                return null;
            }
            var first = output.Result.Trim().Split('\n').FirstOrDefault()?.Trim();
            return process.ExitCode == 0 && !string.IsNullOrEmpty(first) ? first : null;
        }
        catch (System.ComponentModel.Win32Exception e)
        {
            Log.Error($"flac self-check failed for {exe}", e);
            return null;
        }
    }
}
