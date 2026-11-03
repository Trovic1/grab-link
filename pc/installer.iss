; Builds GrabSetup.exe: installs Grab for the current user (no admin needed).
#define AppVer GetEnv("GRAB_VERSION")

[Setup]
AppId={{8C1E7A52-6B1F-4C1C-9E0B-5A7E3D2B9F11}
AppName=Grab
AppVersion={#AppVer}
AppPublisher=Trovic
DefaultDirName={localappdata}\Programs\Grab
DisableProgramGroupPage=yes
DisableDirPage=yes
PrivilegesRequired=lowest
OutputDir=..\Output
OutputBaseFilename=GrabSetup
SetupIconFile=grab.ico
UninstallDisplayIcon={app}\Grab.exe
UninstallDisplayName=Grab
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=force

[Tasks]
Name: "desktopicon"; Description: "Put a Grab shortcut on my desktop"

[Files]
Source: "..\dist\Grab\*"; DestDir: "{app}"; Flags: recursesubdirs ignoreversion

[Icons]
Name: "{autoprograms}\Grab"; Filename: "{app}\Grab.exe"
Name: "{autodesktop}\Grab"; Filename: "{app}\Grab.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\Grab.exe"; Description: "Open Grab now"; Flags: nowait postinstall skipifsilent

[UninstallRun]
Filename: "{cmd}"; Parameters: "/c taskkill /im Grab.exe /f"; Flags: runhidden; RunOnceId: "StopGrab"
