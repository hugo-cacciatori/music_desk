using System.Runtime.InteropServices;
using Microsoft.Win32;
using Refrain.Core.ReplayGain;
using Refrain.Core.Tidy;

namespace Refrain.Core;

/// <summary>Windows can't recycle this item: nothing was deleted (SPEC §19).</summary>
public sealed class CannotRecycleException(string reason) : IOException(reason);

/// <summary>The Recycle Bin (Ground rule 1, SPEC §19): the app never deletes anything permanently. Ported from the
/// Python app's <c>fsops.can_recycle</c> and <c>send_to_recycle_bin</c>.</summary>
public static partial class Recycle
{
    const uint DriveFixed = 3;
    static readonly Dictionary<uint, string> DriveKinds = new()
    {
        [0] = "a drive of an unknown kind", [1] = "not a drive", [2] = "a removable drive", [4] = "a network location",
        [5] = "a CD or DVD drive", [6] = "a RAM disk",
    };

    [LibraryImport("kernel32.dll", EntryPoint = "GetVolumePathNameW", StringMarshalling = StringMarshalling.Utf16)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool GetVolumePathName(string path, [Out] char[] buffer, uint length);

    [LibraryImport("kernel32.dll", EntryPoint = "GetDriveTypeW", StringMarshalling = StringMarshalling.Utf16)]
    private static partial uint GetDriveType(string root);

    [LibraryImport("kernel32.dll", EntryPoint = "GetVolumeNameForVolumeMountPointW", StringMarshalling = StringMarshalling.Utf16)]
    [return: MarshalAs(UnmanagedType.Bool)]
    private static partial bool GetVolumeName(string root, [Out] char[] buffer, uint length);

    [StructLayout(LayoutKind.Sequential)]
    struct QueryInfo
    {
        public int Size;
        public long Bytes;
        public long Items;
    }

    [LibraryImport("shell32.dll", EntryPoint = "SHQueryRecycleBinW", StringMarshalling = StringMarshalling.Utf16)]
    private static partial int SHQueryRecycleBin(string root, ref QueryInfo info);

    [StructLayout(LayoutKind.Sequential)]
    struct FileOperation
    {
        public nint Window;
        public uint Function;
        public nint From;  // double-NUL-terminated
        public nint To;
        public ushort Flags;
        public int AnyAborted;
        public nint NameMappings;
        public nint ProgressTitle;
    }

    [LibraryImport("shell32.dll", EntryPoint = "SHFileOperationW")]
    private static partial int SHFileOperation(ref FileOperation operation);

    /// <summary>Null when Windows would send <paramref name="path"/> to the Recycle Bin, else why not, in a few
    /// words. Measured (SPEC §19): a network path isn't a fixed drive and fails SHQueryRecycleBin. Inferred from the
    /// bin's own settings: NukeOnDelete=1 turns it off, and a file bigger than MaxCapacity (MB) is deleted at once.</summary>
    public static string? CanRecycle(string path)
    {
        var buffer = new char[1024];
        if (!GetVolumePathName(path, buffer, (uint)buffer.Length)) return $"Windows can't tell which drive {path} is on";
        var root = new string(buffer).TrimEnd('\0');
        var kind = GetDriveType(root);
        if (kind != DriveFixed) return $"{root} is {DriveKinds.GetValueOrDefault(kind, "a drive of an unknown kind")}, which has no Recycle Bin";
        var info = new QueryInfo { Size = Marshal.SizeOf<QueryInfo>() };
        if (SHQueryRecycleBin(root, ref info) != 0) return $"{root} has no Recycle Bin";
        if (!GetVolumeName(root, buffer, (uint)buffer.Length)) return null;  // Windows' defaults apply: the bin is on
        var volume = new string(buffer).TrimEnd('\0').TrimEnd('\\');
        var guid = volume[(volume.LastIndexOf("Volume", StringComparison.Ordinal) + "Volume".Length)..];
        using var key = Registry.CurrentUser.OpenSubKey($@"Software\Microsoft\Windows\CurrentVersion\Explorer\BitBucket\Volume\{guid}");
        if (key is null) return null;  // never configured: Windows' defaults apply
        if (key.GetValue("NukeOnDelete") is int nuke && nuke == 1) return $"the Recycle Bin is turned off on {root}";
        if (key.GetValue("MaxCapacity") is int limit && File.Exists(path) && new FileInfo(path).Length > limit * 1024L * 1024L)
            return $"{Path.GetFileName(path)} is bigger than the Recycle Bin of {root} allows ({limit:N0} MB)";
        return null;
    }

    /// <summary>Sends one file or folder to the Recycle Bin, or throws CannotRecycleException when Windows can't (the
    /// shell would then delete it for good, silently), or IOException when it fails.</summary>
    public static void Send(string path)
    {
        if (CanRecycle(path) is { } reason) throw new CannotRecycleException(reason);
        const uint delete = 3;
        const ushort flags = 0x40 | 0x10 | 0x400 | 0x4;  // allow undo, no confirmation, no error UI, silent
        var from = Marshal.StringToHGlobalUni(path + "\0");
        try
        {
            var operation = new FileOperation { Function = delete, From = from, Flags = flags };
            var result = SHFileOperation(ref operation);
            if (result != 0) throw new IOException($"Windows couldn't send it to the Recycle Bin (error 0x{result:X})");
            if (operation.AnyAborted != 0) throw new IOException("Windows stopped sending it to the Recycle Bin");
        }
        finally
        {
            Marshal.FreeHGlobal(from);
        }
    }
}

/// <summary>What the redundant files list's Delete selected did (SPEC §32).</summary>
public sealed class RecycleReport
{
    public List<OfferItem> Recycled { get; } = [];
    public List<(OfferItem Item, string Why)> Kept { get; } = [];  // no Recycle Bin there: never deleted for good
    public List<(OfferItem Item, string Why)> Skipped { get; } = [];  // changed meanwhile, or not reached after Stop
    public List<(OfferItem Item, string Why)> Failed { get; } = [];
    public bool Cancelled { get; set; }

    /// <summary>The folders whose contents changed, for the index.</summary>
    public List<string> Folders => Recycled.Select(i => Path.GetDirectoryName(i.Path)!).Distinct(StringComparer.OrdinalIgnoreCase).ToList();

    /// <summary>What went to the Recycle Bin, what was kept or skipped, and why.</summary>
    public string Text()
    {
        var lines = new List<string> { $"Sent {Report.Plural(Recycled.Count, "item")} to the Recycle Bin{(Cancelled ? " (stopped)." : ".")}" };
        foreach (var (title, rows) in new (string, List<(OfferItem Item, string Why)>)[]
                 {
                     ("Sent to the Recycle Bin", Recycled.Select(i => (i, i.Why)).ToList()), ("Kept", Kept), ("Skipped", Skipped),
                     ("Failed", Failed),
                 })
        {
            if (rows.Count == 0) continue;
            lines.Add("");
            lines.Add($"{title}:");
            lines.AddRange(rows.Select(r => $"    {r.Item.Path}  ({r.Why})"));
        }
        return string.Join("\n", lines);
    }
}

/// <summary>Recycling the checked items of Tidy's redundant files list (SPEC §32), ported from the Python app's
/// <c>services/recycle_job.py</c>: each item checked again first (gone, or a folder now holding something else, is
/// skipped); where Windows can't recycle, the item is kept. Stop takes effect between two items. The caller holds a
/// lock on the items.</summary>
public static class RecycleTask
{
    public static RecycleReport Run(IReadOnlyList<OfferItem> items, CancellationToken cancel, Action<string>? recycle = null,
        Action<int, int, string>? progress = null)
    {
        recycle ??= Recycle.Send;
        var report = new RecycleReport();
        for (var n = 0; n < items.Count; n++)
        {
            var item = items[n];
            if (cancel.IsCancellationRequested)
            {
                report.Cancelled = true;
                report.Skipped.AddRange(items.Skip(n).Select(rest => (rest, "not reached: stopped")));
                break;
            }
            progress?.Invoke(n, items.Count, item.Name);
            if (Clutter.Recheck(item) is { } why)
            {
                report.Skipped.Add((item, why));
                continue;
            }
            try
            {
                recycle(item.Path);
                report.Recycled.Add(item);
            }
            catch (CannotRecycleException e)
            {
                report.Kept.Add((item, $"no Recycle Bin: {e.Message}"));
            }
            catch (Exception e) when (e is IOException or UnauthorizedAccessException)
            {
                report.Failed.Add((item, e.Message));
            }
        }
        progress?.Invoke(items.Count, items.Count, "");
        Log.Info($"Recycled {report.Recycled.Count} of {items.Count} items{(report.Cancelled ? ", stopped" : "")}");
        return report;
    }
}
