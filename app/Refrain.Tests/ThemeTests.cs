using System.Text.RegularExpressions;
using Xunit;

namespace Refrain.Tests;

/// <summary>One theme module, the only source of the look (SPEC §36 "11."): no other file of the app defines a colour or a
/// window backdrop.</summary>
public sealed class ThemeTests
{
    [Fact]
    public void Only_the_theme_module_defines_colours_and_backdrops()
    {
        var app = Path.Combine(Repository, "app", "Refrain");
        var look = new Regex(@"Color\.FromArgb|ColorHelper\.|SystemBackdrop|MicaBackdrop|DesktopAcrylic");
        var others = Directory.GetFiles(app, "*.cs", SearchOption.AllDirectories)
            .Where(f => !f.Contains(Path.DirectorySeparatorChar + "obj" + Path.DirectorySeparatorChar) &&
                        !f.Contains(Path.DirectorySeparatorChar + "bin" + Path.DirectorySeparatorChar) &&
                        Path.GetFileName(f) != "Theme.cs")
            .Where(f => look.IsMatch(File.ReadAllText(f)))
            .Select(Path.GetFileName);
        Assert.Empty(others);
    }

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
}
