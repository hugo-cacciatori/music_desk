using System.Text.Json;
using Microsoft.Data.Sqlite;
using Refrain.Core;
using Xunit;

namespace Refrain.Tests;

/// <summary>The licences folder (SPEC §13, §36 "10."): every package the app ships is listed at its version, and every
/// file the list names is in <c>resources/licenses/</c>, as every file there is in the list.</summary>
public sealed class LicenseTests
{
    /// <summary>Packages that only build the app: nothing of theirs is in the published folder.</summary>
    static readonly string[] BuildOnly = ["Microsoft.NET.ILLink.Tasks", "Microsoft.Windows.SDK.BuildTools", "Microsoft.Windows.SDK.BuildTools.MSIX"];

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

    [Fact]
    public void Every_shipped_package_is_listed_at_its_version()
    {
        // The app's restore (its packages and Refrain.Core's), as the published build takes them.
        using var assets = JsonDocument.Parse(File.ReadAllText(Path.Combine(Repository, "app", "Refrain", "obj", "project.assets.json")));
        var packages = assets.RootElement.GetProperty("libraries").EnumerateObject()
            .Where(p => p.Value.GetProperty("type").GetString() == "package")
            .Select(p => p.Name.Split('/'))
            .Where(p => !BuildOnly.Contains(p[0], StringComparer.OrdinalIgnoreCase))
            .ToList();
        Assert.NotEmpty(packages);
        foreach (var package in packages)
        {
            var component = Licenses.Components.SingleOrDefault(c => c.Packages.Contains(package[0], StringComparer.OrdinalIgnoreCase));
            Assert.True(component is not null, $"{package[0]} {package[1]} is shipped but not listed");
            Assert.True(component.Version == package[1], $"{component.Name} is listed as {component.Version}, the build has {package[1]}");
        }
    }

    [Fact]
    public void Sqlite_is_listed_at_the_version_the_app_runs()
    {
        using var conn = new SqliteConnection("Data Source=:memory:");
        conn.Open();
        using var command = conn.CreateCommand();
        command.CommandText = "SELECT sqlite_version()";
        var version = (string)command.ExecuteScalar()!;
        Assert.StartsWith(Licenses.Components.Single(c => c.Name.StartsWith("SQLite ")).Version + ".", version + ".");
    }

    [Fact]
    public void The_folder_holds_exactly_the_listed_files()
    {
        var folder = Path.Combine(Repository, "resources", "licenses");
        var present = Directory.GetFiles(folder).Select(Path.GetFileName).Order(StringComparer.OrdinalIgnoreCase);
        Assert.Equal(Licenses.Files().Order(StringComparer.OrdinalIgnoreCase), present);
        var readme = File.ReadAllText(Path.Combine(folder, "README.txt"));
        foreach (var file in Licenses.Components.SelectMany(c => c.Files).Append("GPL-3.0-exception.txt"))
            Assert.Contains(file, readme);
    }
}
