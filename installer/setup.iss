; Build with scripts/build.ps1; sign with scripts/sign.ps1.
#if VER < EncodeVer(6, 7, 0)
  #error Inno Setup 6.7+ or 7 is required
#endif
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
RestartApplications=no
VersionInfoVersion={#AppVersion}
VersionInfoProductVersion={#AppVersion}
#ifdef ReleaseSign
SignTool=DataRefineryReleaseSign
SignedUninstaller=yes
SignedUninstallerDir={#UninstallerTempDir}
#endif

[Languages]
Name: "english"; MessagesFile: "compiler:Default.isl"
Name: "korean"; MessagesFile: "compiler:Languages\Korean.isl"

[Files]
Source: "{#AppSourceDir}\*"; DestDir: "{app}"; Excludes: "UserSetting\*"; Flags: ignoreversion recursesubdirs createallsubdirs; BeforeInstall: EnsureUpgradeReady

[Dirs]
; Keep user configuration, logs, presets, and dataset workspaces on uninstall.
Name: "{app}\UserSetting"; Flags: uninsneveruninstall

[InstallDelete]
; Preserve the previous runtime on cancellation/failure. Do not delete _internal
; before copying the verified replacement. Stale-library cleanup needs native QA.
Type: files; Name: "{userprograms}\{#LegacyAppName}.lnk"
Type: files; Name: "{autodesktop}\{#LegacyAppName}.lnk"

[Icons]
Name: "{userprograms}\{#MyAppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"
Name: "{autodesktop}\{#MyAppName}"; Filename: "{app}\{#AppExeName}"; WorkingDir: "{app}"

[Run]
Filename: "{app}\{#AppExeName}"; Description: "Launch {#MyAppName}"; Flags: nowait postinstall skipifsilent

#define PreviousAppId StringChange(SetupSetting("AppId"), "{{", "{")
#define ExpectedAppHash GetSHA256OfFile(AppSourceDir + "\" + AppExeName)

[CustomMessages]
english.UpgradeBlocked=Close Data Refinery and retry setup. An installed executable is still in use or cannot be replaced.
korean.UpgradeBlocked=Data Refinery를 종료한 후 설치를 다시 실행하세요. 설치된 실행 파일이 사용 중이거나 교체할 수 없습니다.

[Code]
var
  OlderNames, OlderHashes: TStringList;
  PreviousInstallOwned, UpgradeChecked: Boolean;

function GetAppDir: String;
begin
  try
    Result := ExpandConstant('{app}');
  except
    try
      Result := WizardDirValue;
    except
      Result := ExpandConstant('{localappdata}\Programs\Data Refinery');
    end;
  end;
end;

function HasPreviousOwnedInstall: Boolean;
var
  Key, Location: String;
begin
  Key := 'Software\Microsoft\Windows\CurrentVersion\Uninstall\{#PreviousAppId}_is1';
  Result := False;
  if RegQueryStringValue(HKCU64, Key, 'InstallLocation', Location) then
    Result := CompareText(AddBackslash(Location), AddBackslash(GetAppDir)) = 0;
  if not Result then
    if RegQueryStringValue(HKCU32, Key, 'InstallLocation', Location) then
      Result := CompareText(AddBackslash(Location), AddBackslash(GetAppDir)) = 0;
end;

function ParseAppVersion(Value: String; var Major, Minor, Patch: Integer): Boolean;
var
  I, Part, Number: Integer;
  Component: String;
begin
  Result := False;
  for Part := 0 to 2 do begin
    I := Pos('.', Value);
    if Part < 2 then begin
      if I = 0 then Exit;
      Component := Copy(Value, 1, I - 1);
      Delete(Value, 1, I);
    end else Component := Value;
    if Component = '' then Exit;
    for I := 1 to Length(Component) do
      if (Component[I] < '0') or (Component[I] > '9') then Exit;
    Number := StrToIntDef(Component, -1);
    if Number < 0 then Exit;
    { Only the canonical numeric filename contract is eligible for deletion. }
    if IntToStr(Number) <> Component then Exit;
    case Part of
      0: Major := Number;
      1: Minor := Number;
      2: Patch := Number;
    end;
  end;
  Result := True;
end;

function IsOlderAppExecutable(Name: String): Boolean;
var
  OldMajor, OldMinor, OldPatch, NewMajor, NewMinor, NewPatch: Integer;
  Prefix, Version: String;
begin
  Result := False;
  Prefix := 'App04_DataRefinery_v';
  if Copy(Name, 1, Length(Prefix)) <> Prefix then Exit;
  if Copy(Name, Length(Name) - 3, 4) <> '.exe' then Exit;
  Version := Copy(Name, Length(Prefix) + 1, Length(Name) - Length(Prefix) - 4);
  if not ParseAppVersion(Version, OldMajor, OldMinor, OldPatch) then Exit;
  if not ParseAppVersion('{#AppVersion}', NewMajor, NewMinor, NewPatch) then Exit;
  Result := (OldMajor < NewMajor) or
    ((OldMajor = NewMajor) and (OldMinor < NewMinor)) or
    ((OldMajor = NewMajor) and (OldMinor = NewMinor) and (OldPatch < NewPatch));
end;

function PrepareToInstall(var NeedsRestart: Boolean): String;
var
  Entry: TFindRec;
  Target: String;
begin
  Result := '';
  if OlderNames = nil then begin
    OlderNames := TStringList.Create;
    OlderHashes := TStringList.Create;
  end;
  OlderNames.Clear;
  OlderHashes.Clear;
  UpgradeChecked := False;
  { Capture ownership before setup updates its own uninstall registration. }
  PreviousInstallOwned := HasPreviousOwnedInstall;
  if not PreviousInstallOwned then begin
    Log('Previous installation ownership/path is unproven; obsolete EXE cleanup skipped.');
    Exit;
  end;
  if FindFirst(AddBackslash(GetAppDir) + 'App04_DataRefinery_v*.exe', Entry) then begin
    try
      repeat
        if (Entry.Attributes and FILE_ATTRIBUTE_DIRECTORY) = 0 then
          if IsOlderAppExecutable(Entry.Name) then begin
            Target := AddBackslash(GetAppDir) + Entry.Name;
            try
              OlderHashes.Add(GetSHA256OfFile(Target));
              OlderNames.Add(Entry.Name);
            except
              Result := CustomMessage('UpgradeBlocked');
              Exit;
            end;
          end;
      until not FindNext(Entry);
    finally
      FindClose(Entry);
    end;
  end;
end;

procedure RegisterExtraCloseApplicationsResources;
var
  I: Integer;
begin
  if OlderNames = nil then Exit;
  for I := 0 to OlderNames.Count - 1 do begin
#if VER >= EncodeVer(7, 0, 0)
    RegisterExtraCloseApplicationsResource(AddBackslash(GetAppDir) + OlderNames[I]);
#else
    RegisterExtraCloseApplicationsResource(False, AddBackslash(GetAppDir) + OlderNames[I]);
#endif
  end;
end;

procedure CheckExecutableReplaceable(Target: String);
var
  Probe: TFileStream;
begin
  if not FileExists(Target) then Exit;
  try
    Probe := TFileStream.Create(Target, fmOpenReadWrite or fmShareExclusive);
    Probe.Free;
  except
    RaiseException(CustomMessage('UpgradeBlocked'));
  end;
end;

procedure EnsureUpgradeReady;
var
  I: Integer;
begin
  if UpgradeChecked then Exit;
  { First file callback: Restart Manager has run, payload copying has not. }
  CheckExecutableReplaceable(AddBackslash(GetAppDir) + '{#AppExeName}');
  if OlderNames <> nil then
    for I := 0 to OlderNames.Count - 1 do
      CheckExecutableReplaceable(AddBackslash(GetAppDir) + OlderNames[I]);
  UpgradeChecked := True;
end;

procedure CurStepChanged(CurStep: TSetupStep);
var
  I: Integer;
  Target: String;
begin
  if (CurStep <> ssDone) or not PreviousInstallOwned then Exit;
  { No cleanup after cancel/failure or when the replacement is not yet installed. }
  try
    if CompareText(GetSHA256OfFile(AddBackslash(GetAppDir) + '{#AppExeName}'), '{#ExpectedAppHash}') <> 0 then Exit;
    for I := 0 to OlderNames.Count - 1 do begin
      Target := AddBackslash(GetAppDir) + OlderNames[I];
      if FileExists(Target) then
        if CompareText(GetSHA256OfFile(Target), OlderHashes[I]) = 0 then
          if not DeleteFile(Target) then Log('Obsolete application EXE retained: ' + OlderNames[I]);
    end;
  except
    Log('Obsolete application EXE cleanup skipped: ' + GetExceptionMessage);
  end;
end;

procedure DeinitializeSetup;
begin
  if OlderNames <> nil then OlderNames.Free;
  if OlderHashes <> nil then OlderHashes.Free;
end;
