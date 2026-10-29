using System.Runtime.InteropServices;

namespace Refrain.Core;

/// <summary>File Explorer's order ("Track 2" before "Track 10"): Windows' own comparison, as the Python app's
/// natsort did on Windows.</summary>
public sealed partial class NaturalOrder : IComparer<string?>
{
    public static readonly NaturalOrder Instance = new();

    [LibraryImport("shlwapi.dll", EntryPoint = "StrCmpLogicalW", StringMarshalling = StringMarshalling.Utf16)]
    private static partial int StrCmpLogical(string a, string b);

    public int Compare(string? a, string? b)
    {
        if (ReferenceEquals(a, b)) return 0;
        if (a is null) return -1;
        if (b is null) return 1;
        return StrCmpLogical(a, b);
    }
}
