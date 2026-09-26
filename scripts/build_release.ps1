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

# PyInstaller discovers optional libraries from its Python environment. A
# fresh environment keeps unrelated developer packages out of the installer.
python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)"
if ($LASTEXITCODE -ne 0) {
    throw 'Python 3.13 is required for this pinned release environment.'
}
$buildPython = Join-Path $projectRoot 'release\build\release-venv\Scripts\python.exe'
python -m venv --clear (Join-Path $projectRoot 'release\build\release-venv')
if ($LASTEXITCODE -ne 0) {
    throw 'Could not create the isolated release environment.'
}
& $buildPython -m pip install --disable-pip-version-check --no-input -r requirements.txt
if ($LASTEXITCODE -ne 0) {
    throw 'Could not install pinned release dependencies.'
}
& $buildPython -m pip check
if ($LASTEXITCODE -ne 0) {
    throw 'Pinned release dependencies are inconsistent.'
}

$versionMatch = Select-String -Path 'src\data_refinery.py' -Pattern '^__version__\s*=\s*"([^"]+)"' | Select-Object -First 1
if (-not $versionMatch) {
    throw 'Could not read __version__ from data_refinery.py.'
}
$appVersion = $versionMatch.Matches[0].Groups[1].Value
$bundleName = "App04_DataRefinery_v$appVersion"
$appExeName = "$bundleName.exe"

if (-not $SkipTests) {
    & $buildPython -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw 'Tests failed. Aborting build.'
    }
}

& $buildPython -m PyInstaller --clean --noconfirm --workpath release\build --distpath release\dist release\packaging\data_refinery.spec
if ($LASTEXITCODE -ne 0) {
    throw 'PyInstaller build failed.'
}

$mainExePath = Join-Path $projectRoot "release\dist\$bundleName\$appExeName"
if (-not $SkipSign -and (Test-Path $mainExePath)) {
    Invoke-SignBinary -FilePath $mainExePath -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer
}
if (-not (Test-Path $mainExePath)) {
    throw "Application executable was not created: $mainExePath"
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
$installerHash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$checksumPath = "$installerPath.sha256"
Set-Content -LiteralPath $checksumPath -Value "$installerHash *$(Split-Path -Leaf $installerPath)" -Encoding ascii

Write-Host ""
Write-Host "=================================================================" -ForegroundColor Green
if ($SkipSign) {
    Write-Host " Unsigned test installer created: $installerPath" -ForegroundColor Yellow
} else {
    Write-Host " Signed installer created: $installerPath" -ForegroundColor Green
}
Write-Host " SHA-256 checksum: $checksumPath"
Write-Host "=================================================================" -ForegroundColor Green
