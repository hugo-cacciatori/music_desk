using System.IO.Pipes;
using System.Text;
using System.Text.Json;
using System.Text.Json.Serialization;

namespace Refrain.Core;

/// <summary>What a start asks for (SPEC §31, §36 "11."): a right-click entry and its paths. A start without one
/// (Refrain.exe on its own, or with a bare path) asks for nothing.</summary>
public sealed record StartRequest(
    [property: JsonPropertyName("entry")] string? Entry,
    [property: JsonPropertyName("paths")] IReadOnlyList<string> Paths)
{
    public static readonly StartRequest None = new(null, []);
    public bool IsEmpty => Entry is null;

    /// <summary><c>Refrain.exe --entry --list file</c> (the right-click menu writes the paths one per line, UTF-8, into
    /// the data folder's <c>menu\</c>; the file is read, then deleted, and only a file in that folder), or
    /// <c>--entry path…</c> (a run from source, tests).</summary>
    public static StartRequest Parse(IReadOnlyList<string> args)
    {
        string? entry = null;
        var paths = new List<string>();
        for (var i = 0; i < args.Count; i++)
        {
            var arg = args[i];
            if (arg == "--list" && i + 1 < args.Count)
                paths.AddRange(ReadList(args[++i]));
            else if (arg.StartsWith("--", StringComparison.Ordinal))
                entry ??= arg[2..];
            else
                paths.Add(arg.Trim('"'));
        }
        if (entry is null) return None;
        return new StartRequest(entry, paths);
    }

    public static string MenuFolder => Path.Combine(AppPaths.DataFolder, "menu");

    static List<string> ReadList(string file)
    {
        var full = Path.GetFullPath(file);
        if (!RealPath.IsInside(full, MenuFolder) || !File.Exists(full)) return [];
        var lines = File.ReadAllLines(full, Encoding.UTF8).Where(l => l.Trim().Length > 0).ToList();
        try
        {
            File.Delete(full);
        }
        catch (IOException e)
        {
            Log.Info($"Couldn't remove the menu's list {full}: {e.Message}");
        }
        return lines;
    }
}

[JsonSerializable(typeof(StartRequest))]
internal sealed partial class InstanceJson : JsonSerializerContext;

/// <summary>One Refrain at a time (SPEC §29): the first start listens on a pipe of its own user; a later start hands
/// its request over and exits. The preview's pipe has its own name, so it never talks to the Python app.</summary>
public sealed class Instance : IDisposable
{
    /// <summary>A pipe per user; another data folder (REFRAIN_DATA: tests, an agent's checks) gets a pipe of its own,
    /// so such a copy never hands over to the user's running Refrain.</summary>
    public static string DefaultPipe
    {
        get
        {
            var data = Environment.GetEnvironmentVariable("REFRAIN_DATA");
            var name = AppPaths.Release ? "RefrainApp" : "RefrainNext";  // never the Python app's pipe
            if (string.IsNullOrEmpty(data)) return $"{name}-{Environment.UserName}";
            var hash = System.Security.Cryptography.SHA256.HashData(Encoding.UTF8.GetBytes(Path.GetFullPath(data).ToUpperInvariant()));
            return $"{name}-{Environment.UserName}-{Convert.ToHexString(hash)[..12]}";
        }
    }

    readonly string pipe;
    readonly NamedPipeServerStream first;
    readonly CancellationTokenSource stop = new();

    /// <summary>A later start's request (raised on a pipe thread).</summary>
    public event Action<StartRequest>? Received;

    Instance(string pipe, NamedPipeServerStream first)
    {
        this.pipe = pipe;
        this.first = first;
        _ = Listen();
    }

    /// <summary>The running instance's server, or null when another Refrain already listens.</summary>
    public static Instance? TryStart(string? pipe = null)
    {
        pipe ??= DefaultPipe;
        try
        {
            return new Instance(pipe, Server(pipe, firstInstance: true));
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException)
        {
            return null;
        }
    }

    static NamedPipeServerStream Server(string pipe, bool firstInstance) =>
        new(pipe, PipeDirection.In, NamedPipeServerStream.MaxAllowedServerInstances, PipeTransmissionMode.Byte,
            PipeOptions.Asynchronous | PipeOptions.CurrentUserOnly | (firstInstance ? PipeOptions.FirstPipeInstance : 0));

    async Task Listen()
    {
        var server = first;
        while (!stop.IsCancellationRequested)
        {
            NamedPipeServerStream? next = null;
            try
            {
                await server.WaitForConnectionAsync(stop.Token);
                next = Server(pipe, firstInstance: false);  // the next start finds a listener at once
                using var reader = new StreamReader(server, Encoding.UTF8);
                var line = await reader.ReadLineAsync(stop.Token);
                if (line is not null && JsonSerializer.Deserialize(line, InstanceJson.Default.StartRequest) is { } request)
                {
                    var received = request with { Paths = request.Paths ?? [] };
                    _ = Task.Run(() => Received?.Invoke(received));  // never on the listening loop
                }
            }
            catch (OperationCanceledException)
            {
                next?.Dispose();
                return;
            }
            catch (Exception e) when (e is IOException or JsonException)
            {
                Log.Info($"A hand-over couldn't be read: {e.Message}");
            }
            finally
            {
                await server.DisposeAsync();
            }
            if (stop.IsCancellationRequested)
            {
                next?.Dispose();
                return;
            }
            server = next ?? Server(pipe, firstInstance: false);
        }
    }

    /// <summary>Hands a request to the running Refrain; false when none answers within half a second.</summary>
    public static bool HandOver(StartRequest request, string? pipe = null)
    {
        try
        {
            using var client = new NamedPipeClientStream(".", pipe ?? DefaultPipe, PipeDirection.Out, PipeOptions.CurrentUserOnly);
            client.Connect(500);
            using var writer = new StreamWriter(client, new UTF8Encoding(false));
            writer.WriteLine(JsonSerializer.Serialize(request, InstanceJson.Default.StartRequest));
            writer.Flush();
            return true;
        }
        catch (Exception e) when (e is TimeoutException or IOException or UnauthorizedAccessException)
        {
            return false;
        }
    }

    public void Dispose() => stop.Cancel();
}
