using System.Collections.Concurrent;
using System.Text;
using System.Text.Json;
using System.Text.RegularExpressions;
using Xunit;

namespace Refrain.Tests;

/// <summary>The Python app's answers (SPEC §36 "8.": parity measured, not assumed; "10." step c: kept once it is gone).
/// Each call's answer was recorded on 2026-10-30 from the Python app's own code (the script each test passes, kept to
/// say how), run on the same input as the C# port, into <c>Expected/&lt;test class&gt;.json</c>, keyed by the test and the
/// call's place in it; the Python app has left the repository since, so they are only replayed. <see cref="Recorded"/>
/// holds C# values recorded when the Python app proved them right; <c>REFRAIN_RECORD=1</c> records those again, after a
/// deliberate change. The test's own temporary folders and the repository's folder change from run to run: they are
/// kept as tokens.</summary>
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

    /// <summary>The answer the Python app gave to <paramref name="script"/> with <paramref name="input"/>.</summary>
    public static string Run(string script, string input = "")
    {
        _ = script;  // recorded from it; kept in the tests to say how each answer was made
        var (file, key) = NextKey();
        return Detokenize(Load(file, key), Roots(input));
    }

    /// <summary>A value of the C# side, recorded when the Python app proved it right (the files a tool wrote, read
    /// back), and compared with that recording in a normal run. <paramref name="roots"/> are the folders it names.</summary>
    public static string Recorded(string value, params string[] roots)
    {
        var (file, key) = NextKey();
        var map = roots.Select((r, i) => (r, $"<ROOT{i}>")).Append((Repository, "<REPO>")).ToList();  // rsgain's path, in logs
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
            if (!all.TryGetValue(name, out var answers)) all[name] = answers = [];
            // The value at its place: a test's replayed Python answers keep theirs, and a new recording replaces the old.
            if (index < answers.Count) answers[index] = value;
            else answers.Add(value);
            Directory.CreateDirectory(ExpectedFolder);
            var sorted = all.OrderBy(kv => kv.Key, StringComparer.Ordinal).ToDictionary(kv => kv.Key, kv => kv.Value);
            File.WriteAllText(FilePath(file), JsonSerializer.Serialize(sorted, new JsonSerializerOptions
            {
                WriteIndented = true,
                Encoder = System.Text.Encodings.Web.JavaScriptEncoder.UnsafeRelaxedJsonEscaping,
            }) + "\n", new UTF8Encoding(false));
        }
    }
}
