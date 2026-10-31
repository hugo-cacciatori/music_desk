; Inno Setup script for Refrain, the C# app (SPEC §13, §36 "10."). Built by app/packaging/build_installer.ps1, which
; passes /DAppVersion, /DSource (the published app) and /DOutputDir, after the release gate has passed.
;
; It upgrades every earlier version in place, MusicDesk (up to 0.11) and the Python Refrain (0.12, 0.13): the same
; AppId, so Windows sees one app, one Apps entry and one uninstaller. Refrain goes to its own folder; an earlier
; version's folder elsewhere (MusicDesk's) is deleted after the install, its shortcuts and menu keys with it. Their
; data (%LOCALAPPDATA%\MusicDesk or %LOCALAPPDATA%\Refrain) is migrated by the new app's first start (Migration.cs).
; Installs for all users in Program Files by default (admin); the first page also offers "only for me" (no admin,
; in %LOCALAPPDATA%\Programs). Refrain starts
; from File Explorer's right-click menu (Open in Refrain and the other entries), and on its own from the Start menu's
; Refrain folder (Refrain, Uninstall Refrain), Windows search and an optional desktop icon; the last page offers to
; start it (SPEC §37). The uninstaller keeps the app's own data unless the user asks to delete it. Music folders are
; never touched.
;
; The right-click menu's handler (SPEC §31) is installed under a name of its own per version, so an update never
; replaces the DLL File Explorer has loaded: the registry points at the new file, and the next right-click loads it.
; Older copies Explorer still holds (the Python app's included) are moved to the Temp folder, so the folder can go.

#ifndef AppVersion
  #error AppVersion must be defined (run app/packaging/build_installer.ps1)
#endif
#ifndef Source
  #error Source must be defined (run app/packaging/build_installer.ps1)
#endif
#ifndef OutputDir
  #error OutputDir must be defined (run app/packaging/build_installer.ps1)
#endif
#ifndef Wizard
  #error Wizard must be defined (run app/packaging/build_installer.ps1)
#endif

#define AppName "Refrain"
; The AppId's GUID, the Python app's: the C# app is its upgrade. Never change it.
#define AppGuid "AF5F6B62-C2F8-41D7-8FBE-124F1D693BF9"
#define AppExe "Refrain.exe"
#define MenuDll "RefrainMenu-" + AppVersion + ".dll"
; The C# handler's CLSID, as in app/RefrainMenu ("{{" is one brace for Inno): never change it.
#define MenuClsid "{{5C0F7D42-8A61-4C3E-9B1E-2D7F4A6B9E13}"
; The Python app's handler CLSID: its keys are deleted, or two submenus would show.
#define PythonMenuClsid "{{366C8DAE-145F-40D0-B273-8AFE153DD3F5}"
; The handler's ContextMenuHandlers key: it sorts right after 7-Zip's (Hugo, 2026-10-24; the Python app's name too).
#define MenuKey "8Refrain"

[Setup]
AppId={{{#AppGuid}}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisherURL=https://github.com/hugo-cacciatori/refrain
AppSupportURL=https://github.com/hugo-cacciatori/refrain/issues
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
; Always Refrain's own folder and Start menu group, even over MusicDesk (its folder goes after the install, [Code]).
UsePreviousAppDir=no
UsePreviousGroup=no
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
; Windows 10 1809 or later, as the Windows App SDK needs.
MinVersion=10.0.17763
LicenseFile=..\..\LICENSE
SetupIconFile=..\Refrain\Assets\refrain.ico
; The wizard images at every display scale, drawn by build_installer.ps1 into the build folder.
WizardImageFile={#Wizard}\large-164x314.bmp,{#Wizard}\large-192x386.bmp,{#Wizard}\large-246x459.bmp,{#Wizard}\large-273x556.bmp,{#Wizard}\large-328x604.bmp,{#Wizard}\large-355x700.bmp,{#Wizard}\large-410x797.bmp
WizardSmallImageFile={#Wizard}\small-55x58.bmp,{#Wizard}\small-64x68.bmp,{#Wizard}\small-83x80.bmp,{#Wizard}\small-92x97.bmp,{#Wizard}\small-110x106.bmp,{#Wizard}\small-119x123.bmp,{#Wizard}\small-138x140.bmp
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir={#OutputDir}
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
; Closes a running Refrain (the Python app or this one) holding files in the app's folder.
CloseApplications=yes
RestartApplications=no
; Tells Explorer its menus changed (SHChangeNotify) at the end of install and uninstall.
ChangesAssociations=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
; Ticked by default (Hugo, 2026-10-31; SPEC §37).
Name: "desktopicon"; Description: "Create a &desktop icon"; GroupDescription: "Additional shortcuts:"

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Excludes: "RefrainMenu.dll"; Flags: ignoreversion recursesubdirs createallsubdirs
; Only if missing: this version, already installed, may be loaded by Explorer (and it is the same file).
Source: "{#Source}\RefrainMenu.dll"; DestDir: "{app}"; DestName: "{#MenuDll}"; Flags: ignoreversion onlyifdoesntexist

[InstallDelete]
; The Python app's program files (PyInstaller's folder); its Refrain.exe and handler DLLs are replaced or removed by
; the files above and [Code].
Type: filesandordirs; Name: "{app}\_internal"
; What earlier versions installed and this one doesn't: the `refrain` keyword's helper (SPEC §36 "11.") and the loose
; Start menu shortcut of 0.2.1; a desktop icon left unticked this time goes too.
Type: files; Name: "{app}\RefrainHere.exe"
Type: files; Name: "{autoprograms}\{#AppName}.lnk"
Type: files; Name: "{autodesktop}\{#AppName}.lnk"; Tasks: not desktopicon
; MusicDesk's (up to 0.11): its Start menu folder, loose shortcut and desktop icon.
Type: filesandordirs; Name: "{autoprograms}\MusicDesk"
Type: files; Name: "{autoprograms}\MusicDesk.lnk"
Type: files; Name: "{autodesktop}\MusicDesk.lnk"

[Icons]
; The Start menu's Refrain folder, which Windows search finds too (SPEC §37): the app, and the uninstaller (Start's own
; Uninstall button only opens Settings for classic apps, SPEC §13).
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Search your music library and its lyrics."
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"; Comment: "Removes Refrain. Your music folders are not touched."
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Comment: "Search your music library and its lyrics."; Tasks: desktopicon

[Run]
; Unticked by default (Hugo, 2026-10-31; SPEC §37); as the user who ran the setup, not as administrator.
Filename: "{app}\{#AppExe}"; Description: "Start Refrain"; Flags: nowait postinstall skipifsilent unchecked

[Registry]
; The `refrain` keyword is gone (SPEC §36 "11."): its App Paths key, from earlier versions, is deleted.
; HKA: all users on an admin install, this user on a per-user one.
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\refrain.exe"; ValueType: none; Flags: deletekey
; MusicDesk's keyword and handler keys (`8MusicDesk`, and `MusicDesk` up to 0.8.17), or two submenus would show.
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\musicdesk.exe"; ValueType: none; Flags: deletekey
Root: HKA; Subkey: "Software\Classes\*\shellex\ContextMenuHandlers\8MusicDesk"; ValueType: none; Flags: deletekey
Root: HKA; Subkey: "Software\Classes\Directory\shellex\ContextMenuHandlers\8MusicDesk"; ValueType: none; Flags: deletekey
Root: HKA; Subkey: "Software\Classes\Directory\Background\shellex\ContextMenuHandlers\8MusicDesk"; ValueType: none; Flags: deletekey
Root: HKA; Subkey: "Software\Classes\*\shellex\ContextMenuHandlers\MusicDesk"; ValueType: none; Flags: deletekey
Root: HKA; Subkey: "Software\Classes\Directory\shellex\ContextMenuHandlers\MusicDesk"; ValueType: none; Flags: deletekey
Root: HKA; Subkey: "Software\Classes\Directory\Background\shellex\ContextMenuHandlers\MusicDesk"; ValueType: none; Flags: deletekey
; The Python app's handler class goes (MusicDesk's too: the same CLSID); the ContextMenuHandlers keys below now name
; the C# handler.
Root: HKA; Subkey: "Software\Classes\CLSID\{#PythonMenuClsid}"; ValueType: none; Flags: deletekey
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved"; ValueType: none; ValueName: "{#PythonMenuClsid}"; Flags: deletevalue
; The Refrain submenu in File Explorer's right-click menu (SPEC §31): the handler's COM class, where it shows (files,
; folders, a folder's empty space), and the approved list some policies require. HKA, as above.
Root: HKA; Subkey: "Software\Classes\CLSID\{#MenuClsid}"; ValueType: string; ValueName: ""; ValueData: "Refrain menu"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\CLSID\{#MenuClsid}\InprocServer32"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MenuDll}"
Root: HKA; Subkey: "Software\Classes\CLSID\{#MenuClsid}\InprocServer32"; ValueType: string; ValueName: "ThreadingModel"; ValueData: "Apartment"
Root: HKA; Subkey: "Software\Classes\*\shellex\ContextMenuHandlers\{#MenuKey}"; ValueType: string; ValueName: ""; ValueData: "{#MenuClsid}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Directory\shellex\ContextMenuHandlers\{#MenuKey}"; ValueType: string; ValueName: ""; ValueData: "{#MenuClsid}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Directory\Background\shellex\ContextMenuHandlers\{#MenuKey}"; ValueType: string; ValueName: ""; ValueData: "{#MenuClsid}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved"; ValueType: string; ValueName: "{#MenuClsid}"; ValueData: "Refrain menu"; Flags: uninsdeletevalue

[Code]
// The files in `Dir` matching `Pattern` other than `Keep`: deleted, or, when Explorer has one loaded (a menu handler
// can't be deleted then, but it can be renamed), moved to the Temp folder, which Windows cleans.
procedure RemoveFiles(Dir, Pattern, Keep: String);
var
  Name: String;
  Found: TFindRec;
begin
  if FindFirst(Dir + '\' + Pattern, Found) then
  try
    repeat
      Name := Found.Name;
      if (CompareText(Name, Keep) <> 0) and not DeleteFile(Dir + '\' + Name) then
        RenameFile(Dir + '\' + Name, AddBackslash(GetTempDir) + 'Refrain-' +
                   GetDateTimeString('yyyymmddhhnnss', #0, #0) + '-' + Name);
    until not FindNext(Found);
  finally
    FindClose(Found);
  end;
end;

procedure RemoveMenuHandlers(Keep: String);
begin
  RemoveFiles(ExpandConstant('{app}'), 'RefrainMenu-*.dll', Keep);
end;

var
  // An earlier version's program folder other than this one's (MusicDesk's), deleted after the install.
  OldFolder: String;

// Whether a program of that name is running, for any user (tasklist, then find's exit code).
function IsRunning(Exe: String): Boolean;
var
  Code: Integer;
begin
  Result := Exec(ExpandConstant('{cmd}'), '/C tasklist /FI "IMAGENAME eq ' + Exe + '" /NH | find /I "' + Exe + '" > nul',
                 '', SW_HIDE, ewWaitUntilTerminated, Code) and (Code = 0);
end;

// Before anything is copied: where the earlier version lives (its uninstall entry, the same AppId), kept only when it
// is another folder holding that version's program; and a stop while MusicDesk runs (its files would stay half
// deleted, and its index open). A running Refrain in this folder is closed by CloseApplications.
function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Old: String;
begin
  Result := '';
  OldFolder := '';
  if RegQueryStringValue(HKA, 'Software\Microsoft\Windows\CurrentVersion\Uninstall\' + '{' + '{#AppGuid}' + '}_is1',
                         'Inno Setup: App Path', Old) and
     (CompareText(RemoveBackslashUnlessRoot(Old), RemoveBackslashUnlessRoot(ExpandConstant('{app}'))) <> 0) and
     (FileExists(AddBackslash(Old) + 'MusicDesk.exe') or FileExists(AddBackslash(Old) + '{#AppExe}')) then
    OldFolder := RemoveBackslashUnlessRoot(Old);
  if IsRunning('MusicDesk.exe') then
    Result := 'MusicDesk is running. Close it, then run this setup again. Your library and settings are kept.'
  else if (OldFolder <> '') and IsRunning('{#AppExe}') then
    Result := 'Refrain is running. Close its windows, then run this setup again. Your library and settings are kept.';
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    RemoveMenuHandlers('{#MenuDll}');  // older versions', the Python app's included
    if OldFolder <> '' then
    begin
      // A handler DLL Explorer still holds can only be moved; then the old folder goes, its uninstaller with it (this
      // version's uninstaller now answers for the AppId).
      RemoveFiles(OldFolder, 'MusicDeskMenu*.dll', '');
      RemoveFiles(OldFolder, 'RefrainMenu*.dll', '');
      DelTree(OldFolder, True, True, True);
    end;
  end;
end;

// After the files are removed: offer to delete the app's own data too. The answer defaults to No, and a silent
// uninstall keeps it, so an uninstall (or an upgrade through one) never loses the index or the settings.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    RemoveMenuHandlers('');  // one Explorer still has loaded, which the uninstaller couldn't delete
    RemoveDir(ExpandConstant('{app}'));
    DataDir := ExpandConstant('{localappdata}\{#AppName}');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Also delete Refrain''s own data in ' + DataDir + '?' + #13#10#13#10 +
                'It holds the library index, the settings and the logs. Your music folders are not touched.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
