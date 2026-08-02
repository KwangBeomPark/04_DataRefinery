param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not $SkipTests) {
    python -m unittest discover -s tests -v
}

$launcherName = 'App04_DataRefinery_Luncher'
python -m PyInstaller --clean --noconfirm --workpath release\build\launcher --distpath release\dist release\packaging\data_refinery_launcher.spec
if ($LASTEXITCODE -ne 0) {
    throw 'Launcher build failed.'
}

$launcherPath = Join-Path $projectRoot "release\dist\$launcherName.exe"
if (-not (Test-Path $launcherPath)) {
    throw "Launcher was not created: $launcherPath"
}
Write-Host "Launcher created: $launcherPath"
