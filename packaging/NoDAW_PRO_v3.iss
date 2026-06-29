#define MyAppName "NoDAW Audio Quality Analyzer PRO"
#define MyAppVersion "3.0.0"
#define MyAppExeName "START_ANALYZER_PRO.bat"

[Setup]
AppId={{89C1EF59-63E7-486B-94A4-4C6285B4B33B}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
DefaultDirName={localappdata}\Programs\NoDAW Audio Quality Analyzer PRO
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputBaseFilename=NoDAW_Audio_Quality_Analyzer_PRO_v3.0.0_Setup
Compression=lzma2
SolidCompression=yes
ArchitecturesAllowed=x64compatible
WizardStyle=modern

[Files]
Source: "..\app\*"; DestDir: "{app}\app"; Excludes: "__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\config\*"; DestDir: "{app}\config"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\input\song\README.txt"; DestDir: "{app}\input\song"; Flags: ignoreversion
Source: "..\input\reference\README.txt"; DestDir: "{app}\input\reference"; Flags: ignoreversion
Source: "..\input\batch\README.txt"; DestDir: "{app}\input\batch"; Flags: ignoreversion
Source: "..\input\album\README.txt"; DestDir: "{app}\input\album"; Flags: ignoreversion
Source: "..\START_ANALYZER_PRO.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\RELEASE_NOTES.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion

[Dirs]
Name: "{app}\reports"
Name: "{app}\exports"
Name: "{app}\logs"

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; Tasks: desktopicon

[Tasks]
Name: "desktopicon"; Description: "Create a desktop shortcut"

[Run]
Filename: "{app}\{#MyAppExeName}"; Parameters: "--mode doctor --no-previews"; Description: "Verify dependencies"; Flags: postinstall nowait skipifsilent
