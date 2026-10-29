; Inno Setup script for MusicDesk (SPEC §13). Built by packaging/build_installer.py, which passes /DAppVersion
; from music_desk.__version__ after the PyInstaller build has filled dist\MusicDesk.
;
; Installs for all users in Program Files by default (admin); the setup's first page also offers "only for me"
; (no admin, in %LOCALAPPDATA%\Programs). The Start menu gets a MusicDesk folder with the app and "Uninstall
; MusicDesk". The uninstaller keeps the app's own data (%LOCALAPPDATA%\MusicDesk:
; the library index, settings, logs) unless the user asks to delete it. Music folders are never touched.
;
; The right-click menu's handler (SPEC §31) is installed under a name of its own per version, so an update never
; replaces the DLL File Explorer has loaded (Inno's restartreplace does nothing on a per-user install): the registry
; points at the new file, and the next right-click loads it. Older copies Explorer still holds are moved to the
; Temp folder, at install and at uninstall, so the app's folder can go.

#ifndef AppVersion
  #error AppVersion must be defined (run packaging/build_installer.py)
#endif

#define AppName "MusicDesk"
#define AppExe "MusicDesk.exe"
#define MenuDll "MusicDeskMenu-" + AppVersion + ".dll"
; The handler's CLSID, as in native\context_menu\menu.cpp ("{{" is one brace for Inno): never change it.
#define MenuClsid "{{366C8DAE-145F-40D0-B273-8AFE153DD3F5}"
; The handler's ContextMenuHandlers key: it sorts right after 7-Zip's ([Registry] below).
#define MenuKey "8MusicDesk"

[Setup]
; The AppId identifies the app for upgrades and the uninstaller: never change it.
AppId={{AF5F6B62-C2F8-41D7-8FBE-124F1D693BF9}
AppName={#AppName}
AppVersion={#AppVersion}
AppVerName={#AppName} {#AppVersion}
AppPublisherURL=https://github.com/hugo-cacciatori/music_desk
AppSupportURL=https://github.com/hugo-cacciatori/music_desk/issues
VersionInfoVersion={#AppVersion}
DefaultDirName={autopf}\{#AppName}
DefaultGroupName={#AppName}
DisableProgramGroupPage=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
LicenseFile=..\LICENSE
SetupIconFile=..\resources\icons\music_desk.ico
; The wizard images at every display scale, drawn into build\wizard by build_installer.py (make_icon.py --wizard).
WizardImageFile=..\build\wizard\large-164x314.bmp,..\build\wizard\large-192x386.bmp,..\build\wizard\large-246x459.bmp,..\build\wizard\large-273x556.bmp,..\build\wizard\large-328x604.bmp,..\build\wizard\large-355x700.bmp,..\build\wizard\large-410x797.bmp
WizardSmallImageFile=..\build\wizard\small-55x58.bmp,..\build\wizard\small-64x68.bmp,..\build\wizard\small-83x80.bmp,..\build\wizard\small-92x97.bmp,..\build\wizard\small-110x106.bmp,..\build\wizard\small-119x123.bmp,..\build\wizard\small-138x140.bmp
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist\v{#AppVersion}
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
; Tells Explorer its menus changed (SHChangeNotify) at the end of install and uninstall.
ChangesAssociations=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\MusicDesk\*"; DestDir: "{app}"; Excludes: "MusicDeskMenu.dll"; Flags: ignoreversion recursesubdirs createallsubdirs
; Only if missing: this version, already installed, may be loaded by Explorer (and it is the same file).
Source: "..\dist\MusicDesk\MusicDeskMenu.dll"; DestDir: "{app}"; DestName: "{#MenuDll}"; Flags: ignoreversion onlyifdoesntexist

[InstallDelete]
; 0.1.0-0.2.x put a loose shortcut at the top of the Start menu's Programs; it moved into the MusicDesk folder.
Type: files; Name: "{autoprograms}\{#AppName}.lnk"

[Icons]
; A Start menu folder with the uninstaller beside the app: Start's own Uninstall button only opens Settings for
; classic apps (SPEC §13).
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"; Comment: "Removes MusicDesk. Your music folders are not touched."
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; The `musicdesk` keyword in File Explorer's address bar (SPEC §29): App Paths finds MusicDeskHere.exe, which opens
; MusicDesk on the folder shown. HKA: all users on an admin install, this user on a per-user one.
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\musicdesk.exe"; ValueType: string; ValueName: ""; ValueData: "{app}\MusicDeskHere.exe"; Flags: uninsdeletekey
; The MusicDesk submenu in File Explorer's right-click menu (SPEC §31): the handler's COM class, where it shows
; (files, folders, a folder's empty space), and the approved list some policies require. HKA, as above.
Root: HKA; Subkey: "Software\Classes\CLSID\{#MenuClsid}"; ValueType: string; ValueName: ""; ValueData: "MusicDesk menu"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\CLSID\{#MenuClsid}\InprocServer32"; ValueType: string; ValueName: ""; ValueData: "{app}\{#MenuDll}"
Root: HKA; Subkey: "Software\Classes\CLSID\{#MenuClsid}\InprocServer32"; ValueType: string; ValueName: "ThreadingModel"; ValueData: "Apartment"
; Explorer calls the handlers in the order of their key names (inferred, SPEC §31): "8MusicDesk" sorts right after
; 7-Zip's "7-Zip", so the submenu shows just below 7-Zip's (Hugo, 2026-10-24). The name shows nowhere else.
; 0.8.17 registered "MusicDesk": its keys are deleted, or the submenu would show twice.
Root: HKA; Subkey: "Software\Classes\*\shellex\ContextMenuHandlers\MusicDesk"; ValueType: none; Flags: deletekey
Root: HKA; Subkey: "Software\Classes\Directory\shellex\ContextMenuHandlers\MusicDesk"; ValueType: none; Flags: deletekey
Root: HKA; Subkey: "Software\Classes\Directory\Background\shellex\ContextMenuHandlers\MusicDesk"; ValueType: none; Flags: deletekey
Root: HKA; Subkey: "Software\Classes\*\shellex\ContextMenuHandlers\{#MenuKey}"; ValueType: string; ValueName: ""; ValueData: "{#MenuClsid}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Directory\shellex\ContextMenuHandlers\{#MenuKey}"; ValueType: string; ValueName: ""; ValueData: "{#MenuClsid}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Classes\Directory\Background\shellex\ContextMenuHandlers\{#MenuKey}"; ValueType: string; ValueName: ""; ValueData: "{#MenuClsid}"; Flags: uninsdeletekey
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\Shell Extensions\Approved"; ValueType: string; ValueName: "{#MenuClsid}"; ValueData: "MusicDesk menu"; Flags: uninsdeletevalue

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// The menu handlers in the app's folder other than `Keep`: deleted, or, when Explorer has one loaded (it can't be
// deleted then, but it can be renamed), moved to the Temp folder, which Windows cleans.
procedure RemoveMenuHandlers(Keep: String);
var
  App, Name: String;
  Found: TFindRec;
begin
  App := ExpandConstant('{app}');
  if FindFirst(App + '\MusicDeskMenu-*.dll', Found) then
  try
    repeat
      Name := Found.Name;
      if (CompareText(Name, Keep) <> 0) and not DeleteFile(App + '\' + Name) then
        RenameFile(App + '\' + Name, AddBackslash(GetTempDir) + 'MusicDesk-' +
                   GetDateTimeString('yyyymmddhhnnss', #0, #0) + '-' + Name);
    until not FindNext(Found);
  finally
    FindClose(Found);
  end;
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RemoveMenuHandlers('{#MenuDll}');
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
      if MsgBox('Also delete MusicDesk''s own data in ' + DataDir + '?' + #13#10#13#10 +
                'It holds the library index, the settings and the logs. Your music folders are not touched.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
