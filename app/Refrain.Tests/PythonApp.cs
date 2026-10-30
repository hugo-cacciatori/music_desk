using System.Collections.Concurrent;
using System.Diagnostics;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Xunit;

namespace Refrain.Tests;

/// <summary>The Python app's answers (SPEC §36 "8.": parity measured, not assumed; "10." step c: kept once it is gone).
/// Each call's answer was recorded from the Python app's own code, run on the same input as the C# port, into
/// <c>Expected/&lt;test class&gt;.json</c>, keyed by the test and the call's place in it. A normal run replays them; with
/// <c>REFRAIN_RECORD=1</c> (and the Python app's <c>.venv</c> and <c>src/</c>, while they existed) they are recorded again.
/// The test's own temporary folders and the repository's folder change from run to run: they are kept as tokens.</summary>
static class PythonApp
{
    public static bool Recording => Environment.GetEnvironmentVariable("REFRAIN_RECORD") == "1";

    static readonly object Gate = new();
    static readonly ConcurrentDictionary<string, int> Calls = new();
    static readonly ConcurrentDictionary<string, Dictionary<string, List<string>>> Files = new();

    static string Repository
    {
        get
        {
            for (var dir = new DirectoryInfo(AppContext.BaseDirectory); dir is not null; dir = dir.Parent)
                if (Directory.Exists(Path.Combine(dir.FullName, "app", "Refrain.Tests")))
                    return dir.FullName;
            throw new DirectoryNotFoundException("The repository wasn't found above the tests.");
        }
    }

    static string ExpectedFolder => Path.Combine(Repository, "app", "Refrain.Tests", "Expected");

    /// <summary>The answer of <paramref name="script"/> to <paramref name="input"/>, as the Python app gave it.</summary>
    public static string Run(string script, string input = "")
    {
        var (file, key) = NextKey();
        var roots = Roots(input);
        if (Recording)
        {
            var output = Execute(script, input);
            Save(file, key, Tokenize(output, roots));
            return output;
        }
        return Detokenize(Load(file, key), roots);
    }

    /// <summary>A value of the C# side, recorded when the Python app proved it right (the files a tool wrote, read
    /// back), and compared with that recording in a normal run. <paramref name="roots"/> are the folders it names.</summary>
    public static string Recorded(string value, params string[] roots)
    {
        var (file, key) = NextKey();
        var map = roots.Select((r, i) => (r, $"<ROOT{i}>")).ToList();
        if (Recording)
        {
            Save(file, key, Tokenize(value, map));
            return value;
        }
        return Detokenize(Load(file, key), map);
    }

    static (string File, string Key) NextKey()
    {
        var test = TestContext.Current.Test ?? throw new InvalidOperationException("No test is running.");
        var name = test.TestDisplayName;
        var file = TestContext.Current.TestClass?.TestClassName.Split('.')[^1] ?? "Tests";
        var index = Calls.AddOrUpdate(file + "|" + name, 0, (_, n) => n + 1);
        return (file, $"{name} #{index}");
    }

    /// <summary>The temporary folders named in the input (each by its first level under the Temp folder), in order,
    /// then the repository: what changes from run to run.</summary>
    static List<(string Path, string Token)> Roots(string input)
    {
        var text = input.Replace(@"\\", @"\");
        var temp = Path.TrimEndingDirectorySeparator(Path.GetTempPath());
        var found = new List<string>();
        foreach (Match match in Regex.Matches(text, Regex.Escape(temp) + @"\\[^\\""\r\n]+", RegexOptions.IgnoreCase))
            if (!found.Contains(match.Value, StringComparer.OrdinalIgnoreCase)) found.Add(match.Value);
        var roots = found.Select((p, i) => (p, $"<TEMP{i}>")).ToList();
        roots.Add((Repository, "<REPO>"));
        return roots;
    }

    static string Escaped(string path) => path.Replace(@"\", @"\\");

    static string Tokenize(string text, List<(string Path, string Token)> roots)
    {
        foreach (var (path, token) in roots)
            text = text.Replace(Escaped(path), token.Replace(">", "|j>"), StringComparison.OrdinalIgnoreCase)
                .Replace(path, token, StringComparison.OrdinalIgnoreCase);
        return text;
    }

    static string Detokenize(string text, List<(string Path, string Token)> roots)
    {
        foreach (var (path, token) in roots)
            text = text.Replace(token.Replace(">", "|j>"), Escaped(path)).Replace(token, path);
        return text;
    }

    static string FilePath(string file) => Path.Combine(ExpectedFolder, file + ".json");

    static Dictionary<string, List<string>> Read(string file) => Files.GetOrAdd(file, f =>
        File.Exists(FilePath(f))
            ? JsonSerializer.Deserialize<Dictionary<string, List<string>>>(File.ReadAllText(FilePath(f))) ?? []
            : []);

    static string Load(string file, string key)
    {
        lock (Gate)
        {
            var all = Read(file);
            var (name, index) = (key[..key.LastIndexOf(" #", StringComparison.Ordinal)], int.Parse(key[(key.LastIndexOf('#') + 1)..]));
            if (!all.TryGetValue(name, out var answers) || index >= answers.Count)
                throw new InvalidOperationException($"No recorded answer for \"{key}\" in Expected/{file}.json.");
            return answers[index];
        }
    }

    static void Save(string file, string key, string value)
    {
        lock (Gate)
        {
            var all = Read(file);
            var (name, index) = (key[..key.LastIndexOf(" #", StringComparison.Ordinal)], int.Parse(key[(key.LastIndexOf('#') + 1)..]));
            if (index == 0) all[name] = [];
            all[name].Add(value);
            Directory.CreateDirectory(ExpectedFolder);
            var sorted = all.OrderBy(kv => kv.Key, StringComparer.Ordinal).ToDictionary(kv => kv.Key, kv => kv.Value);
            File.WriteAllText(FilePath(file), JsonSerializer.Serialize(sorted, new JsonSerializerOptions
            {
                WriteIndented = true,
                Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            }) + "\n", new UTF8Encoding(false));
        }
    }

    /// <summary>Runs the Python app's code: <paramref name="script"/> with <paramref name="input"/> on stdin, through
    /// the repository's <c>.venv</c> with <c>src/</c> on the path. Only while recording.</summary>
    public static string Execute(string script, string input = "")
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
