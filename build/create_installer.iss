[Setup]
AppName=Librarian
AppPublisher=suncloudsmoon
AppPublisherURL=https://github.com/suncloudsmoon
AppCopyright=Copyright (c) 2025 suncloudsmoon. All rights reserved.
AppVersion=0.5.0.0
AppId={{652CD91D-E131-460E-870E-2A546A01C766}
LicenseFile=build\legal\LICENSE.txt
DefaultDirName={autopf}\suncloudsmoon\Librarian
WizardStyle=modern
DisableProgramGroupPage=yes
AlwaysRestart=yes
UninstallRestartComputer=yes
PrivilegesRequired=admin
PrivilegesRequiredOverridesAllowed=dialog
OutputBaseFilename=LibrarianSetup
OutputDir=/build/installer
SourceDir=../

[Files]
Source: "build\executables\librarian\*"; DestDir: "{app}"; Flags: recursesubdirs
Source: "build\enable_long_path.reg"; DestDir: "{app}"; Check: IsUserMode
Source: "build\legal\*"; DestDir: "{app}"

[Registry]
Root: HKLM; Subkey: "SYSTEM\CurrentControlSet\Control\FileSystem"; ValueType: dword; ValueName: "LongPathsEnabled"; ValueData: 1; Check: IsAdminInstallMode

[Run]
Filename: "{app}\librarian.exe"; Parameters: "--install {code:InstallMode}"

[UninstallRun]
Filename: "{app}\librarian.exe"; Parameters: "--uninstall"

[Code]
function InstallMode(Param: String): String;
begin
  if IsAdminInstallMode() then
    Result := 'system'
  else
    Result := 'user';
end;

function IsUserMode(): Boolean;
begin
  Result:= not IsAdminInstallMode();
end;