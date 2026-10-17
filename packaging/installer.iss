; Inno Setup script for MusicDesk (SPEC §13). Built by packaging/build_installer.py, which passes /DAppVersion
; from music_desk.__version__ after the PyInstaller build has filled dist\MusicDesk.
;
; Installs for all users in Program Files by default (admin); the setup's first page also offers "only for me"
; (no admin, in %LOCALAPPDATA%\Programs). The Start menu gets a MusicDesk folder with the app and "Uninstall
; MusicDesk". The uninstaller keeps the app's own data (%LOCALAPPDATA%\MusicDesk:
; the library index, settings, logs) unless the user asks to delete it. Music folders are never touched.

#ifndef AppVersion
  #error AppVersion must be defined (run packaging/build_installer.py)
#endif

#define AppName "MusicDesk"
#define AppExe "MusicDesk.exe"

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
UninstallDisplayIcon={app}\{#AppExe}
UninstallDisplayName={#AppName}
OutputDir=..\dist\installer
OutputBaseFilename={#AppName}-Setup-{#AppVersion}
Compression=lzma2
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
RestartApplications=no

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "{cm:CreateDesktopIcon}"; GroupDescription: "{cm:AdditionalIcons}"; Flags: unchecked

[Files]
Source: "..\dist\MusicDesk\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

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

[Run]
Filename: "{app}\{#AppExe}"; Description: "{cm:LaunchProgram,{#AppName}}"; Flags: nowait postinstall skipifsilent

[Code]
// After the files are removed: offer to delete the app's own data too. The answer defaults to No, and a silent
// uninstall keeps it, so an uninstall (or an upgrade through one) never loses the index or the settings.
procedure CurUninstallStepChanged(CurUninstallStep: TUninstallStep);
var
  DataDir: String;
begin
  if CurUninstallStep = usPostUninstall then
  begin
    DataDir := ExpandConstant('{localappdata}\{#AppName}');
    if DirExists(DataDir) and not UninstallSilent then
      if MsgBox('Also delete MusicDesk''s own data in ' + DataDir + '?' + #13#10#13#10 +
                'It holds the library index, the settings and the logs. Your music folders are not touched.',
                mbConfirmation, MB_YESNO or MB_DEFBUTTON2) = IDYES then
        DelTree(DataDir, True, True, True);
  end;
end;
