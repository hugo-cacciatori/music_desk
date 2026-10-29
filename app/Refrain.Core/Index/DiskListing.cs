namespace Refrain.Core.Index;

/// <summary>Lists one folder on disk for the scanner. Junctions and symbolic links to folders aren't followed
/// (SPEC §4); a folder that can't be listed gives null, so its index is kept.</summary>
public static class DiskListing
{
    static readonly DateTime Epoch = DateTime.UnixEpoch;

    static readonly EnumerationOptions Options = new()
    {
        IgnoreInaccessible = true,
        AttributesToSkip = 0,
        RecurseSubdirectories = false,
        ReturnSpecialDirectories = false,
    };

    public static List<DiskEntry>? List(string folder)
    {
        try
        {
            var entries = new List<DiskEntry>();
            foreach (var info in new DirectoryInfo(folder).EnumerateFileSystemInfos("*", Options))
            {
                var isDir = (info.Attributes & FileAttributes.Directory) != 0;
                if (isDir && (info.Attributes & FileAttributes.ReparsePoint) != 0) continue;
                var mtime = (info.LastWriteTimeUtc - Epoch).TotalSeconds;
                entries.Add(isDir
                    ? new DiskEntry(info.Name, true, null, null, mtime)
                    : new DiskEntry(info.Name, false, Ext(info.Name), ((FileInfo)info).Length, mtime));
            }
            return entries;
        }
        catch (Exception e) when (e is IOException or UnauthorizedAccessException or System.Security.SecurityException)
        {
            return null;
        }
    }

    static string? Ext(string name)
    {
        var ext = Path.GetExtension(name);
        return ext.Length > 1 ? ext.ToLowerInvariant() : null;
    }
}
