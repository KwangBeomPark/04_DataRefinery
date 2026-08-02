; Build with ISCC and the definitions supplied by build_release.ps1.
#ifndef AppVersion
  #define AppVersion "0.0.0"
#endif
#ifndef AppBundleName
  #define AppBundleName "App04_DataRefinery_v0.0.0"
#endif
#ifndef AppExeName
  #define AppExeName "App04_DataRefinery_v0.0.0.exe"
#endif

#define MyAppName "Data Refinery"
#define LegacyAppName "CSV Modifier"
#define MyAppPublisher "KwangBeomPark"

[Setup]
AppId={{2E1A7E3F-8D78-4DB0-9B62-50B12CD4326F}
AppName={#MyAppName}
AppVersion={#AppVersion}
AppPublisher={#MyAppPublisher}
DefaultDirName={localappdata}\Programs\{#MyAppName}
DefaultGroupName={#MyAppName}
DisableProgramGroupPage=yes
UsePreviousAppDir=no
UsePreviousGroup=no
PrivilegesRequired=lowest
ArchitecturesAllowed=x64compatible
ArchitecturesInstallIn64BitMode=x64compatible
OutputDir=..\dist\installer
OutputBaseFilename=App04_DataRefinery_Setup_v{#AppVersion}
SetupIconFile=..\..\assets\icons\icon.ico
UninstallDisplayIcon={app}\{#AppExeName}
Compression=lzma2/max
SolidCompression=yes
WizardStyle=modern
CloseApplications=yes

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "..\dist\{#AppBundleName}\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[InstallDelete]
; The product name changed from CSV Modifier. App output files are saved beside
; the user's source files, so only obsolete application files and shortcuts move.
Type: filesandordirs; Name: "{localappdata}\Programs\{#LegacyAppName}"
Type: files; Name: "{userprograms}\{#LegacyAppName}.lnk"
Type: files; Name: "{autodesktop}\{#LegacyAppName}.lnk"

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent
