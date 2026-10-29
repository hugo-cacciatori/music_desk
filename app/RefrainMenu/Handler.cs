using System.Diagnostics;
using System.Runtime.CompilerServices;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.Marshalling;
using System.Text;

namespace RefrainMenu;

[GeneratedComInterface, Guid("000214E8-0000-0000-C000-000000000046")]
public partial interface IShellExtInit
{
    [PreserveSig] int Initialize(nint pidlFolder, nint dataObject, nint progIdKey);
}

[GeneratedComInterface, Guid("000214E4-0000-0000-C000-000000000046")]
public partial interface IContextMenu
{
    [PreserveSig] int QueryContextMenu(nint menu, uint index, uint firstId, uint lastId, uint flags);
    [PreserveSig] int InvokeCommand(nint info);
    [PreserveSig] int GetCommandString(nuint id, uint type, nint reserved, nint name, uint max);
}

[GeneratedComInterface, Guid("00000001-0000-0000-C000-000000000046")]
public partial interface IClassFactory
{
    [PreserveSig] int CreateInstance(nint outer, in Guid iid, out nint instance);
    [PreserveSig] int LockServer([MarshalAs(UnmanagedType.Bool)] bool locked);
}

/// <summary>The handler Explorer makes for each right-click (SPEC §31 "How it is reached"): it gets the whole
/// selection at once, adds a Refrain submenu with the entries for what was clicked, and on a click starts Refrain
/// once with the entry and every path.</summary>
[GeneratedComClass]
public sealed partial class Handler : IShellExtInit, IContextMenu
{
    const int Ok = 0, Fail = unchecked((int)0x80004005), InvalidArg = unchecked((int)0x80070057), NotImpl = unchecked((int)0x80004001);
    const uint DefaultOnly = 0x1;  // CMF_DEFAULTONLY: a double-click asks only for the default verb

    /// <summary>Set by the test export: InvokeCommand records the command instead of starting it.</summary>
    internal static string? Captured;
    internal static bool Capture;

    List<(string Path, bool IsFolder)> items = [];
    bool background;
    List<Entry> entries = [];

    public int Initialize(nint pidlFolder, nint dataObject, nint progIdKey)
    {
        // A selection comes as a data object; a folder's empty space as its ID list. Items without a path on disk
        // (This PC, libraries) get no submenu.
        background = dataObject == 0;
        var paths = dataObject != 0 ? Native.DroppedPaths(dataObject) : pidlFolder != 0 && Native.PathOf(pidlFolder) is { } p ? [p] : [];
        items = paths.Select(path => (path, Native.IsFolder(path))).ToList();
        return Ok;
    }

    public int QueryContextMenu(nint menu, uint index, uint firstId, uint lastId, uint flags)
    {
        if ((flags & DefaultOnly) != 0 || items.Count == 0) return 0;
        entries = MenuLogic.Entries(MenuLogic.Classify(background, items), items);
        if (entries.Count == 0 || firstId + (uint)entries.Count > lastId) return 0;
        // Explorer's navigation pane may ask twice for one menu: a submenu already there is left alone (SPEC §31).
        if (Native.FindItem(menu, MenuLogic.SubmenuLabel) >= 0) return 0;
        var popup = Native.CreatePopupMenu();
        for (var i = 0; i < entries.Count; i++)
            Native.InsertMenuW(popup, (uint)i, Native.MF_BYPOSITION | (entries[i].IsSeparator ? Native.MF_SEPARATOR : Native.MF_STRING),
                entries[i].IsSeparator ? 0 : firstId + (nuint)i, entries[i].Label);
        // Just below 7-Zip's submenu when the menu has it (Hugo, 2026-10-24), else where Explorer asks.
        var sevenZip = Native.FindItem(menu, "7-Zip");
        var at = sevenZip >= 0 ? (uint)sevenZip + 1 : index;
        if (!Native.InsertMenuW(menu, at, Native.MF_BYPOSITION | Native.MF_POPUP | Native.MF_STRING, (nuint)popup, MenuLogic.SubmenuLabel))
        {
            Native.DestroyMenu(popup);
            return Fail;
        }
        return entries.Count;  // a success code carrying the ids used
    }

    public unsafe int InvokeCommand(nint info)
    {
        var verb = *(nint*)(info + 16);  // CMINVOKECOMMANDINFO.lpVerb (after cbSize, fMask and hwnd)
        Entry? entry = null;
        if (verb >> 16 == 0)
        {
            var offset = (int)verb;
            if (offset >= 0 && offset < entries.Count && !entries[offset].IsSeparator) entry = entries[offset];
        }
        else
        {
            var name = Marshal.PtrToStringAnsi(verb);
            const uint unicode = 0x4000;  // CMIC_MASK_UNICODE: CMINVOKECOMMANDINFOEX.lpVerbW holds the verb
            if ((*(uint*)(info + 4) & unicode) != 0 && *(uint*)info >= 72 && *(nint*)(info + 64) is var wide && wide != 0)
                name = Marshal.PtrToStringUni(wide);
            entry = entries.FirstOrDefault(e => !e.IsSeparator && $"refrain.{e.Verb}" == name);
        }
        if (entry is null) return InvalidArg;
        try
        {
            var (exe, arguments, list) = MenuLogic.Command(entry, items.Select(i => i.Path).ToList(), Native.ModuleFolder());
            if (Capture)
            {
                // The tests: the command is described, and nothing is written into the real data folder.
                Captured = list is null ? $"{exe} {arguments}" : $"{exe} {arguments}<list: {list.Replace("\n", "|")}>";
                return Ok;
            }
            if (list is not null)
            {
                Directory.CreateDirectory(MenuLogic.MenuFolder);
                var file = Path.Combine(MenuLogic.MenuFolder, $"{Guid.NewGuid():N}.txt");
                File.WriteAllText(file, list, new UTF8Encoding(false));
                arguments += MenuLogic.Quote(file);
            }
            Native.AllowSetForegroundWindow(-1);
            Process.Start(new ProcessStartInfo(exe, arguments) { UseShellExecute = false, WorkingDirectory = Native.ModuleFolder() })?.Dispose();
            return Ok;
        }
        catch (Exception e)
        {
            Native.MessageBoxW(0, $"Refrain couldn't be started: {e.Message}", "Refrain", 0x10);
            return Fail;
        }
    }

    public unsafe int GetCommandString(nuint id, uint type, nint reserved, nint name, uint max)
    {
        const uint verbW = 4, helpTextW = 5, validateW = 6;
        if (id >= (nuint)entries.Count || entries[(int)id].IsSeparator) return InvalidArg;
        var text = type switch
        {
            verbW => $"refrain.{entries[(int)id].Verb}",
            helpTextW => entries[(int)id].Help,
            validateW => "",
            _ => null,
        };
        if (text is null) return NotImpl;
        if (type == validateW) return Ok;
        var length = Math.Min(text.Length, (int)max - 1);
        fixed (char* source = text) Buffer.MemoryCopy(source, (void*)name, max * 2, length * 2);
        ((char*)name)[length] = '\0';
        return Ok;
    }
}

[GeneratedComClass]
public sealed partial class Factory : IClassFactory
{
    public int CreateInstance(nint outer, in Guid iid, out nint instance)
    {
        instance = 0;
        if (outer != 0) return unchecked((int)0x80040110);  // CLASS_E_NOAGGREGATION
        var unknown = Exports.Wrappers.GetOrCreateComInterfaceForObject(new Handler(), CreateComInterfaceFlags.None);
        var hr = Marshal.QueryInterface(unknown, in iid, out instance);
        Marshal.Release(unknown);
        return hr;
    }

    public int LockServer(bool locked) => 0;
}

public static unsafe class Exports
{
    /// <summary>The handler's class, registered by the installer (task 10). Not the Python app's: both may be
    /// installed side by side while the rebuild is a preview.</summary>
    public static readonly Guid Clsid = new("5c0f7d42-8a61-4c3e-9b1e-2d7f4a6b9e13");
    internal static readonly StrategyBasedComWrappers Wrappers = new();

    [UnmanagedCallersOnly(EntryPoint = "DllGetClassObject")]
    public static int DllGetClassObject(Guid* clsid, Guid* iid, nint* instance) => GetClassObject(clsid, iid, instance);

    static int GetClassObject(Guid* clsid, Guid* iid, nint* instance)
    {
        *instance = 0;
        if (*clsid != Clsid) return unchecked((int)0x80040111);  // CLASS_E_CLASSNOTAVAILABLE
        var unknown = Wrappers.GetOrCreateComInterfaceForObject(new Factory(), CreateComInterfaceFlags.None);
        var hr = Marshal.QueryInterface(unknown, in *iid, out *instance);
        Marshal.Release(unknown);
        return hr;
    }

    [UnmanagedCallersOnly(EntryPoint = "DllCanUnloadNow")]
    public static int DllCanUnloadNow() => 1;  // S_FALSE: stay loaded while Explorer runs

    /// <summary>For the tests, as Explorer does it: the class object, an instance, Initialize (a data object for the
    /// items, or a folder's ID list for empty space), QueryContextMenu on a menu that holds a 7-Zip submenu, then
    /// InvokeCommand on the first entry, captured rather than started. Writes "position", each entry's label and
    /// verb, and the command, one per line; returns the entries added, or -1.</summary>
    [UnmanagedCallersOnly(EntryPoint = "RefrainMenu_Test")]
    public static int Test(char* pathsText, int backgroundFlag, char* output, int max)
    {
        var paths = new string(pathsText).Split('\n', StringSplitOptions.RemoveEmptyEntries);
        Guid clsid = Clsid, factoryIid = typeof(IClassFactory).GUID;
        nint factoryPointer;
        if (GetClassObject(&clsid, &factoryIid, &factoryPointer) != 0) return -1;
        var factory = (IClassFactory)Wrappers.GetOrCreateObjectForComInstance(factoryPointer, CreateObjectFlags.None);
        Marshal.Release(factoryPointer);
        if (factory.CreateInstance(0, typeof(IShellExtInit).GUID, out var handlerPointer) != 0) return -1;
        // Through the COM interfaces, as Explorer calls it.
        var init = (IShellExtInit)Wrappers.GetOrCreateObjectForComInstance(handlerPointer, CreateObjectFlags.None);
        var handler = (IContextMenu)init;
        Marshal.Release(handlerPointer);
        int ok;
        if (backgroundFlag != 0)
        {
            fixed (char* folder = paths[0])
            {
                var pidl = Native.ILCreateFromPathW(folder);
                ok = init.Initialize(pidl, 0, 0);
                Native.ILFree(pidl);
            }
        }
        else
        {
            var data = Native.DataObjectFor(paths);
            if (data == 0) return -1;
            ok = init.Initialize(0, data, 0);
            Marshal.Release(data);
        }
        if (ok != 0) return -1;
        var menu = Native.CreatePopupMenu();
        Native.InsertMenuW(menu, 0, Native.MF_BYPOSITION | Native.MF_STRING, 1, "Open");
        Native.InsertMenuW(menu, 1, Native.MF_BYPOSITION | Native.MF_POPUP | Native.MF_STRING, (nuint)Native.CreatePopupMenu(), "7-&Zip");
        Native.InsertMenuW(menu, 2, Native.MF_BYPOSITION | Native.MF_STRING, 2, "Properties");
        var added = handler.QueryContextMenu(menu, 2, 100, 0x7fff, 0);
        var report = new StringBuilder();
        report.Append($"position={Native.FindItem(menu, MenuLogic.SubmenuLabel)}\n");
        var again = handler.QueryContextMenu(menu, 2, 200, 0x7fff, 0);
        report.Append($"again={again}\n");
        if (added > 0)
        {
            var label = stackalloc char[260];
            var verb = stackalloc char[260];
            for (nuint id = 0; id < (nuint)added; id++)
            {
                if (handler.GetCommandString(id, 4, 0, (nint)verb, 260) != 0)
                {
                    report.Append("-\n");  // a separator
                    continue;
                }
                var sub = Native.GetSubMenu(menu, Native.FindItem(menu, MenuLogic.SubmenuLabel));
                Native.GetMenuStringW(sub, (uint)id, label, 260, Native.MF_BYPOSITION);
                report.Append($"{new string(label)}|{new string(verb)}\n");
            }
            Handler.Capture = true;
            var info = stackalloc byte[72];
            *(uint*)info = 56;
            *(nint*)(info + 16) = 0;  // the first entry, by offset
            handler.InvokeCommand((nint)info);
            Handler.Capture = false;
            report.Append($"command={Handler.Captured}\n");
        }
        Native.DestroyMenu(menu);
        var text = report.ToString();
        var length = Math.Min(text.Length, max - 1);
        fixed (char* source = text) Buffer.MemoryCopy(source, output, max * 2L, length * 2L);
        output[length] = '\0';
        return added;
    }
}

static unsafe partial class Native
{
    public const uint MF_STRING = 0, MF_POPUP = 0x10, MF_BYPOSITION = 0x400, MF_SEPARATOR = 0x800;

    [LibraryImport("user32.dll", StringMarshalling = StringMarshalling.Utf16)]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool InsertMenuW(nint menu, uint position, uint flags, nuint id, string text);

    [LibraryImport("user32.dll")] public static partial nint CreatePopupMenu();
    [LibraryImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static partial bool DestroyMenu(nint menu);
    [LibraryImport("user32.dll")] public static partial int GetMenuItemCount(nint menu);
    [LibraryImport("user32.dll")] public static partial nint GetSubMenu(nint menu, int position);
    [LibraryImport("user32.dll")] public static partial int GetMenuStringW(nint menu, uint item, char* text, int max, uint flags);
    [LibraryImport("user32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static partial bool AllowSetForegroundWindow(int processId);

    [LibraryImport("user32.dll", StringMarshalling = StringMarshalling.Utf16)]
    public static partial int MessageBoxW(nint window, string text, string caption, uint type);

    [LibraryImport("shell32.dll")] public static partial nint ILCreateFromPathW(char* path);
    [LibraryImport("shell32.dll")] public static partial void ILFree(nint pidl);
    [LibraryImport("shell32.dll")] public static partial nint ILClone(nint pidl);
    [LibraryImport("shell32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static partial bool ILRemoveLastID(nint pidl);
    [LibraryImport("shell32.dll")] public static partial nint ILFindLastID(nint pidl);
    [LibraryImport("shell32.dll")] [return: MarshalAs(UnmanagedType.Bool)] public static partial bool SHGetPathFromIDListW(nint pidl, char* path);
    [LibraryImport("shell32.dll")] public static partial int SHCreateDataObject(nint folder, uint count, nint* children, nint inner, Guid* iid, nint* data);
    [LibraryImport("shell32.dll")] public static partial uint DragQueryFileW(nint drop, uint index, char* file, uint max);
    [LibraryImport("ole32.dll")] public static partial void ReleaseStgMedium(void* medium);
    [LibraryImport("kernel32.dll")] public static partial uint GetModuleFileNameW(nint module, char* name, uint max);
    [LibraryImport("kernel32.dll", StringMarshalling = StringMarshalling.Utf16)] public static partial uint GetFileAttributesW(string path);

    [LibraryImport("kernel32.dll")]
    [return: MarshalAs(UnmanagedType.Bool)]
    public static partial bool GetModuleHandleExW(uint flags, void* address, out nint module);

    public static bool IsFolder(string path)
    {
        var attributes = GetFileAttributesW(path);
        return attributes != uint.MaxValue && (attributes & 0x10) != 0;  // FILE_ATTRIBUTE_DIRECTORY
    }

    public static string? PathOf(nint pidl)
    {
        var buffer = stackalloc char[32768];
        return SHGetPathFromIDListW(pidl, buffer) ? new string(buffer) : null;
    }

    /// <summary>The position of a menu item by its label, its "&amp;" marks ignored; -1 when it isn't there.</summary>
    public static int FindItem(nint menu, string label)
    {
        var buffer = stackalloc char[260];
        for (var i = 0; i < GetMenuItemCount(menu); i++)
        {
            var length = GetMenuStringW(menu, (uint)i, buffer, 260, MF_BYPOSITION);
            if (length > 0 && new string(buffer, 0, length).Replace("&", "") == label) return i;
        }
        return -1;
    }

    [StructLayout(LayoutKind.Sequential)]
    struct FormatEtc { public ushort Format; public nint Device; public uint Aspect; public int Index; public uint Medium; }

    [StructLayout(LayoutKind.Sequential)]
    struct StgMedium { public uint Medium; public nint Handle; public nint Release; }

    /// <summary>IDataObject::GetData(CF_HDROP), called through the vtable (slot 3), then every dropped path.</summary>
    public static List<string> DroppedPaths(nint dataObject)
    {
        var format = new FormatEtc { Format = 15, Aspect = 1, Index = -1, Medium = 1 };
        StgMedium medium;
        var getData = (delegate* unmanaged<nint, FormatEtc*, StgMedium*, int>)(*(nint**)dataObject)[3];
        if (getData(dataObject, &format, &medium) != 0) return [];
        var paths = new List<string>();
        var buffer = stackalloc char[32768];
        var count = DragQueryFileW(medium.Handle, uint.MaxValue, null, 0);
        for (uint i = 0; i < count; i++)
        {
            var length = DragQueryFileW(medium.Handle, i, buffer, 32768);
            if (length > 0) paths.Add(new string(buffer, 0, (int)length));
        }
        ReleaseStgMedium(&medium);
        return paths;
    }

    /// <summary>A shell data object for items of one folder, as Explorer hands a selection over (tests).</summary>
    public static nint DataObjectFor(IReadOnlyList<string> paths)
    {
        var absolute = new nint[paths.Count];
        for (var i = 0; i < paths.Count; i++)
            fixed (char* p = paths[i]) absolute[i] = ILCreateFromPathW(p);
        var parent = ILClone(absolute[0]);
        ILRemoveLastID(parent);
        var children = stackalloc nint[paths.Count];
        for (var i = 0; i < paths.Count; i++) children[i] = ILFindLastID(absolute[i]);
        var iid = new Guid("0000010e-0000-0000-C000-000000000046");  // IID_IDataObject
        nint data;
        var hr = SHCreateDataObject(parent, (uint)paths.Count, children, 0, &iid, &data);
        ILFree(parent);
        foreach (var pidl in absolute) ILFree(pidl);
        return hr == 0 ? data : 0;
    }

    /// <summary>This DLL's own folder: Refrain.exe sits beside it.</summary>
    public static string ModuleFolder()
    {
        delegate* unmanaged<int> self = &Anchor;
        GetModuleHandleExW(6, self, out var module);  // FROM_ADDRESS | UNCHANGED_REFCOUNT
        var buffer = stackalloc char[32768];
        var length = GetModuleFileNameW(module, buffer, 32768);
        return Path.GetDirectoryName(new string(buffer, 0, (int)length)) ?? AppContext.BaseDirectory;
    }

    [UnmanagedCallersOnly]
    static int Anchor() => 0;
}
