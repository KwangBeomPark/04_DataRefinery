[CmdletBinding()]
param(
    [string]$Version,
    [string]$CertificateThumbprint = 'E9C72CF5090840A1805296525D56BE680622A7FD',
    [string]$TimestampServer = 'http://timestamp.digicert.com',
    [string]$SignToolPath = $env:SIGNTOOL_PATH
)

$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location -LiteralPath $projectRoot
$versionMatch = Select-String -LiteralPath 'src\version.py' -Pattern '^__version__\s*=\s*"([^"]+)"'
$sourceVersion = $versionMatch.Matches[0].Groups[1].Value
if (-not $Version) { $Version = $sourceVersion }
if ($Version -ne $sourceVersion -or $Version -notmatch '^\d+\.\d+\.\d+$') { throw 'Requested version does not match source.' }
$resultPath = Join-Path $projectRoot "release\build\signtool_v$Version.result.json"
$logPath = Join-Path $projectRoot "release\build\signtool_v$Version.log"
$transcriptStarted = $false
try {
    Start-Transcript -LiteralPath $logPath -Force | Out-Null
    $transcriptStarted = $true
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Use the interactive Administrator PowerShell where SimplySign is logged in.'
    }
    $branch = git branch --show-current
    if ($LASTEXITCODE -ne 0 -or $branch -ne 'main') { throw 'Finalize a release only on main.' }
    $dirty = git status --porcelain
    if ($LASTEXITCODE -ne 0 -or $dirty) { throw 'Commit source changes before signing.' }
    $sourceCommit = git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Could not read source commit.' }
    $input = Get-Content -LiteralPath 'release\build\release-input.json' -Raw | ConvertFrom-Json
    if ($input.version -ne $Version -or $input.commit -ne $sourceCommit) { throw 'Build input does not match current source.' }
    if (-not $SignToolPath) {
        $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
        if ($null -ne $onPath) { $SignToolPath = $onPath.Source }
    }
    if (-not $SignToolPath -or -not (Test-Path -LiteralPath $SignToolPath)) { throw 'Provide SIGNTOOL_PATH or put Microsoft SignTool on PATH.' }
    $toolSignature = Get-AuthenticodeSignature -LiteralPath $SignToolPath
    if ($toolSignature.Status -ne 'Valid' -or $toolSignature.SignerCertificate.Subject -notmatch 'Microsoft') { throw 'Microsoft SignTool signature is invalid.' }
    $env:PATH = (Split-Path -Parent $SignToolPath) + ';' + $env:PATH
    . (Join-Path $PSScriptRoot 'Signing.ps1')
    $bundleName = "App04_DataRefinery_v$Version"
    $appPath = Join-Path $projectRoot "release\dist\$bundleName\$bundleName.exe"
    $launcherPath = Join-Path $projectRoot 'release\dist\App04_DataRefinery_Launcher.exe'
    foreach ($binary in @($appPath, $launcherPath)) {
        if (-not (Test-Path -LiteralPath $binary)) { throw "Missing build artifact: $binary" }
        $null = Invoke-SignBinary -FilePath $binary -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer
    }
    $isccCandidates = @(
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 7\ISCC.exe'),
        (Join-Path $env:LOCALAPPDATA 'Programs\Inno Setup 6\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 7\ISCC.exe'),
        (Join-Path ${env:ProgramFiles(x86)} 'Inno Setup 6\ISCC.exe')
    )
    $iscc = $isccCandidates | Where-Object { Test-Path -LiteralPath $_ } | Select-Object -First 1
    if (-not $iscc) { throw 'Inno Setup is unavailable.' }
    $archiveDirectory = Join-Path $projectRoot ("release\build\signed-uninstaller\$Version-" + [guid]::NewGuid().ToString('N'))
    $signArgs = Get-InnoReleaseSignArguments -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer -UninstallerArchiveDirectory $archiveDirectory
    & $iscc "/DAppVersion=$Version" "/DAppBundleName=$bundleName" "/DAppExeName=$bundleName.exe" @signArgs 'release\installer\DataRefinery.iss'
    if ($LASTEXITCODE -ne 0) { throw 'Signed installer compilation failed.' }
    $installerPath = Join-Path $projectRoot "release\dist\installer\App04_DataRefinery_Setup_v$Version.exe"
    $uninstallers = @(Get-ChildItem -LiteralPath $archiveDirectory -File -Filter 'uninstaller-*.exe')
    if (-not $uninstallers.Count) { throw 'The signing hook did not preserve the signed uninstaller.' }
    $records = @()
    foreach ($binary in (@($appPath, $launcherPath, $installerPath) + @($uninstallers.FullName))) {
        $signature = Get-AuthenticodeSignature -LiteralPath $binary
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $CertificateThumbprint -or -not $signature.TimeStamperCertificate) {
            throw "Release signature or timestamp is invalid: $binary"
        }
        & $SignToolPath verify /pa /all $binary
        if ($LASTEXITCODE -ne 0) { throw "SignTool verification failed: $binary" }
        $records += [pscustomobject]@{
            name = Split-Path -Leaf $binary
            size = (Get-Item -LiteralPath $binary).Length
            sha256 = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
            signer = $signature.SignerCertificate.Subject
            signerThumbprint = $signature.SignerCertificate.Thumbprint
            timestampSigner = $signature.TimeStamperCertificate.Subject
        }
    }
    $publicPaths = @($installerPath, $launcherPath)
    $sums = @()
    foreach ($binary in $publicPaths) {
        $hash = (Get-FileHash -LiteralPath $binary -Algorithm SHA256).Hash.ToLowerInvariant()
        $line = "$hash *$(Split-Path -Leaf $binary)"
        Set-Content -LiteralPath "$binary.sha256" -Value $line -Encoding ascii
        $sums += $line
    }
    $sumsPath = Join-Path $projectRoot 'release\dist\installer\SHA256SUMS.txt'
    $manifestPath = Join-Path $projectRoot 'release\dist\installer\build-manifest.json'
    [IO.File]::WriteAllText($sumsPath, (($sums -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
    $manifest = @{ version=$Version; commit=$sourceCommit; signedAtUtc=[DateTime]::UtcNow.ToString('o'); artifacts=$records }
    [IO.File]::WriteAllText($manifestPath, ($manifest | ConvertTo-Json -Depth 6), [Text.UTF8Encoding]::new($false))
    @{ Status='success'; Version=$Version; Commit=$sourceCommit; AppPath=$appPath; InstallerPath=$installerPath; LauncherPath=$launcherPath; ManifestPath=$manifestPath; ChecksumsPath=$sumsPath } |
        ConvertTo-Json | Set-Content -LiteralPath $resultPath -Encoding utf8
    Write-Host "Signed app, launcher, installer and uninstaller verified for v$Version." -ForegroundColor Green
} catch {
    @{ Status='failed'; Version=$Version; Error=$_.Exception.Message } | ConvertTo-Json |
        Set-Content -LiteralPath $resultPath -Encoding utf8
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
}
