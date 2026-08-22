; CoProducer — one-click Windows installer (Inno Setup 6)
; Everything needed to run: frozen app + Python libs + FFmpeg + docs + shortcuts
; Output: packaging\output\CoProducer-Setup-1.0.0-beta.exe
;
; Build:  powershell -ExecutionPolicy Bypass -File packaging\build_installer.ps1
; Or:     BUILD_INSTALLER.bat  (project root)

#define MyAppName "CoProducer"
#define MyAppVersion "1.0.0-beta"
#define MyAppPublisher "NoDAW Labs"
#define MyAppURL "https://nodaw.com"
#define MyAppExeName "CoProducer.exe"
#define MyAppId "{{A7C3E91B-4D2F-4E8A-9B1C-CoProducer100B}"

[Setup]
AppId={#MyAppId}
AppName={#MyAppName}
AppVersion={#MyAppVersion}
AppVerName={#MyAppName} {#MyAppVersion}
AppPublisher={#MyAppPublisher}
AppPublisherURL={#MyAppURL}
AppSupportURL={#MyAppURL}
DefaultDirName={autopf}\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
LicenseFile=..\LICENSE.txt
InfoBeforeFile=installer_welcome.txt
InfoAfterFile=installer_done.txt
OutputDir=output
OutputBaseFilename=CoProducer-Setup-{#MyAppVersion}
SetupIconFile=..\assets\icon.ico
Compression=lzma2/ultra64
SolidCompression=yes
WizardStyle=modern
WizardSizePercent=110
PrivilegesRequired=admin
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
UninstallDisplayIcon={app}\{#MyAppExeName}
UninstallDisplayName={#MyAppName} {#MyAppVersion}
VersionInfoVersion=1.0.0.0
VersionInfoCompany={#MyAppPublisher}
VersionInfoDescription={#MyAppName} AI production assistant installer
VersionInfoProductName={#MyAppName}
VersionInfoProductVersion=1.0.0.0
MinVersion=10.0
CloseApplications=yes
RestartApplications=no
ChangesAssociations=no
DisableWelcomePage=no
SetupLogging=yes
; Allow reinstall / upgrade over previous beta
AllowNoIcons=yes
UsePreviousAppDir=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"

[Messages]
WelcomeLabel1=Welcome to CoProducer Setup
WelcomeLabel2=This will install CoProducer {#MyAppVersion} on your computer.%n%nEverything is included — no separate Python, FFmpeg, or library installs.%n%nClick Next to continue.

[Tasks]
Name: "desktopicon"; Description: "Create a &desktop shortcut"; GroupDescription: "Shortcuts:"; Flags: checkedonce
Name: "startmenu"; Description: "Create a &Start Menu folder"; GroupDescription: "Shortcuts:"; Flags: checkedonce

[Files]
; Full frozen application tree (PyInstaller onedir) — includes Python runtime,
; PySide6, numpy/scipy/librosa/pedalboard, FFmpeg under runtime\ffmpeg\bin, assets
Source: "dist\CoProducer\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs
Source: "..\LICENSE.txt"; DestDir: "{app}"; Flags: ignoreversion
; Quick-start beside the app
Source: "installer_readme.txt"; DestDir: "{app}"; DestName: "README_INSTALL.txt"; Flags: ignoreversion

[Dirs]
; Writable user data (reports / repairs / logs)
Name: "{app}\reports"; Permissions: users-modify
Name: "{app}\reports\html"; Permissions: users-modify
Name: "{app}\reports\json"; Permissions: users-modify
Name: "{app}\reports\txt"; Permissions: users-modify
Name: "{app}\reports\csv"; Permissions: users-modify
Name: "{app}\reports\history"; Permissions: users-modify
Name: "{app}\exports"; Permissions: users-modify
Name: "{app}\exports\repairs"; Permissions: users-modify
Name: "{app}\exports\previews"; Permissions: users-modify
Name: "{app}\exports\eq_preview"; Permissions: users-modify
Name: "{app}\exports\converts"; Permissions: users-modify
Name: "{app}\exports\trims"; Permissions: users-modify
Name: "{app}\exports\spectral"; Permissions: users-modify
Name: "{app}\config"; Permissions: users-modify
Name: "{app}\logs"; Permissions: users-modify
Name: "{app}\input"; Permissions: users-modify
Name: "{app}\input\song"; Permissions: users-modify
Name: "{app}\input\reference"; Permissions: users-modify
Name: "{app}\input\batch"; Permissions: users-modify
Name: "{app}\input\album"; Permissions: users-modify

[Icons]
Name: "{group}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"; Comment: "CoProducer — analyze, repair, A/B"; Tasks: startmenu
Name: "{group}\User Guide"; Filename: "{app}\docs\USER_GUIDE.md"; Tasks: startmenu
Name: "{group}\Uninstall {#MyAppName}"; Filename: "{uninstallexe}"; Tasks: startmenu
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#MyAppExeName}"; WorkingDir: "{app}"; IconFilename: "{app}\assets\icon.ico"; Comment: "CoProducer"; Tasks: desktopicon

[Run]
; Launch GUI after install (windowed EXE — no console)
Filename: "{app}\{#MyAppExeName}"; Description: "Launch {#MyAppName} now"; Flags: nowait postinstall skipifsilent; WorkingDir: "{app}"

[UninstallDelete]
Type: filesandordirs; Name: "{app}\logs"
Type: filesandordirs; Name: "{app}\__pycache__"
Type: filesandordirs; Name: "{app}\exports\ab_playback_cache"

[Code]
function InitializeSetup(): Boolean;
begin
  Result := True;
end;

function NextButtonClick(CurPageID: Integer): Boolean;
begin
  Result := True;
end;
