using System.Runtime.InteropServices;
using Microsoft.Win32.SafeHandles;

namespace Refrain.Core;

/// <summary>The folder Windows really opens for a path (SPEC §3 "The same folder under another spelling"): a mapped
/// drive and its network path, a subst drive, a junction or link, or a short name all resolve to one spelling.</summary>
public static partial class RealPath
{
    const uint FileReadAttributes = 0x80;
    const uint ShareAll = 0x7;
    const uint OpenExisting = 3;
    const uint BackupSemantics = 0x02000000;  // needed to open a folder

    [LibraryImport("kernel32.dll", EntryPoint = "CreateFileW", StringMarshalling = StringMarshalling.Utf16,
        SetLastError = true)]
    private static partial SafeFileHandle CreateFile(string name, uint access, uint share, nint security,
        uint disposition, uint flags, nint template);

    [LibraryImport("kernel32.dll", EntryPoint = "GetFinalPathNameByHandleW", SetLastError = true)]
    private static unsafe partial uint GetFinalPathNameByHandle(SafeFileHandle file, char* buffer, uint size,
        uint flags);

    /// <summary>The resolved path, or null when Windows can't open it (offline, missing, no access).</summary>
    public static unsafe string? Of(string path)
    {
        using var handle = CreateFile(path, FileReadAttributes, ShareAll, 0, OpenExisting, BackupSemantics, 0);
        if (handle.IsInvalid) return null;
        var buffer = new char[1024];
        uint length;
        fixed (char* p = buffer)
            length = GetFinalPathNameByHandle(handle, p, (uint)buffer.Length, 0);
        if (length == 0 || length >= buffer.Length) return null;
        var result = new string(buffer, 0, (int)length);
        if (result.StartsWith(@"\\?\UNC\", StringComparison.Ordinal)) return @"\\" + result[8..];
        if (result.StartsWith(@"\\?\", StringComparison.Ordinal)) return result[4..];
        return result;
    }

    /// <summary>Whether two paths are one file on disk (a case-only rename's target is its own source).</summary>
    public static bool SameFile(string a, string b) =>
        Of(a) is { } first && Of(b) is { } second && first.Equals(second, StringComparison.OrdinalIgnoreCase);

    /// <summary>A path without a trailing separator (but "D:\" stays a drive root).</summary>
    public static string Normalize(string path)
    {
        var full = Path.GetFullPath(path.Trim().Trim('"'));
        var root = Path.GetPathRoot(full);
        return full.Length > (root?.Length ?? 0) ? full.TrimEnd('\\', '/') : full;
    }

    /// <summary>Whether <paramref name="path"/> is <paramref name="folder"/> or lies inside it, ignoring case.</summary>
    public static bool IsInside(string path, string folder)
    {
        if (path.Equals(folder, StringComparison.OrdinalIgnoreCase)) return true;
        var prefix = folder.EndsWith('\\') ? folder : folder + "\\";
        return path.StartsWith(prefix, StringComparison.OrdinalIgnoreCase);
    }
}
