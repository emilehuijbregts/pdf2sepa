; PDF2SEPA Windows installer (Inno Setup 6)
; Build: iscc packaging/installer.iss /DMyAppVersion=1.0.1

#define MyAppName "PDF2SEPA"
#define MyAppPublisher "PDF2SEPA"
#ifndef MyAppVersion
  #define MyAppVersion "1.0.0"
#endif

[Setup]
AppId={{A3F8C2E1-9B4D-4F6A-8C1E-2D5F7A9B3C4E}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\PDF2SEPA\app
DisableDirPage=yes
DisableProgramGroupPage=yes
PrivilegesRequired=lowest
OutputDir=output
OutputBaseFilename=PDF2SEPA-Setup-{#MyAppVersion}
Compression=lzma2
SolidCompression=yes
ArchitecturesInstallIn64BitMode=x64compatible

[Languages]
Name: "dutch"; MessagesFile: "compiler:Languages\Dutch.isl"
Name: "english"; MessagesFile: "compiler:Default.isl"

[Files]
Source: "dist\PDF2SEPA\*"; DestDir: "{localappdata}\PDF2SEPA\app"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{userdesktop}\{#MyAppName}"; Filename: "{localappdata}\PDF2SEPA\app\PDF2SEPA.exe"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Bureaublad-snelkoppeling"; GroupDescription: "Extra opties:"; Flags: unchecked

[Code]
procedure WriteDataRootJson;
var
  DataRootPath, JsonPath: string;
  JsonContent: string;
begin
  DataRootPath := ExpandConstant('{localappdata}\PDF2SEPA\data');
  JsonPath := ExpandConstant('{localappdata}\PDF2SEPA\data_root.json');
  JsonContent := '{' + #13#10 +
    '  "user_data_directory": "' + DataRootPath + '"' + #13#10 +
    '}' + #13#10;
  SaveStringToFile(JsonPath, JsonContent, False);
end;

procedure CurStepChanged(CurStep: TSetupStep);
begin
  if CurStep = ssPostInstall then
  begin
    ForceDirectories(ExpandConstant('{localappdata}\PDF2SEPA\data'));
    ForceDirectories(ExpandConstant('{localappdata}\PDF2SEPA\logs'));
    ForceDirectories(ExpandConstant('{localappdata}\PDF2SEPA\backups'));
    if not FileExists(ExpandConstant('{localappdata}\PDF2SEPA\data_root.json')) then
      WriteDataRootJson;
  end;
end;

[Run]
Filename: "{localappdata}\PDF2SEPA\app\PDF2SEPA.exe"; Description: "Start {#MyAppName}"; Flags: nowait postinstall skipifsilent
