; Inno Setup script for Refrain, the C# app (SPEC §13, §36 "10."). Built by app/packaging/build_installer.ps1, which
; passes /DAppVersion, /DSource (the published app) and /DOutputDir, after the release gate has passed.
;
; It upgrades the Python app in place: the same AppId, so Windows sees one app, one Apps entry and one uninstaller.
; The Python app's program files go ([InstallDelete]); its data in %LOCALAPPDATA%\Refrain is migrated by the new
; app's first start (Migration.cs), which the last page starts. Installs for all users in Program Files by default
; (admin); the first page also offers "only for me" (no admin, in %LOCALAPPDATA%\Programs). The Start menu gets a
; Refrain folder with the app and "Uninstall Refrain". The uninstaller keeps the app's own data unless the user asks
; to delete it. Music folders are never touched.
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
; The Python app's folder and group are reused, so its program files are replaced where they are.
UsePreviousAppDir=yes
UsePreviousGroup=yes
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
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "{#Source}\*"; DestDir: "{app}"; Excludes: "RefrainMenu.dll"; Flags: ignoreversion recursesubdirs createallsubdirs
; Only if missing: this version, already installed, may be loaded by Explorer (and it is the same file).
Source: "{#Source}\RefrainMenu.dll"; DestDir: "{app}"; DestName: "{#MenuDll}"; Flags: ignoreversion onlyifdoesntexist

[InstallDelete]
; The Python app's program files (PyInstaller's folder); its Refrain.exe, RefrainHere.exe and handler DLLs are
; replaced or removed by the files above and [Code].
Type: filesandordirs; Name: "{app}\_internal"

[Icons]
; A Start menu folder with the uninstaller beside the app: Start's own Uninstall button only opens Settings for
; classic apps (SPEC §13).
Name: "{group}\{#AppName}"; Filename: "{app}\{#AppExe}"
Name: "{group}\Uninstall {#AppName}"; Filename: "{uninstallexe}"; Comment: "Removes Refrain. Your music folders are not touched."
Name: "{autodesktop}\{#AppName}"; Filename: "{app}\{#AppExe}"; Tasks: desktopicon

[Registry]
; The `refrain` keyword in File Explorer's address bar (SPEC §29): App Paths finds RefrainHere.exe, which opens
; Refrain on the folder shown. HKA: all users on an admin install, this user on a per-user one.
Root: HKA; Subkey: "Software\Microsoft\Windows\CurrentVersion\App Paths\refrain.exe"; ValueType: string; ValueName: ""; ValueData: "{app}\RefrainHere.exe"; Flags: uninsdeletekey
; The Python app's handler class goes; the ContextMenuHandlers keys below now name the C# handler.
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

[Run]
; The first start migrates the Python app's settings, library folders and LRCLIB results.
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

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

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
    RemoveMenuHandlers('{#MenuDll}');  // older versions', the Python app's included
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
