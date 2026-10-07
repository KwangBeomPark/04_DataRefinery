param(
    [string]$InstallerPath
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
    throw 'This installer smoke test runs only on an ephemeral CI runner.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$versionMatch = Select-String -Path (Join-Path $projectRoot 'src\version.py') -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionMatch) {
    throw 'Could not read the application version.'
}
$version = $versionMatch.Matches[0].Groups[1].Value
if (-not $InstallerPath) {
    $InstallerPath = Join-Path $projectRoot "dist\staging\App04_DataRefinery_Setup_v$version.exe"
}
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$installDir = Join-Path $env:RUNNER_TEMP "DataRefinery-Smoke-$([guid]::NewGuid().ToString('N'))"
$appExe = Join-Path $installDir "App04_DataRefinery_v$version.exe"

$install = Start-Process -FilePath $installer -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=$installDir") -WindowStyle Hidden -PassThru -Wait
if ($install.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $appExe)) {
    throw "Installation failed: exit code $($install.ExitCode); expected $appExe"
}

$uninstaller = Join-Path $installDir 'unins000.exe'
$userSetting = Join-Path $installDir 'UserSetting'
if (-not (Test-Path -LiteralPath $userSetting -PathType Container)) {
    throw 'Installer did not create UserSetting.'
}
$preservedSettings = Join-Path $userSetting 'settings.json'
Set-Content -LiteralPath $preservedSettings -Value '{"smoke_test":true}' -Encoding utf8
$settingsHash = (Get-FileHash -LiteralPath $preservedSettings).Hash
New-Item -ItemType Directory -Path (Join-Path $userSetting 'datasets'),(Join-Path $userSetting 'logs') -Force | Out-Null
$datasetSentinel = Join-Path $userSetting 'datasets\retained-data.txt'
$logSentinel = Join-Path $userSetting 'logs\retained-log.txt'
Set-Content -LiteralPath $datasetSentinel -Value 'dataset workspace sentinel'
Set-Content -LiteralPath $logSentinel -Value 'diagnostic log sentinel'
$upgrade = Start-Process -FilePath $installer -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=$installDir") -WindowStyle Hidden -PassThru -Wait
if ($upgrade.ExitCode -ne 0 -or (Get-FileHash -LiteralPath $preservedSettings).Hash -ne $settingsHash -or
    -not (Test-Path -LiteralPath $datasetSentinel) -or -not (Test-Path -LiteralPath $logSentinel)) {
    throw 'Reinstallation changed or removed UserSetting data.'
}
if (-not (Test-Path -LiteralPath $uninstaller)) {
    throw "Uninstaller missing: $uninstaller"
}
$uninstall = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -WindowStyle Hidden -PassThru -Wait
if ($uninstall.ExitCode -ne 0 -or (Test-Path -LiteralPath $appExe)) {
    throw "Uninstallation failed: exit code $($uninstall.ExitCode)"
}
if (-not (Test-Path -LiteralPath $preservedSettings) -or (Get-FileHash -LiteralPath $preservedSettings).Hash -ne $settingsHash -or
    -not (Test-Path -LiteralPath $datasetSentinel) -or -not (Test-Path -LiteralPath $logSentinel)) {
    throw 'Uninstallation removed UserSetting data.'
}
Write-Host 'Installer install/reinstall/uninstall and UserSetting preservation checks passed.'
