; CoProducer v3.2 — Windows EXE Installer (Inno Setup)
; Build with Inno Setup 6+ (jrsoftware.org/isinfo.php)
; Output: CoProducer_v3.2.0_Setup.exe

#define MyAppName "CoProducer - AI Production Assistant"
#define MyAppShortName "CoProducer"
#define MyAppVersion "3.2.0"
#define MyAppPublisher "NoDAW Labs"
#define MyAppURL "https://nodaw.ai/coproducer"
#define MyAppExeName "START_GUI.bat"

[Setup]
AppId={{A3F8C12D-5E4B-4A7C-9D6F-1B8E2C4D5A6F}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
DefaultDirName={localappdata}\Programs\CoProducer
DefaultGroupName={#MyAppName}
PrivilegesRequired=lowest
OutputBaseFilename=CoProducer_v{#MyAppVersion}_Setup
Compression=lzma2/ultra
SolidCompression=yes
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
WizardStyle=modern
WizardSizePercent=100
DisableProgramGroupPage=yes
SetupIconFile=..\assets\icon.ico
UninstallDisplayIcon={app}\assets\icon.ico
ChangesEnvironment=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Additional shortcuts:"
Name: "venv"; Description: "Create Python virtual environment (recommended)"; GroupDescription: "Setup:"; Flags: checkedonce

[Files]
; Core application
Source: "..\app\*"; DestDir: "{app}\app"; Excludes: "__pycache__\*,*.pyc,*.pyo"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\assets\*"; DestDir: "{app}\assets"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\config\*"; DestDir: "{app}\config"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\docs\*"; DestDir: "{app}\docs"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\CoProducerDesktop.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CoProducer_GUI.py"; DestDir: "{app}"; Flags: ignoreversion

; Design System (UI components, theme, icons)
Source: "..\app\nodaw\ui\*"; DestDir: "{app}\app\nodaw\ui"; Excludes: "__pycache__\*,*.pyc"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\app\nodaw\ui\assets\icons\*"; DestDir: "{app}\app\nodaw\ui\assets\icons"; Flags: ignoreversion recursesubdirs createallsubdirs

; Launchers
Source: "..\START_GUI.bat"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\START_ANALYZER_PRO.bat"; DestDir: "{app}"; Flags: ignoreversion

; Documentation
Source: "..\README.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\CHANGELOG.md"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\requirements.txt"; DestDir: "{app}"; Flags: ignoreversion
Source: "..\pyproject.toml"; DestDir: "{app}"; Flags: ignoreversion

; Input folder readme placeholders
Source: "..\input\song\README.txt"; DestDir: "{app}\input\song"; Flags: ignoreversion
Source: "..\input\reference\README.txt"; DestDir: "{app}\input\reference"; Flags: ignoreversion
Source: "..\input\batch\README.txt"; DestDir: "{app}\input\batch"; Flags: ignoreversion
Source: "..\input\album\README.txt"; DestDir: "{app}\input\album"; Flags: ignoreversion

; Packaging (installer scripts for reference)
Source: "..\packaging\install.ps1"; DestDir: "{app}\packaging"; Flags: ignoreversion

[Dirs]
Name: "{app}\reports"
Name: "{app}\reports\html"
Name: "{app}\reports\json"
Name: "{app}\reports\txt"
Name: "{app}\reports\csv"
Name: "{app}\reports\history"
Name: "{app}\exports"
Name: "{app}\exports\repairs"
Name: "{app}\exports\previews\codecs"
Name: "{app}\exports\previews\streaming"
Name: "{app}\logs"
Name: "{app}\input\song"
Name: "{app}\input\reference"
Name: "{app}\input\batch"
Name: "{app}\input\album"

[Icons]
Name: "{group}\CoProducer"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Comment: "Launch CoProducer - AI Production Assistant"
Name: "{group}\Uninstall CoProducer"; Filename: "{uninstallexe}"
Name: "{autodesktop}\CoProducer"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Tasks: desktopicon; Comment: "Launch CoProducer - AI Production Assistant"

[Run]
Filename: "{app}\packaging\install.ps1"; Parameters: "-ExecutionPolicy Bypass"; WorkingDir: "{app}"; Flags: runhidden runascurrentuser postinstall; Description: "Create virtual environment and install dependencies (recommended before first launch)"
Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; Flags: postinstall nowait skipifsilent shellexec; Description: "Launch CoProducer"

[UninstallRun]
Filename: "{app}\packaging\install.ps1"; Parameters: "-Uninstall"; Flags: runhidden runascurrentuser

[Code]
function InitializeSetup: Boolean;
begin
  Result := True;
end;
