// MusicDeskMenu.dll: the MusicDesk submenu in File Explorer's classic right-click menu (SPEC §31).
//
// A classic in-process IContextMenu handler, as 7-Zip's. It stays small: it looks only at what was right-clicked
// (empty space, folders, files: their kinds, never their contents), builds the submenu, and on a click starts
// MusicDesk.exe (beside this DLL) once, with the entry and every path. Every action stays in the Python app.
// The paths go through a list file in %LOCALAPPDATA%\MusicDesk\menu\ (one per line, UTF-8), so a large selection
// isn't cut by the command line's length limit; MusicDesk reads and deletes it.
//
// Built by packaging/build_menu.py; registered by the installer (SPEC §31). Two exports beside COM's,
// MusicDeskMenu_Entries and MusicDeskMenu_Command, run the same code for tests/test_menu_handler.py.

#include <windows.h>
#include <shlobj.h>
#include <shlwapi.h>
#include <shellapi.h>
#include <strsafe.h>

#include <new>
#include <string>
#include <vector>

// {366C8DAE-145F-40D0-B273-8AFE153DD3F5}: never change it, the installer registers it.
static const CLSID CLSID_MusicDeskMenu = {0x366c8dae, 0x145f, 0x40d0, {0xb2, 0x73, 0x8a, 0xfe, 0x15, 0x3d, 0xd3, 0xf5}};

static HINSTANCE g_module = nullptr;
static LONG g_objects = 0;
static LONG g_locks = 0;

// -- what was clicked, and which entries it gets --

// The extensions rsgain scans (core/formats.py, the formats with replaygain=True); tests/test_menu_handler.py
// checks they stay in step. The handler judges audio by extension only (SPEC §31).
static const wchar_t* const REPLAYGAIN_EXTENSIONS[] = {
    L".flac", L".ogg", L".oga", L".opus", L".spx", L".mp3", L".mp2", L".wav", L".aif", L".aiff", L".aifc",
    L".m4a", L".mp4", L".wv", L".mpc", L".tak", L".wma",
};

// Every audio extension the app knows (core/formats.py, all FORMATS): Rename's music files. Also kept in step.
static const wchar_t* const AUDIO_EXTENSIONS[] = {
    L".flac", L".ogg", L".oga", L".opus", L".spx", L".mp3", L".mp2", L".wav", L".aif", L".aiff", L".aifc",
    L".m4a", L".mp4", L".m4b", L".wv", L".mpc", L".tak", L".ofr", L".ofs", L".tta", L".wma", L".dsf", L".dff",
    L".aac",
};

// What Trim cuts and the split reads (core/cue.py SUPPORTED, the bundled flac's formats). Also kept in step.
static const wchar_t* const TRIM_EXTENSIONS[] = {L".flac", L".wav", L".aif", L".aiff"};

template <size_t N>
static bool has_extension(const std::wstring& path, const wchar_t* const (&extensions)[N]) {
    const wchar_t* extension = PathFindExtensionW(path.c_str());
    for (const wchar_t* candidate : extensions)
        if (_wcsicmp(extension, candidate) == 0)
            return true;
    return false;
}

struct Selection {
    bool background = false;  // empty space: paths holds the open folder
    std::vector<std::wstring> paths;
    int folders = 0;
    int files = 0;
    int audio = 0;  // files rsgain scans
    int music = 0;  // audio files of any format the app knows
    bool one_folder = true;  // every file is in the same folder (a search's results may not be)
};

static void count_kinds(Selection& selection) {
    selection.folders = selection.files = selection.audio = selection.music = 0;
    selection.one_folder = true;
    std::wstring parent;
    for (const std::wstring& path : selection.paths) {
        DWORD attributes = GetFileAttributesW(path.c_str());
        if (attributes != INVALID_FILE_ATTRIBUTES && (attributes & FILE_ATTRIBUTE_DIRECTORY)) {
            ++selection.folders;
            continue;
        }
        ++selection.files;
        if (has_extension(path, REPLAYGAIN_EXTENSIONS))
            ++selection.audio;
        if (has_extension(path, AUDIO_EXTENSIONS))
            ++selection.music;
        std::wstring folder = path.substr(0, path.find_last_of(L'\\'));
        if (parent.empty())
            parent = folder;
        else if (_wcsicmp(parent.c_str(), folder.c_str()) != 0)
            selection.one_folder = false;
    }
}

struct Entry {
    const wchar_t* verb;  // also what MusicDesk is asked: "--" + the verb's part after "musicdesk."
    const wchar_t* help;
};

static const Entry OPEN = {L"musicdesk.open", L"Opens this folder in MusicDesk"};
static const Entry RG_ALBUM = {L"musicdesk.replaygain-album",
                               L"Runs ReplayGain on this folder as one album, with the target set in MusicDesk"};
static const Entry RG_DISCOGRAPHY = {L"musicdesk.replaygain-discography",
                                     L"Runs ReplayGain on each album in this folder, then levels them together"};
static const Entry RG_ALBUMS = {L"musicdesk.replaygain-albums",
                                L"Runs ReplayGain on each folder as its own album, one after another"};
static const Entry RG_TRACKS = {L"musicdesk.replaygain-tracks",
                                L"Runs ReplayGain on the selected tracks as one album; the folder's other tracks "
                                L"are left as they are"};
static const Entry RG_LOOSE = {L"musicdesk.replaygain-loose",
                               L"Runs ReplayGain on each selected track alone, as its own album, with no log"};
static const Entry RENAME = {L"musicdesk.rename",
                             L"Previews renaming the music files from their tags; nothing changes until you apply"};
static const Entry REMOVE_RG = {L"musicdesk.remove-replaygain",
                                L"Removes the selected tracks' ReplayGain tags, after a question"};
static const Entry TIDY = {L"musicdesk.tidy",
                           L"Plans Tidy (split, rename, ReplayGain) on every album here; nothing changes until you run "
                           L"it"};
static const Entry CLEANUP = {L"musicdesk.cleanup",
                              L"Lists the redundant files here; you choose what goes to the Recycle Bin"};
static const Entry SPLIT = {L"musicdesk.split",
                            L"Opens the cue sheet's Split page; nothing is split until you click Split"};
static const Entry TRIM = {L"musicdesk.trim",
                           L"Opens the track's Trim page; the trimmed copy is made only when you click Trim"};
static const Entry SETTINGS = {L"musicdesk.settings", L"Opens MusicDesk's Options window"};

// The submenu's entries, in order (SPEC §31): Open in MusicDesk on one folder or empty space; the ReplayGain entries
// on folders only or on files with tracks rsgain scans (no folder); Rename on any selection with a folder or a
// music file (empty space too); Remove ReplayGain tags when every item is a track rsgain scans; Tidy and Clean up
// on folders only (empty space too); Split on one .cue; Trim on one FLAC, WAV or AIFF file; Settings always.
static std::vector<const Entry*> entries_for(const Selection& selection) {
    std::vector<const Entry*> entries;
    if (selection.background || (selection.folders == 1 && selection.files == 0)) {
        entries.push_back(&OPEN);
        entries.push_back(&RG_ALBUM);
        entries.push_back(&RG_DISCOGRAPHY);
    } else if (selection.folders > 1 && selection.files == 0) {
        entries.push_back(&RG_ALBUMS);
    } else if (selection.folders == 0 && selection.audio == 1) {
        entries.push_back(&RG_LOOSE);  // "this track"
    } else if (selection.folders == 0 && selection.audio > 1) {
        if (selection.one_folder)  // an album is one folder's tracks
            entries.push_back(&RG_TRACKS);
        entries.push_back(&RG_LOOSE);
    }
    if (selection.background || selection.folders > 0 || selection.music > 0)
        entries.push_back(&RENAME);
    if (!selection.background && selection.folders == 0 && selection.files > 0 && selection.audio == selection.files)
        entries.push_back(&REMOVE_RG);
    if (selection.background || (selection.folders > 0 && selection.files == 0)) {
        entries.push_back(&TIDY);
        entries.push_back(&CLEANUP);
    }
    if (!selection.background && selection.folders == 0 && selection.files == 1) {
        const std::wstring& path = selection.paths[0];
        if (_wcsicmp(PathFindExtensionW(path.c_str()), L".cue") == 0)
            entries.push_back(&SPLIT);
        if (has_extension(path, TRIM_EXTENSIONS))
            entries.push_back(&TRIM);
    }
    entries.push_back(&SETTINGS);
    return entries;
}

// -- the ReplayGain target, for the labels: read from settings.json when the menu opens --

static bool is_space(char c) {
    return c == ' ' || c == '\t' || c == '\r' || c == '\n';
}

// The JSON number at `text[at]`, or false (true, a string…: the app then takes its default, as here).
static bool read_number(const std::string& text, size_t at, double& value) {
    while (at < text.size() && is_space(text[at]))
        ++at;
    if (at >= text.size() || !(text[at] == '-' || (text[at] >= '0' && text[at] <= '9')))
        return false;
    char* end = nullptr;
    value = strtod(text.c_str() + at, &end);
    return end != text.c_str() + at;
}

// replaygain_options.target in settings.json's text, or false when it isn't there as a number. A small scan of
// the JSON: strings may hold braces (the comment's "{target}") and escaped quotes.
static bool find_target(const std::string& text, double& target) {
    size_t at = text.find("\"replaygain_options\"");
    if (at == std::string::npos)
        return false;
    at = text.find('{', at);
    if (at == std::string::npos)
        return false;
    int depth = 0;
    for (size_t i = at; i < text.size(); ++i) {
        char c = text[i];
        if (c == '{' || c == '[') {
            ++depth;
        } else if (c == '}' || c == ']') {
            if (--depth == 0)
                return false;
        } else if (c == '"') {
            size_t start = i + 1;
            for (++i; i < text.size() && text[i] != '"'; ++i)
                if (text[i] == '\\')
                    ++i;
            if (depth != 1 || text.compare(start, i - start, "target") != 0)
                continue;
            size_t colon = i + 1;
            while (colon < text.size() && is_space(text[colon]))
                ++colon;
            if (colon < text.size() && text[colon] == ':')
                return read_number(text, colon + 1, target);
        }
    }
    return false;
}

static std::wstring local_app_data();

// The target as the labels show it, "−15 LUFS"; the app's default (-15) when settings.json doesn't say.
static std::wstring target_text() {
    double target = -15.0;
    std::wstring base = local_app_data();
    if (!base.empty()) {
        std::wstring file = base + L"\\MusicDesk\\settings.json";
        HANDLE handle = CreateFileW(file.c_str(), GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE,
                                    nullptr, OPEN_EXISTING, FILE_ATTRIBUTE_NORMAL, nullptr);
        if (handle != INVALID_HANDLE_VALUE) {
            LARGE_INTEGER size;
            if (GetFileSizeEx(handle, &size) && size.QuadPart < 4 * 1024 * 1024) {
                std::string text(static_cast<size_t>(size.QuadPart), '\0');
                DWORD read = 0;
                if (ReadFile(handle, text.data(), static_cast<DWORD>(text.size()), &read, nullptr)) {
                    text.resize(read);
                    double value = 0;
                    if (find_target(text, value))
                        target = value;
                }
            }
            CloseHandle(handle);
        }
    }
    wchar_t number[32];
    StringCchPrintfW(number, ARRAYSIZE(number), L"%g", target < 0 ? -target : target);  // as the app's "{:g}"
    return std::wstring(target < 0 ? L"−" : L"") + number + L" LUFS";
}

// An entry's label (SPEC §31, Hugo 2026-10-24), with the target so it shows where the run goes.
static std::wstring label_for(const Entry& entry, const Selection& selection, const std::wstring& target) {
    std::wstring suffix = L" (" + target + L")";
    if (&entry == &OPEN)
        return L"Open in MusicDesk";
    if (&entry == &SETTINGS)
        return L"Settings";
    if (&entry == &RENAME)
        return L"Rename from tags\u2026";
    if (&entry == &REMOVE_RG)
        return L"Remove ReplayGain tags\u2026";
    if (&entry == &SPLIT)
        return L"Split this cue sheet\u2026";
    if (&entry == &TRIM)
        return L"Trim this track\u2026";
    if (&entry == &TIDY || &entry == &CLEANUP) {  // with what they act on (Hugo, 2026-10-24)
        std::wstring what = selection.background || selection.folders == 1
            ? L"this folder" : std::to_wstring(selection.folders) + L" folders";
        return (&entry == &TIDY ? L"Tidy " : L"Clean up ") + what + L"\u2026";
    }
    if (&entry == &RG_ALBUM)
        return L"ReplayGain as an album" + suffix;
    if (&entry == &RG_DISCOGRAPHY)
        return L"ReplayGain as a discography" + suffix;
    if (&entry == &RG_ALBUMS)
        return L"ReplayGain: " + std::to_wstring(selection.folders) + L" folders, each as an album" + suffix;
    if (&entry == &RG_TRACKS)
        return L"ReplayGain: these " + std::to_wstring(selection.audio) + L" tracks as an album" + suffix;
    if (selection.audio == 1)
        return L"ReplayGain this track" + suffix;
    return L"ReplayGain: each of these " + std::to_wstring(selection.audio) + L" tracks on its own" + suffix;
}

// -- starting MusicDesk --

static std::wstring module_folder() {
    wchar_t path[MAX_PATH * 4];
    DWORD length = GetModuleFileNameW(g_module, path, ARRAYSIZE(path));
    if (length == 0 || length >= ARRAYSIZE(path))
        return L"";
    PathRemoveFileSpecW(path);
    return path;
}

static std::wstring quoted(const std::wstring& argument) {
    // Paths can't hold a double quote; a trailing backslash would escape the closing one ("D:\").
    std::wstring text = L"\"" + argument;
    if (!argument.empty() && argument.back() == L'\\')
        text += L'\\';
    return text + L"\"";
}

static std::wstring local_app_data() {
    // As music_desk.paths.app_data_dir: the LOCALAPPDATA variable first.
    wchar_t buffer[MAX_PATH * 4];
    DWORD length = GetEnvironmentVariableW(L"LOCALAPPDATA", buffer, ARRAYSIZE(buffer));
    if (length > 0 && length < ARRAYSIZE(buffer))
        return buffer;
    PWSTR known = nullptr;
    std::wstring result;
    if (SUCCEEDED(SHGetKnownFolderPath(FOLDERID_LocalAppData, 0, nullptr, &known)))
        result = known;
    CoTaskMemFree(known);
    return result;
}

// Writes the paths to a new list file; returns its path, or "" when it can't be written.
static std::wstring write_list(const std::vector<std::wstring>& paths) {
    std::wstring base = local_app_data();
    if (base.empty())
        return L"";
    std::wstring folder = base + L"\\MusicDesk\\menu";
    int made = SHCreateDirectoryExW(nullptr, folder.c_str(), nullptr);  // with any missing parent, as the app's
    if (made != ERROR_SUCCESS && made != ERROR_ALREADY_EXISTS && made != ERROR_FILE_EXISTS)
        return L"";
    GUID guid;
    wchar_t name[64];
    if (FAILED(CoCreateGuid(&guid)) || StringFromGUID2(guid, name, ARRAYSIZE(name)) == 0)
        return L"";
    std::wstring file = folder + L"\\" + std::wstring(name + 1, wcslen(name) - 2) + L".txt";  // no braces

    std::wstring text;
    for (const std::wstring& path : paths)
        text += path + L"\n";
    int size = WideCharToMultiByte(CP_UTF8, 0, text.c_str(), static_cast<int>(text.size()), nullptr, 0, nullptr,
                                   nullptr);
    std::string utf8(size, '\0');
    WideCharToMultiByte(CP_UTF8, 0, text.c_str(), static_cast<int>(text.size()), utf8.data(), size, nullptr,
                        nullptr);
    HANDLE handle = CreateFileW(file.c_str(), GENERIC_WRITE, 0, nullptr, CREATE_NEW, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
        return L"";
    DWORD written = 0;
    BOOL ok = WriteFile(handle, utf8.data(), static_cast<DWORD>(utf8.size()), &written, nullptr);
    CloseHandle(handle);
    if (!ok || written != utf8.size()) {
        DeleteFileW(file.c_str());
        return L"";
    }
    return file;
}

// The command line an entry starts, writing its list file; "" when it can't. `exe`: MusicDesk.exe's path.
static std::wstring command_for(const Entry& entry, const Selection& selection, const std::wstring& exe) {
    if (&entry == &OPEN)  // as the `musicdesk` keyword (SPEC §29): the folder as the one argument
        return selection.paths.empty() ? L"" : quoted(exe) + L" " + quoted(selection.paths[0]);
    std::wstring list = write_list(selection.paths);
    if (list.empty())
        return L"";
    std::wstring option = std::wstring(L"--") + (entry.verb + wcslen(L"musicdesk."));
    return quoted(exe) + L" " + option + L" --list " + quoted(list);
}

static bool start(std::wstring command, const std::wstring& folder) {
    STARTUPINFOW startup = {sizeof(startup)};
    PROCESS_INFORMATION process = {};
    if (!CreateProcessW(nullptr, command.data(), nullptr, nullptr, FALSE, 0, nullptr,
                        folder.empty() ? nullptr : folder.c_str(), &startup, &process))
        return false;
    CloseHandle(process.hThread);
    CloseHandle(process.hProcess);
    return true;
}

// -- an opt-in call log, to see what Explorer asks (SPEC §31) --

// One line per call to %LOCALAPPDATA%\MusicDesk\logs\menu.log, only while %LOCALAPPDATA%\MusicDesk\menu-debug.txt
// exists (Hugo creates it to diagnose, then deletes it): what Explorer asked, which menu, what the handler did.
static void debug_log(const void* object, const std::wstring& line) {
    std::wstring base = local_app_data();
    if (base.empty() || GetFileAttributesW((base + L"\\MusicDesk\\menu-debug.txt").c_str()) == INVALID_FILE_ATTRIBUTES)
        return;
    std::wstring folder = base + L"\\MusicDesk\\logs";
    SHCreateDirectoryExW(nullptr, folder.c_str(), nullptr);
    SYSTEMTIME now;
    GetLocalTime(&now);
    wchar_t head[96];
    StringCchPrintfW(head, ARRAYSIZE(head), L"%02d:%02d:%02d.%03d pid %lu tid %lu obj %p  ", now.wHour, now.wMinute,
                     now.wSecond, now.wMilliseconds, GetCurrentProcessId(), GetCurrentThreadId(), object);
    std::wstring text = head + line + L"\r\n";
    int size = WideCharToMultiByte(CP_UTF8, 0, text.c_str(), static_cast<int>(text.size()), nullptr, 0, nullptr, nullptr);
    std::string utf8(size, '\0');
    WideCharToMultiByte(CP_UTF8, 0, text.c_str(), static_cast<int>(text.size()), utf8.data(), size, nullptr, nullptr);
    HANDLE handle = CreateFileW((folder + L"\\menu.log").c_str(), FILE_APPEND_DATA, FILE_SHARE_READ | FILE_SHARE_WRITE,
                                nullptr, OPEN_ALWAYS, FILE_ATTRIBUTE_NORMAL, nullptr);
    if (handle == INVALID_HANDLE_VALUE)
        return;
    DWORD written = 0;
    WriteFile(handle, utf8.data(), static_cast<DWORD>(utf8.size()), &written, nullptr);
    CloseHandle(handle);
}

static std::wstring pidl_path(PCIDLIST_ABSOLUTE pidl) {
    wchar_t path[MAX_PATH * 4];
    if (pidl == nullptr)
        return L"(none)";
    return SHGetPathFromIDListEx(pidl, path, ARRAYSIZE(path), GPFIDL_DEFAULT) ? path : L"(not a path)";
}

// -- where the submenu goes in Explorer's menu --

// An item's text without its "&" accelerator marks ("7-&Zip" reads "7-Zip").
static std::wstring item_text(HMENU menu, int position) {
    wchar_t text[128];
    if (GetMenuStringW(menu, position, text, ARRAYSIZE(text), MF_BYPOSITION) <= 0)
        return L"";
    std::wstring plain;
    for (const wchar_t* c = text; *c; ++c)
        if (*c != L'&')
            plain += *c;
    return plain;
}

// Where the MusicDesk submenu goes (SPEC §31): -1 when the menu already has one (Explorer's navigation pane asked
// this handler twice for one folder: two submenus, measured by Hugo 2026-10-24); just below 7-Zip's submenu when it
// is there (Hugo: "just below 7-Zip"), else where Explorer asks.
static int place_in(HMENU menu, UINT index) {
    int count = GetMenuItemCount(menu);
    int below_seven_zip = -1;
    for (int i = 0; i < count; ++i) {
        std::wstring text = item_text(menu, i);
        if (text == L"MusicDesk")
            return -1;
        if (text == L"7-Zip")
            below_seven_zip = i + 1;
    }
    return below_seven_zip >= 0 ? below_seven_zip : static_cast<int>(index);
}

// -- the COM object --

class MusicDeskMenu : public IShellExtInit, public IContextMenu {
public:
    MusicDeskMenu() { InterlockedIncrement(&g_objects); }

    // IUnknown
    IFACEMETHODIMP QueryInterface(REFIID riid, void** object) override {
        static const QITAB table[] = {
            QITABENT(MusicDeskMenu, IShellExtInit),
            QITABENT(MusicDeskMenu, IContextMenu),
            {nullptr, 0},
        };
        return QISearch(this, table, riid, object);
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return InterlockedIncrement(&references_); }
    IFACEMETHODIMP_(ULONG) Release() override {
        LONG count = InterlockedDecrement(&references_);
        if (count == 0)
            delete this;
        return count;
    }

    // IShellExtInit: a selection comes as CF_HDROP; empty space as the folder's pidl alone.
    IFACEMETHODIMP Initialize(PCIDLIST_ABSOLUTE folder, IDataObject* data, HKEY) override {
        selection_ = Selection();
        if (data != nullptr) {
            FORMATETC format = {CF_HDROP, nullptr, DVASPECT_CONTENT, -1, TYMED_HGLOBAL};
            STGMEDIUM medium;
            if (FAILED(data->GetData(&format, &medium)))
                return E_INVALIDARG;  // nothing on disk (This PC, a library…): no submenu
            HDROP drop = static_cast<HDROP>(GlobalLock(medium.hGlobal));
            if (drop != nullptr) {
                UINT count = DragQueryFileW(drop, 0xFFFFFFFF, nullptr, 0);
                for (UINT i = 0; i < count; ++i) {
                    UINT length = DragQueryFileW(drop, i, nullptr, 0);
                    std::wstring path(length, L'\0');
                    DragQueryFileW(drop, i, path.data(), length + 1);
                    selection_.paths.push_back(path);
                }
                GlobalUnlock(medium.hGlobal);
            }
            ReleaseStgMedium(&medium);
        } else if (folder != nullptr) {
            wchar_t path[MAX_PATH * 4];
            if (!SHGetPathFromIDListEx(folder, path, ARRAYSIZE(path), GPFIDL_DEFAULT))
                return E_INVALIDARG;
            selection_.background = true;
            selection_.paths.push_back(path);
        }
        std::wstring listed;
        for (const std::wstring& path : selection_.paths)
            listed += L" [" + path + L"]";
        debug_log(this, L"Initialize folder=" + pidl_path(folder) + L" data=" + (data ? L"yes" : L"no") +
                  L" background=" + (selection_.background ? L"yes" : L"no") + L" paths:" + listed);
        if (selection_.paths.empty())
            return E_INVALIDARG;
        if (!selection_.background)
            count_kinds(selection_);
        return S_OK;
    }

    // IContextMenu
    IFACEMETHODIMP QueryContextMenu(HMENU menu, UINT index, UINT first, UINT last, UINT flags) override {
        std::wstring items;
        for (int i = 0; i < GetMenuItemCount(menu); ++i)
            items += L" [" + item_text(menu, i) + L"]";
        wchar_t call[160];
        StringCchPrintfW(call, ARRAYSIZE(call), L"QueryContextMenu menu=%p index=%u first=%u last=%u flags=0x%x items=%d:",
                         menu, index, first, last, flags, GetMenuItemCount(menu));
        debug_log(this, call + items);
        if (flags & CMF_DEFAULTONLY)
            return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);
        entries_ = entries_for(selection_);
        if (entries_.empty() || first + entries_.size() > last)
            return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);
        int position = place_in(menu, index);
        debug_log(this, L"  -> " + (position < 0 ? std::wstring(L"skipped: the menu has a MusicDesk submenu")
                                               : L"inserted at " + std::to_wstring(position)));
        if (position < 0) {
            entries_.clear();  // the submenu already there is the one Explorer shows
            return MAKE_HRESULT(SEVERITY_SUCCESS, 0, 0);
        }
        std::wstring target = target_text();
        HMENU submenu = CreatePopupMenu();
        for (size_t i = 0; i < entries_.size(); ++i) {
            if (entries_[i] == &SETTINGS && i > 0)
                AppendMenuW(submenu, MF_SEPARATOR, 0, nullptr);  // Settings apart from the actions
            AppendMenuW(submenu, MF_STRING, first + i, label_for(*entries_[i], selection_, target).c_str());
        }
        MENUITEMINFOW item = {sizeof(item)};
        item.fMask = MIIM_SUBMENU | MIIM_STRING | MIIM_ID;
        item.wID = first + static_cast<UINT>(entries_.size());
        item.hSubMenu = submenu;
        item.dwTypeData = const_cast<LPWSTR>(L"MusicDesk");
        if (!InsertMenuItemW(menu, static_cast<UINT>(position), TRUE, &item)) {
            DestroyMenu(submenu);
            return HRESULT_FROM_WIN32(GetLastError());
        }
        return MAKE_HRESULT(SEVERITY_SUCCESS, 0, entries_.size() + 1);
    }

    IFACEMETHODIMP InvokeCommand(CMINVOKECOMMANDINFO* info) override {
        const Entry* entry = find(info);
        if (entry == nullptr)
            return E_FAIL;
        std::wstring folder = module_folder();
        std::wstring command = command_for(*entry, selection_, folder + L"\\MusicDesk.exe");
        if (command.empty() || !start(command, folder)) {
            MessageBoxW(info->hwnd, L"MusicDesk couldn't be started. Reinstalling it may help.", L"MusicDesk",
                        MB_OK | MB_ICONWARNING);
            return E_FAIL;
        }
        return S_OK;
    }

    IFACEMETHODIMP GetCommandString(UINT_PTR id, UINT type, UINT*, LPSTR name, UINT size) override {
        if (id >= entries_.size())
            return E_INVALIDARG;
        const Entry* entry = entries_[id];
        switch (type) {
        case GCS_VERBW:
            return StringCchCopyW(reinterpret_cast<LPWSTR>(name), size, entry->verb);
        case GCS_HELPTEXTW:
            return StringCchCopyW(reinterpret_cast<LPWSTR>(name), size, entry->help);
        default:
            return E_NOTIMPL;
        }
    }

    const Selection& selection() const { return selection_; }  // for the tests' exports

private:
    ~MusicDeskMenu() { InterlockedDecrement(&g_objects); }

    // The entry a click names: an offset, or a verb (ANSI or, with CMIC_MASK_UNICODE, wide).
    const Entry* find(CMINVOKECOMMANDINFO* info) const {
        if (IS_INTRESOURCE(info->lpVerb)) {
            UINT_PTR id = reinterpret_cast<UINT_PTR>(info->lpVerb);
            return id < entries_.size() ? entries_[id] : nullptr;
        }
        std::wstring verb;
        if (info->cbSize >= sizeof(CMINVOKECOMMANDINFOEX) && (info->fMask & CMIC_MASK_UNICODE)) {
            const wchar_t* wide = reinterpret_cast<CMINVOKECOMMANDINFOEX*>(info)->lpVerbW;
            if (wide != nullptr)
                verb = wide;
        }
        if (verb.empty()) {
            int length = MultiByteToWideChar(CP_ACP, 0, info->lpVerb, -1, nullptr, 0);
            if (length > 0) {
                verb.resize(length - 1);
                MultiByteToWideChar(CP_ACP, 0, info->lpVerb, -1, verb.data(), length);
            }
        }
        for (const Entry* entry : entries_)
            if (verb == entry->verb)
                return entry;
        return nullptr;
    }

    LONG references_ = 1;
    Selection selection_;
    std::vector<const Entry*> entries_;
};

class ClassFactory : public IClassFactory {
public:
    IFACEMETHODIMP QueryInterface(REFIID riid, void** object) override {
        static const QITAB table[] = {QITABENT(ClassFactory, IClassFactory), {nullptr, 0}};
        return QISearch(this, table, riid, object);
    }
    IFACEMETHODIMP_(ULONG) AddRef() override { return 2; }  // a static object
    IFACEMETHODIMP_(ULONG) Release() override { return 1; }

    IFACEMETHODIMP CreateInstance(IUnknown* outer, REFIID riid, void** object) override {
        *object = nullptr;
        if (outer != nullptr)
            return CLASS_E_NOAGGREGATION;
        MusicDeskMenu* menu = new (std::nothrow) MusicDeskMenu();
        if (menu == nullptr)
            return E_OUTOFMEMORY;
        HRESULT result = menu->QueryInterface(riid, object);
        menu->Release();
        return result;
    }
    IFACEMETHODIMP LockServer(BOOL lock) override {
        if (lock)
            InterlockedIncrement(&g_locks);
        else
            InterlockedDecrement(&g_locks);
        return S_OK;
    }
};

static ClassFactory g_factory;

BOOL APIENTRY DllMain(HINSTANCE module, DWORD reason, LPVOID) {
    if (reason == DLL_PROCESS_ATTACH) {
        g_module = module;
        DisableThreadLibraryCalls(module);
    }
    return TRUE;
}

STDAPI DllGetClassObject(REFCLSID clsid, REFIID riid, void** object) {
    *object = nullptr;
    if (!IsEqualCLSID(clsid, CLSID_MusicDeskMenu))
        return CLASS_E_CLASSNOTAVAILABLE;
    return g_factory.QueryInterface(riid, object);
}

STDAPI DllCanUnloadNow() {
    return g_objects == 0 && g_locks == 0 ? S_OK : S_FALSE;
}

// -- the tests' exports: the real COM object, driven without Explorer --

// Makes the object as Explorer does and initializes it with `paths` (count of them; background: one folder, the
// open one). Returns null when Initialize refuses.
static IContextMenu* make_menu(const wchar_t* const* paths, int count, BOOL background) {
    IShellExtInit* init = nullptr;
    if (FAILED(g_factory.CreateInstance(nullptr, IID_PPV_ARGS(&init))))
        return nullptr;
    HRESULT result = E_INVALIDARG;
    if (background && count == 1) {
        PIDLIST_ABSOLUTE folder = nullptr;
        if (SUCCEEDED(SHParseDisplayName(paths[0], nullptr, &folder, 0, nullptr))) {
            result = init->Initialize(folder, nullptr, nullptr);
            CoTaskMemFree(folder);
        }
    } else if (!background && count > 0) {
        std::vector<PIDLIST_ABSOLUTE> items;
        for (int i = 0; i < count; ++i) {
            PIDLIST_ABSOLUTE item = nullptr;
            if (SUCCEEDED(SHParseDisplayName(paths[i], nullptr, &item, 0, nullptr)))
                items.push_back(item);
        }
        IShellItemArray* array = nullptr;
        IDataObject* data = nullptr;
        if (static_cast<int>(items.size()) == count &&
            SUCCEEDED(SHCreateShellItemArrayFromIDLists(static_cast<UINT>(items.size()),
                                                        const_cast<PCIDLIST_ABSOLUTE_ARRAY>(items.data()), &array)) &&
            SUCCEEDED(array->BindToHandler(nullptr, BHID_DataObject, IID_PPV_ARGS(&data)))) {
            result = init->Initialize(nullptr, data, nullptr);
        }
        if (data != nullptr)
            data->Release();
        if (array != nullptr)
            array->Release();
        for (PIDLIST_ABSOLUTE item : items)
            CoTaskMemFree(item);
    }
    IContextMenu* menu = nullptr;
    if (SUCCEEDED(result))
        init->QueryInterface(IID_PPV_ARGS(&menu));
    init->Release();
    return menu;
}

// The submenu Explorer would show, written to `out` as "MusicDesk:" then each entry's verb, a tab and its label,
// one per line; "" when there is none. Returns the HRESULT of the first step that failed, else S_OK.
extern "C" __declspec(dllexport) HRESULT __stdcall MusicDeskMenu_Entries(const wchar_t* const* paths, int count,
                                                                        BOOL background, wchar_t* out, int size) {
    out[0] = L'\0';
    IContextMenu* menu = make_menu(paths, count, background);
    if (menu == nullptr)
        return E_INVALIDARG;
    HMENU host = CreatePopupMenu();
    HRESULT result = menu->QueryContextMenu(host, 0, 100, 0x7FFF, CMF_NORMAL);
    std::wstring text;
    if (SUCCEEDED(result) && GetMenuItemCount(host) == 1) {
        wchar_t label[64];
        GetMenuStringW(host, 0, label, ARRAYSIZE(label), MF_BYPOSITION);
        text = std::wstring(label) + L":";
        HMENU submenu = GetSubMenu(host, 0);
        for (int i = 0; i < GetMenuItemCount(submenu); ++i) {
            UINT id = GetMenuItemID(submenu, i);
            if (id == static_cast<UINT>(-1) || id == 0)
                continue;  // the separator
            wchar_t verb[64];
            wchar_t entry_label[256];
            if (SUCCEEDED(menu->GetCommandString(id - 100, GCS_VERBW, nullptr, reinterpret_cast<LPSTR>(verb),
                                                 ARRAYSIZE(verb))) &&
                GetMenuStringW(submenu, i, entry_label, ARRAYSIZE(entry_label), MF_BYPOSITION) > 0)
                text += std::wstring(L"\n") + verb + L"\t" + entry_label;
        }
    }
    DestroyMenu(host);
    menu->Release();
    return SUCCEEDED(result) ? StringCchCopyW(out, size, text.c_str()) : result;
}

// Explorer's menu as labels, one per line, after the handler adds to a menu already holding `existing` (labels,
// one per line; "MusicDesk" as a submenu, as the handler's own). For the placement tests.
extern "C" __declspec(dllexport) HRESULT __stdcall MusicDeskMenu_Place(const wchar_t* const* paths, int count,
                                                                      BOOL background, const wchar_t* existing,
                                                                      wchar_t* out, int size) {
    out[0] = L'\0';
    IContextMenu* menu = make_menu(paths, count, background);
    if (menu == nullptr)
        return E_INVALIDARG;
    HMENU host = CreatePopupMenu();
    std::wstring labels = existing;
    size_t start = 0;
    while (start < labels.size()) {
        size_t end = labels.find(L'\n', start);
        std::wstring label = labels.substr(start, end == std::wstring::npos ? std::wstring::npos : end - start);
        if (label == L"MusicDesk")
            AppendMenuW(host, MF_POPUP, reinterpret_cast<UINT_PTR>(CreatePopupMenu()), label.c_str());
        else if (!label.empty())
            AppendMenuW(host, MF_STRING, 1, label.c_str());
        if (end == std::wstring::npos)
            break;
        start = end + 1;
    }
    HRESULT result = menu->QueryContextMenu(host, 1, 100, 0x7FFF, CMF_NORMAL);
    std::wstring text;
    for (int i = 0; i < GetMenuItemCount(host); ++i)
        text += (i ? L"\n" : L"") + item_text(host, i);
    DestroyMenu(host);
    menu->Release();
    return SUCCEEDED(result) ? StringCchCopyW(out, size, text.c_str()) : result;
}

// The command line a click on `verb` would start (with `exe` as MusicDesk.exe), written to `out`; its list file is
// written as for real, nothing is started.
extern "C" __declspec(dllexport) HRESULT __stdcall MusicDeskMenu_Command(const wchar_t* const* paths, int count,
                                                                        BOOL background, const wchar_t* verb,
                                                                        const wchar_t* exe, wchar_t* out, int size) {
    out[0] = L'\0';
    IContextMenu* menu = make_menu(paths, count, background);
    if (menu == nullptr)
        return E_INVALIDARG;
    HMENU host = CreatePopupMenu();
    menu->QueryContextMenu(host, 0, 100, 0x7FFF, CMF_NORMAL);
    DestroyMenu(host);
    const Selection& selection = static_cast<MusicDeskMenu*>(menu)->selection();
    const Entry* entry = nullptr;
    for (const Entry* candidate : entries_for(selection))
        if (wcscmp(candidate->verb, verb) == 0)
            entry = candidate;
    std::wstring command = entry != nullptr ? command_for(*entry, selection, exe) : L"";
    menu->Release();
    return command.empty() ? E_FAIL : StringCchCopyW(out, size, command.c_str());
}
