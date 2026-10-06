param(
    [switch]$SkipTests,
    [switch]$SkipSign,
    [string]$CertificateThumbprint = $env:SIGN_CERT_THUMBPRINT,
    [string]$TimestampServer = "http://timestamp.digicert.com"
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot
. (Join-Path $PSScriptRoot 'Signing.ps1')

if (-not $SkipTests) {
    python -m unittest discover -s tests -v
    if ($LASTEXITCODE -ne 0) {
        throw 'Tests failed. Aborting launcher build.'
    }
}

$launcherName = 'App04_DataRefinery_Launcher'
python -m PyInstaller --clean --noconfirm --workpath release\build\launcher --distpath release\dist release\packaging\data_refinery_launcher.spec
if ($LASTEXITCODE -ne 0) {
    throw 'Launcher build failed.'
}

$launcherPath = Join-Path $projectRoot "release\dist\$launcherName.exe"
if (-not (Test-Path $launcherPath)) {
    throw "Launcher was not created: $launcherPath"
}
if (-not $SkipSign) {
    Invoke-SignBinary -FilePath $launcherPath -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer
}
$launcherHash = (Get-FileHash -LiteralPath $launcherPath -Algorithm SHA256).Hash.ToLowerInvariant()
Set-Content -LiteralPath "$launcherPath.sha256" -Value "$launcherHash *$(Split-Path -Leaf $launcherPath)" -Encoding ascii
Write-Host "Launcher created: $launcherPath"
