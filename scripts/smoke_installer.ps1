param(
    [string]$InstallerPath
)

$ErrorActionPreference = 'Stop'
if ([string]::IsNullOrWhiteSpace($env:RUNNER_TEMP)) {
    throw 'This installer smoke test runs only on an ephemeral CI runner.'
}

$projectRoot = Split-Path -Parent $PSScriptRoot
$versionMatch = Select-String -Path (Join-Path $projectRoot 'src\data_refinery.py') -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionMatch) {
    throw 'Could not read the application version.'
}
$version = $versionMatch.Matches[0].Groups[1].Value
if (-not $InstallerPath) {
    $InstallerPath = Join-Path $projectRoot "release\dist\installer\App04_DataRefinery_Setup_v$version.exe"
}
$installer = (Resolve-Path -LiteralPath $InstallerPath).Path
$installDir = Join-Path $env:RUNNER_TEMP "DataRefinery-Smoke-$([guid]::NewGuid().ToString('N'))"
$appExe = Join-Path $installDir "App04_DataRefinery_v$version.exe"

$install = Start-Process -FilePath $installer -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART', "/DIR=$installDir") -WindowStyle Hidden -PassThru -Wait
if ($install.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $appExe)) {
    throw "Installation failed: exit code $($install.ExitCode); expected $appExe"
}

$uninstaller = Join-Path $installDir 'unins000.exe'
if (-not (Test-Path -LiteralPath $uninstaller)) {
    throw "Uninstaller missing: $uninstaller"
}
$uninstall = Start-Process -FilePath $uninstaller -ArgumentList @('/VERYSILENT', '/SUPPRESSMSGBOXES', '/NORESTART') -WindowStyle Hidden -PassThru -Wait
if ($uninstall.ExitCode -ne 0 -or (Test-Path -LiteralPath $appExe)) {
    throw "Uninstallation failed: exit code $($uninstall.ExitCode)"
}
Write-Host 'Installer smoke test passed.'
