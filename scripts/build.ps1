[CmdletBinding()]
param([switch]$SkipTests)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
. (Join-Path $PSScriptRoot 'sign.ps1') -FunctionsOnly
$buildDirectory = Assert-ProjectPath (Join-Path $projectRoot 'build')
$distDirectory = Assert-ProjectPath (Join-Path $projectRoot 'dist')
New-Item -ItemType Directory -Path $buildDirectory,$distDirectory -Force | Out-Null
$inputPath = Join-Path $buildDirectory 'release-input.json'
if (Test-Path -LiteralPath $inputPath) { Remove-Item -LiteralPath $inputPath }
$version = Get-ReleaseVersion
$bundleName = "App04_DataRefinery_v$version"
$commit = & git rev-parse HEAD
if ($LASTEXITCODE -ne 0) { throw 'Cannot identify build source.' }
$dirty = & git status --porcelain
if ($LASTEXITCODE -ne 0) { throw 'Cannot check build source.' }
$sourceClean = -not [bool]$dirty
$iscc = Get-InnoCompiler
python -c "import sys; sys.exit(0 if sys.version_info[:2] == (3, 13) else 1)"
if ($LASTEXITCODE -ne 0) { throw 'Python 3.13 is required by the pinned requirements.' }
$venv = Assert-ProjectPath (Join-Path $buildDirectory 'venv')
python -m venv --clear $venv
if ($LASTEXITCODE -ne 0) { throw 'Cannot create the isolated build environment.' }
$buildPython = Join-Path $venv 'Scripts\python.exe'
& $buildPython -m pip install --disable-pip-version-check --no-input -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Pinned dependency installation failed.' }
& $buildPython -m pip check
if ($LASTEXITCODE -ne 0) { throw 'Build dependencies are inconsistent.' }
if (-not $SkipTests) {
    $savedLocalAppData = $env:LOCALAPPDATA
    $savedRoamingAppData = $env:APPDATA
    try {
        $env:LOCALAPPDATA = Join-Path $buildDirectory 'test-appdata\Local'
        $env:APPDATA = Join-Path $buildDirectory 'test-appdata\Roaming'
        & $buildPython -m unittest discover -s tests -q
        if ($LASTEXITCODE -ne 0) { throw 'Application tests failed.' }
        & powershell -NoProfile -ExecutionPolicy Bypass -File 'tests\test_release_signing.ps1'
        if ($LASTEXITCODE -ne 0) { throw 'Release pipeline tests failed.' }
        & powershell -NoProfile -File 'scripts\test_user_data_backup.ps1'
        if ($LASTEXITCODE -ne 0) { throw 'User data backup tests failed.' }
    } finally {
        $env:LOCALAPPDATA = $savedLocalAppData
        $env:APPDATA = $savedRoamingAppData
    }
}
& $buildPython -m PyInstaller --clean --noconfirm --workpath (Join-Path $buildDirectory 'app') --distpath $distDirectory 'installer\data_refinery.spec'
if ($LASTEXITCODE -ne 0) { throw 'Application onedir build failed.' }
& $buildPython -m PyInstaller --clean --noconfirm --workpath (Join-Path $buildDirectory 'launcher') --distpath $distDirectory 'installer\data_refinery_launcher.spec'
if ($LASTEXITCODE -ne 0) { throw 'Launcher build failed.' }
$bundle = Join-Path $distDirectory $bundleName
$app = Join-Path $bundle "$bundleName.exe"
$launcher = Join-Path $distDirectory 'App04_DataRefinery_Launcher.exe'
foreach ($binary in @($app,$launcher)) { if (-not (Test-Path -LiteralPath $binary)) { throw "Missing build: $binary" } }
if (Test-Path -LiteralPath (Join-Path $bundle 'UserSetting')) { throw 'A user settings folder must never be bundled.' }
$preview = Assert-ProjectPath (Join-Path $distDirectory 'staging')
New-Item -ItemType Directory -Path $preview -Force | Out-Null
# A fresh staging directory avoids retaining obsolete unsigned alias files.
foreach ($file in (Get-ChildItem -LiteralPath $preview -File)) { Remove-Item -LiteralPath $file.FullName }
& $iscc "/DAppVersion=$version" "/DAppExeName=$bundleName.exe" "/DAppSourceDir=$bundle" "/DArtifactDir=$preview" 'installer\setup.iss'
if ($LASTEXITCODE -ne 0) { throw 'Unsigned preview installer compilation failed.' }
$builtAt = [DateTime]::UtcNow.ToString('o')
Write-ReleaseMetadata -Directory $preview -Version $version -Commit $commit -BuiltAtUtc $builtAt -Thumbprint $CertificateThumbprint -RequireSignature $false
$bundleRecords = @()
foreach ($file in (Get-ChildItem -LiteralPath $bundle -Recurse -File | Sort-Object FullName)) {
    $bundleRecords += [pscustomobject]@{
        relativePath = $file.FullName.Substring($bundle.Length+1)
        sha256 = (Get-FileHash -LiteralPath $file.FullName).Hash.ToLowerInvariant()
    }
}
$endCommit = & git rev-parse HEAD
$endDirty = & git status --porcelain
if ($commit -ne $endCommit -or [string]($dirty -join "`n") -ne [string]($endDirty -join "`n")) { throw 'Source status changed during the build. Rebuild before signing.' }
$inputRecord = [ordered]@{
    version=$version; commit=$commit; sourceClean=$sourceClean; testsPassed=(-not [bool]$SkipTests); builtAtUtc=$builtAt;
    bundleFiles=$bundleRecords; launcherSha256=(Get-FileHash -LiteralPath $launcher).Hash.ToLowerInvariant()
}
[IO.File]::WriteAllText((Join-Path $buildDirectory 'release-input.json'), ($inputRecord | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
Write-Host 'Unsigned preview is in dist/staging. The existing official release was preserved.' -ForegroundColor Green
Write-Host 'Next: run scripts/sign.ps1 in the interactive Administrator PowerShell with SimplySign logged in.'
