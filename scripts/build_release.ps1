param(
    [switch]$SkipTests,
    [switch]$SkipSign,
    [string]$CertificateThumbprint = $env:SIGN_CERT_THUMBPRINT,
    [string]$TimestampServer = "http://timestamp.digicert.com"
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

. (Join-Path $PSScriptRoot "Signing.ps1")

$versionMatch = Select-String -Path 'src\data_refinery.py' -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionMatch) {
    throw 'Could not read __version__ from data_refinery.py.'
}
$appVersion = $versionMatch.Matches[0].Groups[1].Value
$bundleName = "App04_DataRefinery_v$appVersion"
$appExeName = "$bundleName.exe"

if (-not $SkipTests) {
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw 'Tests failed. Aborting build.'
    }
}

python -m PyInstaller --clean --noconfirm --workpath release\build --distpath release\dist release\packaging\data_refinery.spec
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller build failed.'
}

$mainExePath = Join-Path $projectRoot "release\dist\$bundleName\$appExeName"
if (-not $SkipSign -and (Test-Path $mainExePath)) {
    Invoke-SignBinary -FilePath $mainExePath -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer
}

$isccCandidates = @(
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
    (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
    'C:\Program Files\Inno Setup 7\ISCC.exe',
    'C:\Program Files (x86)\Inno Setup 7\ISCC.exe',
    'C:\Program Files\Inno Setup 6\ISCC.exe',
    'C:\Program Files (x86)\Inno Setup 6\ISCC.exe'
)
$iscc = $null
foreach ($cand in $isccCandidates) {
    if (Test-Path $cand) {
        $iscc = $cand
        break
    }
}
if (-not $iscc) {
    throw 'Inno Setup 6 or 7 is required. Please install Inno Setup and run again.'
}

& $iscc "/DAppVersion=$appVersion" "/DAppBundleName=$bundleName" "/DAppExeName=$appExeName" 'release\installer\DataRefinery.iss'
if ($LASTEXITCODE -ne 0) {
    throw 'Inno Setup build failed.'
}

$installerPath = Join-Path $projectRoot "release\dist\installer\App04_DataRefinery_Setup_v$appVersion.exe"
if (-not (Test-Path $installerPath)) {
    throw "Installer was not created: $installerPath"
}

if (-not $SkipSign) {
    Invoke-SignBinary -FilePath $installerPath -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer
}

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
Write-Host " Installer created and signed successfully: $installerPath" -ForegroundColor Green
Write-Host "=================================================================" -ForegroundColor Green
