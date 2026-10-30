namespace Refrain.Core;

/// <summary>A third-party component shipped with the app: what About lists, with its licence's files in the licences
/// folder. <see cref="Packages"/>: the NuGet packages it covers, so a test notices a package or version that changed.</summary>
public sealed record Component(string Name, string Version, string Licence, string[] Files, string[] Packages);

/// <summary>The licences folder (SPEC §13, §36 "10."): Refrain's own licence and every bundled component's, from
/// <c>resources/licenses/</c>, copied beside the app as <c>licenses\</c>; its README.txt says the same in words.</summary>
public static class Licenses
{
    public static string Folder => Path.Combine(AppContext.BaseDirectory, "licenses");

    public const string Own = "GPL-3.0.txt";

    public static readonly Component[] Components =
    [
        new(".NET runtime (Native AOT)", "10.0.14", "MIT", ["dotnet-MIT.txt", "dotnet-THIRD-PARTY-NOTICES.txt"], ["Microsoft.DotNet.ILCompiler", "runtime.win-x64.Microsoft.DotNet.ILCompiler"]),
        new("Windows App SDK (WinUI 3)", "2.6.4", "Microsoft Software Licence Terms",
            ["WindowsAppSDK-license.txt", "WindowsAppSDK-NOTICE.txt", "WinUI-NOTICE.txt"],
            ["Microsoft.WindowsAppSDK.WinUI", "Microsoft.WindowsAppSDK.Base", "Microsoft.WindowsAppSDK.Foundation",
             "Microsoft.WindowsAppSDK.InteractiveExperiences"]),
        new("WebView2 loader (with the Windows App SDK)", "1.0.3719.77", "BSD-3-Clause", ["WebView2-BSD.txt"], ["Microsoft.Web.WebView2"]),
        new("TagLib#", "2.3.0", "LGPL-2.1", ["LGPL-2.1.txt"], ["TagLibSharp"]),
        new("Microsoft.Data.Sqlite", "10.0.14", "MIT", ["dotnet-MIT.txt"], ["Microsoft.Data.Sqlite", "Microsoft.Data.Sqlite.Core"]),
        new("SQLitePCLRaw", "2.1.13", "Apache-2.0", ["SQLitePCLRaw-Apache-2.0.txt"],
            ["SQLitePCLRaw.bundle_e_sqlite3", "SQLitePCLRaw.core", "SQLitePCLRaw.lib.e_sqlite3", "SQLitePCLRaw.provider.e_sqlite3"]),
        new("SQLite (in SQLitePCLRaw)", "3.53.3", "Public domain", ["SQLite-public-domain.txt"], []),
        new("rsgain", "3.6", "BSD-2-Clause", ["rsgain-BSD-2-Clause.txt"], []),
        new("CRC++ (in rsgain)", "", "BSD-3-Clause", ["CRCpp-BSD.txt"], []),
        new("FFmpeg (in rsgain)", "7.1", "LGPL-2.1-or-later", ["LGPL-2.1.txt"], []),
        new("Fraunhofer FDK AAC (in rsgain)", "", "FDK AAC licence", ["FDK-AAC-NOTICE.txt"], []),
        new("flac", "1.5.0", "GPL-2.0-or-later", ["flac-GPL-2.0.txt"], []),
        new("libFLAC (with flac)", "1.5.0", "BSD-3-Clause (Xiph.Org)", ["libFLAC-Xiph-BSD.txt"], []),
    ];

    /// <summary>Every file the list names, Refrain's own licence and the README first.</summary>
    public static IEnumerable<string> Files() =>
        new[] { Own, "README.txt" }.Concat(Components.SelectMany(c => c.Files)).Distinct();
}
