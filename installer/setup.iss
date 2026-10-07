; Build with scripts/build.ps1; sign with scripts/sign.ps1.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppExeName
  #define AppExeName "App04_DataRefinery_v0.0.0.exe"
#endif
#ifndef AppSourceDir
  #error AppSourceDir must identify the complete PyInstaller onedir bundle
#endif
#ifndef ArtifactDir
  #error ArtifactDir must identify the temporary staging directory
#endif
#ifdef ReleaseSign
  #ifndef UninstallerTempDir
    #error UninstallerTempDir is required for signed compilation
  #endif
#endif

#define MyAppName "Data Refinery"
#define LegacyAppName "CSV Modifier"
#define MyAppPublisher "KwangBeomPark"

[Setup]
AppId={{2E1A7E3F-8D78-4DB0-9B62-50B12CD4326F}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppPublisher}
; Program binaries and persistent UserSetting have distinct roles under {app}.
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
DisableDirPage=yes
UsePreviousAppDir=no
UsePreviousGroup=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir={#ArtifactDir}
OutputBaseFilename=App04_DataRefinery_Setup_v{#AppVersion}
SetupIconFile=..\assets\icons\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes
#ifdef ReleaseSign
SignTool=DataRefineryReleaseSign
SignedUninstaller=yes
SignedUninstallerDir={#UninstallerTempDir}
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Excludes: "UserSetting\*"; Flags: ignoreversion recursesubdirs createallsubdirs

[Dirs]
; Keep user configuration, logs, presets, and dataset workspaces on uninstall.
Name: "{app}\UserSetting"; Flags: uninsneveruninstall

[InstallDelete]
; The product name changed from CSV Modifier. App output files are saved beside
; the user's source files, so only obsolete application files and shortcuts move.
; Legacy application folders may contain user files; do not recursively delete them.
Type: files; Name: "{userprograms}\{#LegacyAppName}.lnk"
Type: files; Name: "{autodesktop}\{#LegacyAppName}.lnk"

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
