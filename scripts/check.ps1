[CmdletBinding()]
param([string]$Python = 'python')

$ErrorActionPreference = 'Stop'
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $PSScriptRoot
$checkRoot = Join-Path $projectRoot ('build\checks-' + [guid]::NewGuid().ToString('N'))
$savedEnvironment = @{}
foreach ($name in @('LOCALAPPDATA', 'APPDATA', 'TEMP', 'TMP', 'PSModulePath')) {
    $savedEnvironment[$name] = [Environment]::GetEnvironmentVariable($name, 'Process')
}
Push-Location -LiteralPath $projectRoot
try {
    # Never run migration/settings tests against the developer's installed app.
    foreach ($pair in @(@('LOCALAPPDATA', 'Local'), @('APPDATA', 'Roaming'), @('TEMP', 'Temp'), @('TMP', 'Temp'))) {
        $path = Join-Path $checkRoot $pair[1]
        New-Item -ItemType Directory -Path $path -Force | Out-Null
        [Environment]::SetEnvironmentVariable($pair[0], $path, 'Process')
    }
    & $Python -c 'import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) and sys.maxsize > 2**32 else 1)'
    if ($LASTEXITCODE -ne 0) { throw 'Checks require Python 3.13 x64.' }
    & $Python -m pip check
    if ($LASTEXITCODE -ne 0) { throw 'Dependency consistency check failed.' }
    & $Python -m ruff check --select E4,E7,E9,F src tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'Python lint failed.' }
    & $Python -m unittest discover -s tests -q
    if ($LASTEXITCODE -ne 0) { throw 'Application tests failed.' }
    # Separate processes contain deliberate native failure codes in fault tests.
    # Let Windows PowerShell rebuild its own module path instead of inheriting PS7 modules.
    [Environment]::SetEnvironmentVariable('PSModulePath', $null, 'Process')
    $windowsPowerShell = Join-Path $env:WINDIR 'System32\WindowsPowerShell\v1.0\powershell.exe'
    & $windowsPowerShell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot 'tests\test_release_signing.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'Release pipeline tests failed.' }
    & $windowsPowerShell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $projectRoot 'scripts\test_user_data_backup.ps1')
    if ($LASTEXITCODE -ne 0) { throw 'User data backup tests failed.' }
    Write-Host 'Quality checks passed: dependencies, lint, application, release and backup safety.' -ForegroundColor Green
    Write-Host "Isolated test fixtures: $checkRoot"
} finally {
    foreach ($name in $savedEnvironment.Keys) {
        [Environment]::SetEnvironmentVariable($name, $savedEnvironment[$name], 'Process')
    }
    Pop-Location
}
# Do not propagate intentional failure codes left by successful fault-injection tests.
exit 0
