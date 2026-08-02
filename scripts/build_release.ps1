param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

$versionMatch = Select-String -Path 'src\data_refinery.py' -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionMatch) {
    throw 'Could not read __version__ from data_refinery.py.'
}
$appVersion = $versionMatch.Matches[0].Groups[1].Value
$bundleName = "App04_DataRefinery_v$appVersion"
$appExeName = "$bundleName.exe"

if (-not $SkipTests) {
    python -m unittest discover -s tests -v
}

python -m PyInstaller --clean --noconfirm --workpath release\build --distpath release\dist release\packaging\data_refinery.spec
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller build failed.'
}

$isccCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    'C:\Program Files\Inno Setup 7\ISCC.exe',
    'C:\Program Files (x86)\Inno Setup 7\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe',
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
)
$iscc = $isccCandidates | Where-Object { Test-Path $_ } | Select-Object -First 1
if (-not $iscc) {
    throw 'Inno Setup 7 is required. Install its 64-bit edition, then run build_release.ps1 again.'
}

& $iscc "/DAppVersion=$appVersion" "/DAppBundleName=$bundleName" "/DAppExeName=$appExeName" 'release\installer\DataRefinery.iss'
if ($LASTEXITCODE -ne 0) {
    throw 'Inno Setup build failed.'
}

$installerPath = Join-Path $projectRoot "release\dist\installer\App04_DataRefinery_Setup_v$appVersion.exe"
if (-not (Test-Path $installerPath)) {
    throw "Installer was not created: $installerPath"
}
Write-Host "Installer created: $installerPath"
