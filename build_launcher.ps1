param(
    [switch]$SkipTests
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

if (-not $SkipTests) {
    python -m unittest discover -s tests -v
}

$launcherName = 'App04_DataRefinery_Luncher'
python -m PyInstaller --clean --noconfirm --onefile --windowed --name $launcherName --icon icon.ico --workpath build\launcher --distpath dist data_refinery_launcher.py
if ($LASTEXITCODE -ne 0) {
    throw 'Launcher build failed.'
}

$launcherPath = Join-Path $projectRoot "dist\$launcherName.exe"
if (-not (Test-Path $launcherPath)) {
    throw "Launcher was not created: $launcherPath"
}
Write-Host "Launcher created: $launcherPath"
