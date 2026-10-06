[CmdletBinding()]
param(
    [switch]$Publish,
    [switch]$VerifyOnly,
    [switch]$FunctionsOnly,
    [string]$CertificateThumbprint = 'E9C72CF5090840A1805296525D56BE680622A7FD',
    [string]$TimestampServer = 'http://timestamp.digicert.com',
    [string]$SignToolPath = $env:SIGNTOOL_PATH,
    # Internal Inno callback. Users run this script without these arguments.
    [string]$InnoFilePath,
    [string]$UninstallerArchiveDirectory
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$script:ReleaseProjectRoot = Split-Path -Parent $PSScriptRoot

function Assert-ProjectPath {
    param([string]$Path)
    $absolute = [IO.Path]::GetFullPath($Path)
    if (-not $absolute.StartsWith($script:ReleaseProjectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Path escapes the project: $absolute"
    }
    $ancestor = $absolute
    while ($ancestor -ne $script:ReleaseProjectRoot) {
        if (Test-Path -LiteralPath $ancestor) {
            if ((Get-Item -LiteralPath $ancestor -Force).Attributes -band [IO.FileAttributes]::ReparsePoint) {
                throw "Linked paths are not allowed: $ancestor"
            }
        }
        $ancestor = Split-Path -Parent $ancestor
    }
    return $absolute
}

function Get-ReleaseVersion {
    $match = Select-String -LiteralPath (Join-Path $script:ReleaseProjectRoot 'src\version.py') -Pattern '^__version__\s*=\s*"(\d+\.\d+\.\d+)"'
    if (-not $match) { throw 'Cannot read the single source version: src/version.py.' }
    return $match.Matches[0].Groups[1].Value
}

function Get-InnoCompiler {
    $candidates = @()
    $onPath = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($null -ne $onPath) { $candidates += $onPath.Source }
    foreach ($base in @((Join-Path $env:LOCALAPPDATA 'Programs'), $env:ProgramFiles, ${env:ProgramFiles(x86)})) {
        foreach ($major in @(7, 6)) { $candidates += Join-Path $base "Inno Setup $major\ISCC.exe" }
    }
    foreach ($candidate in $candidates) { if (Test-Path -LiteralPath $candidate -PathType Leaf) { return $candidate } }
    throw 'Install Inno Setup 6.7+ or 7 before building.'
}

function Find-ReleaseSignTool {
    param([string]$RequestedPath)
    $candidates = @()
    if ($RequestedPath) { $candidates += $RequestedPath }
    $candidates += Join-Path $script:ReleaseProjectRoot 'tools\signtool\signtool.exe'
    $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($null -ne $onPath) { $candidates += $onPath.Source }
    $sdk = Join-Path ${env:ProgramFiles(x86)} 'Windows Kits\10\bin'
    if (Test-Path -LiteralPath $sdk) {
        foreach ($directory in (Get-ChildItem -LiteralPath $sdk -Directory | Sort-Object Name -Descending)) {
            $candidates += Join-Path $directory.FullName 'x64\signtool.exe'
        }
    }
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        $signature = Get-AuthenticodeSignature -LiteralPath $candidate
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Microsoft') {
            throw "SignTool must have a valid Microsoft signature: $candidate"
        }
        return (Resolve-Path -LiteralPath $candidate).Path
    }
    throw 'Provide SIGNTOOL_PATH, Microsoft SignTool on PATH, or tools/signtool/signtool.exe.'
}

function Get-SignedArtifactRecord {
    param([string]$FilePath, [string]$Thumbprint, [bool]$RequireSignature = $true)
    $signature = Get-AuthenticodeSignature -LiteralPath $FilePath
    if ($RequireSignature -and ($signature.Status -ne 'Valid' -or
        $signature.SignerCertificate.Thumbprint -ne $Thumbprint -or -not $signature.TimeStamperCertificate)) {
        throw "Invalid publisher signature or timestamp: $FilePath"
    }
    return [pscustomobject]@{
        name = Split-Path -Leaf $FilePath
        size = (Get-Item -LiteralPath $FilePath).Length
        sha256 = (Get-FileHash -LiteralPath $FilePath -Algorithm SHA256).Hash.ToLowerInvariant()
        signatureStatus = [string]$signature.Status
        signer = $(if ($signature.SignerCertificate) { $signature.SignerCertificate.Subject } else { $null })
        signerThumbprint = $(if ($signature.SignerCertificate) { $signature.SignerCertificate.Thumbprint } else { $null })
        timestampSigner = $(if ($signature.TimeStamperCertificate) { $signature.TimeStamperCertificate.Subject } else { $null })
    }
}

function Assert-ReleaseSignature {
    param([string]$FilePath, [string]$Thumbprint, [string]$ToolPath)
    $null = Get-SignedArtifactRecord -FilePath $FilePath -Thumbprint $Thumbprint
    & $ToolPath verify /pa /all $FilePath | Out-Host
    if ($LASTEXITCODE -ne 0) { throw "SignTool verification failed: $FilePath" }
}

function Invoke-SignBinary {
    param([string]$FilePath, [string]$CertificateThumbprint, [string]$TimestampServer, [string]$SignToolPath)
    if ($CertificateThumbprint -notmatch '^[0-9a-fA-F]{40}$') { throw 'Invalid certificate thumbprint.' }
    if ($TimestampServer -notmatch '^https?://[^\s"$]+$') { throw 'Invalid timestamp URL.' }
    foreach ($server in @($TimestampServer, 'http://time.certum.pl') | Select-Object -Unique) {
        & $SignToolPath sign /sha1 $CertificateThumbprint /fd sha256 /tr $server /td sha256 $FilePath | Out-Host
        if ($LASTEXITCODE -eq 0) {
            Assert-ReleaseSignature -FilePath $FilePath -Thumbprint $CertificateThumbprint -ToolPath $SignToolPath
            return
        }
        Write-Warning "Signing failed using timestamp server $server."
    }
    throw "Signing failed: $FilePath"
}

function Invoke-InnoSignAndArchive {
    param([string]$FilePath, [string]$SignToolPath, [string]$CertificateThumbprint,
        [string]$TimestampServer, [string]$UninstallerArchiveDirectory)
    Invoke-SignBinary -FilePath $FilePath -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer -SignToolPath $SignToolPath
    # Keep the exact signed input before Inno embeds its signature and deletes it.
    if ((Split-Path -Leaf $FilePath) -match '^uninst.*\.tmp$') {
        $hash = (Get-FileHash -LiteralPath $FilePath -Algorithm SHA256).Hash.ToLowerInvariant()
        New-Item -ItemType Directory -Force -Path $UninstallerArchiveDirectory | Out-Null
        $archivePath = Join-Path $UninstallerArchiveDirectory "uninstaller-$hash.exe"
        Copy-Item -LiteralPath $FilePath -Destination $archivePath -Force
        if ((Get-FileHash -LiteralPath $archivePath -Algorithm SHA256).Hash.ToLowerInvariant() -ne $hash) {
            throw 'Signed uninstaller archive changed during copying.'
        }
    }
}

function Get-InnoReleaseSignArguments {
    param([string]$CertificateThumbprint, [string]$TimestampServer, [string]$SignToolPath,
        [string]$UninstallerArchiveDirectory)
    if ($CertificateThumbprint -notmatch '^[0-9a-fA-F]{40}$') { throw 'Invalid certificate thumbprint.' }
    if ($TimestampServer -notmatch '^https?://[^\s"$]+$') { throw 'Invalid timestamp URL.' }
    $powershell = (Get-Command powershell.exe -ErrorAction Stop).Source
    $command = '$q' + $powershell + '$q -NoProfile -ExecutionPolicy Bypass -File $q' +
        (Join-Path $PSScriptRoot 'sign.ps1') + '$q -InnoFilePath $f -SignToolPath $q' +
        $SignToolPath + '$q -CertificateThumbprint ' + $CertificateThumbprint +
        ' -TimestampServer ' + $TimestampServer + ' -UninstallerArchiveDirectory $q' +
        $UninstallerArchiveDirectory + '$q'
    return @('/DReleaseSign', ('/SDataRefineryReleaseSign=' + $command))
}

function New-ReleaseAlias {
    param([string]$Source, [string]$Destination)
    if (Test-Path -LiteralPath $Destination) {
        if ((Get-FileHash -LiteralPath $Source).Hash -ne (Get-FileHash -LiteralPath $Destination).Hash) {
            throw "Refusing to overwrite a different alias: $Destination"
        }
        return
    }
    try { $null = New-Item -ItemType HardLink -Path $Destination -Value $Source -ErrorAction Stop }
    catch {
        Write-Warning 'Hard links are unavailable; the public alias will use a complete copy.'
        Copy-Item -LiteralPath $Source -Destination $Destination
    }
    if ((Get-FileHash -LiteralPath $Source).Hash -ne (Get-FileHash -LiteralPath $Destination).Hash) { throw 'Dual naming content mismatch.' }
}

function Write-ReleaseMetadata {
    param([string]$Directory, [string]$Version, [string]$Commit, [string]$BuiltAtUtc,
        [string]$Thumbprint, [object[]]$Components = @(), [bool]$RequireSignature = $true,
        [string]$SignedAtUtc = '')
    $binaryNames = @("App04_DataRefinery_Setup_v$Version.exe", "DataRefinery-Setup.v$Version.exe", 'App04_DataRefinery_Launcher.exe')
    $records = @()
    foreach ($name in $binaryNames) {
        $file = Join-Path $Directory $name
        $records += Get-SignedArtifactRecord -FilePath $file -Thumbprint $Thumbprint -RequireSignature $RequireSignature
        $hash = (Get-FileHash -LiteralPath $file).Hash.ToLowerInvariant()
        [IO.File]::WriteAllText("$file.sha256", "$hash *$name`n", [Text.UTF8Encoding]::new($false))
    }
    $note = "RELEASE_NOTES_v$Version.md"
    Copy-Item -LiteralPath (Join-Path $script:ReleaseProjectRoot "docs\release-notes\$note") -Destination (Join-Path $Directory $note) -Force
    $manifest = [ordered]@{
        schemaVersion=2; productId='App04_DataRefinery'; version=$Version; commit=$Commit;
        builtAtUtc=$BuiltAtUtc; signedAtUtc=$SignedAtUtc;
        status=$(if ($RequireSignature) { 'signed' } else { 'unsigned-preview' });
        artifacts=$records; components=@($Components);
        aliases=@{ "DataRefinery-Setup.v$Version.exe"="App04_DataRefinery_Setup_v$Version.exe" }
    }
    [IO.File]::WriteAllText((Join-Path $Directory 'build-manifest.json'), ($manifest | ConvertTo-Json -Depth 8), [Text.UTF8Encoding]::new($false))
    $sums = foreach ($file in (Get-ChildItem -LiteralPath $Directory -File | Where-Object { $_.Name -ne 'SHA256SUMS.txt' } | Sort-Object Name)) {
        (Get-FileHash -LiteralPath $file.FullName).Hash.ToLowerInvariant() + ' *' + $file.Name
    }
    [IO.File]::WriteAllText((Join-Path $Directory 'SHA256SUMS.txt'), (($sums -join "`n") + "`n"), [Text.UTF8Encoding]::new($false))
}

function Assert-ReleaseDirectory {
    param([string]$Directory, [string]$Thumbprint, [string]$ToolPath)
    $manifest = Get-Content -LiteralPath (Join-Path $Directory 'build-manifest.json') -Raw | ConvertFrom-Json
    if ($manifest.status -ne 'signed' -or $manifest.productId -ne 'App04_DataRefinery') { throw 'Only signed official artifacts can be released.' }
    if ($manifest.version -notmatch '^\d+\.\d+\.\d+$' -or $manifest.commit -notmatch '^[0-9a-f]{40}$') { throw 'Invalid release identity.' }
    $binaryNames = @("App04_DataRefinery_Setup_v$($manifest.version).exe", "DataRefinery-Setup.v$($manifest.version).exe", 'App04_DataRefinery_Launcher.exe')
    if (@($manifest.artifacts).Count -ne 3 -or @(Compare-Object ($binaryNames | Sort-Object) (@($manifest.artifacts.name) | Sort-Object)).Count) {
        throw 'The manifest must cover both installer names and the launcher exactly once.'
    }
    $allowedFiles = @($binaryNames) + @($binaryNames | ForEach-Object { $_ + '.sha256' }) + @("RELEASE_NOTES_v$($manifest.version).md", 'build-manifest.json', 'SHA256SUMS.txt')
    $presentFiles = @(Get-ChildItem -LiteralPath $Directory -File).Name
    if (@(Compare-Object ($allowedFiles | Sort-Object) ($presentFiles | Sort-Object)).Count) { throw 'Official release contains missing or unexpected files.' }
    foreach ($record in $manifest.artifacts) {
        if ($record.name -ne (Split-Path -Leaf $record.name)) { throw 'Invalid manifest artifact name.' }
        $path = Join-Path $Directory $record.name
        Assert-ReleaseSignature -FilePath $path -Thumbprint $Thumbprint -ToolPath $ToolPath
        if ((Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant() -ne $record.sha256 -or (Get-Item -LiteralPath $path).Length -ne $record.size -or $record.signerThumbprint -ne $Thumbprint) {
            throw "Manifest content mismatch: $path"
        }
    }
    $expectedFiles = @(Get-ChildItem -LiteralPath $Directory -File | Where-Object { $_.Name -ne 'SHA256SUMS.txt' }).Name
    $checkedFiles = @()
    foreach ($line in (Get-Content -LiteralPath (Join-Path $Directory 'SHA256SUMS.txt'))) {
        if ($line -notmatch '^([0-9a-f]{64}) \*([^\\/]+)$') { throw 'Invalid checksum line.' }
        $hash = $Matches[1]; $name = $Matches[2]
        if ($name -notin $expectedFiles -or $name -in $checkedFiles) { throw 'Unexpected or duplicate checksum file.' }
        if ((Get-FileHash -LiteralPath (Join-Path $Directory $name)).Hash.ToLowerInvariant() -ne $hash) { throw "Checksum mismatch: $name" }
        $checkedFiles += $name
    }
    if ($checkedFiles.Count -ne $expectedFiles.Count) { throw 'Checksum coverage is incomplete.' }
    return $manifest
}

function Publish-VerifiedRelease {
    param([string]$Directory, [object]$Manifest)
    $tag = 'v' + $Manifest.version
    $existing = & gh release view $tag --json tagName 2>$null
    if ($LASTEXITCODE -eq 0) { throw 'This release already exists. Bump the version; published files are never overwritten.' }
    & git push origin main
    if ($LASTEXITCODE -ne 0) { throw 'Source push failed.' }
    $runs = @()
    for ($attempt = 0; $attempt -lt 12; $attempt++) {
        $runJson = & gh run list --commit $Manifest.commit --workflow windows-release-check.yml --limit 1 --json databaseId,status,conclusion
        if ($LASTEXITCODE -ne 0) { throw 'Cannot query the Windows release check.' }
        $runs = @($runJson | ConvertFrom-Json)
        if ($runs.Count) { break }
        Start-Sleep -Seconds 5
    }
    if (-not $runs.Count) { throw 'No Windows release check was found for this source commit.' }
    if ($runs[0].status -ne 'completed') {
        & gh run watch $runs[0].databaseId --exit-status
        if ($LASTEXITCODE -ne 0) { throw 'Windows release check failed.' }
    } elseif ($runs[0].conclusion -ne 'success') { throw 'Windows release check did not pass.' }
    & git tag -a $tag $Manifest.commit -m "Data Refinery $tag"
    if ($LASTEXITCODE -ne 0) { throw 'Release tag creation failed.' }
    & git push origin $tag
    if ($LASTEXITCODE -ne 0) { throw 'Release tag push failed.' }
    $assets = @(Get-ChildItem -LiteralPath $Directory -File).FullName
    & gh release create $tag --verify-tag --latest --title "Data Refinery $tag" --notes-file (Join-Path $Directory "RELEASE_NOTES_$tag.md") @assets
    if ($LASTEXITCODE -ne 0) { throw 'Release upload failed.' }
    $repoJson = & gh repo view --json nameWithOwner
    if ($LASTEXITCODE -ne 0) { throw 'Cannot identify the uploaded repository.' }
    $repository = ($repoJson | ConvertFrom-Json).nameWithOwner
    $uploadedJson = & gh api "repos/$repository/releases/tags/$tag"
    if ($LASTEXITCODE -ne 0) { throw 'Cannot verify uploaded release files.' }
    $uploaded = $uploadedJson | ConvertFrom-Json
    foreach ($asset in $assets) {
        $remote = @($uploaded.assets | Where-Object { $_.name -eq (Split-Path -Leaf $asset) })
        if ($remote.Count -ne 1 -or $remote[0].state -ne 'uploaded' -or
            $remote[0].digest -ne ('sha256:' + (Get-FileHash -LiteralPath $asset).Hash.ToLowerInvariant())) {
            throw "Uploaded file verification failed: $asset"
        }
    }
}

if ($FunctionsOnly) { return }
Set-Location -LiteralPath $script:ReleaseProjectRoot
$sessionDirectory = $null
$transcriptStarted = $false
try {
    if ($VerifyOnly) {
        if ($Publish -or $InnoFilePath) { throw 'VerifyOnly cannot sign or publish.' }
        $tool = Find-ReleaseSignTool -RequestedPath $SignToolPath
        $null = Assert-ReleaseDirectory -Directory (Join-Path $script:ReleaseProjectRoot 'release') -Thumbprint $CertificateThumbprint -ToolPath $tool
        Write-Host 'Official release signatures, timestamps, manifest and all checksums verified.'
        exit 0
    }
    $principal = [Security.Principal.WindowsPrincipal]::new([Security.Principal.WindowsIdentity]::GetCurrent())
    if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        throw 'Run in the user-opened Administrator PowerShell where SimplySign is logged in.'
    }
    $tool = Find-ReleaseSignTool -RequestedPath $SignToolPath
    if ($InnoFilePath) {
        $null = Assert-ProjectPath $InnoFilePath
        $null = Assert-ProjectPath $UninstallerArchiveDirectory
        Invoke-InnoSignAndArchive -FilePath $InnoFilePath -SignToolPath $tool -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer -UninstallerArchiveDirectory $UninstallerArchiveDirectory
        exit 0
    }
    $version = Get-ReleaseVersion
    $head = & git rev-parse HEAD
    if ($LASTEXITCODE -ne 0) { throw 'Cannot identify the source commit.' }
    $branch = & git branch --show-current
    $dirty = & git status --porcelain
    if ($LASTEXITCODE -ne 0 -or $branch -ne 'main' -or $dirty) { throw 'Sign only a clean, committed main branch.' }
    $inputRecord = Get-Content -LiteralPath 'build\release-input.json' -Raw | ConvertFrom-Json
    if ($inputRecord.version -ne $version -or $inputRecord.commit -ne $head -or -not $inputRecord.sourceClean) { throw 'Build provenance does not match this clean source commit.' }
    $existingTag = & git rev-parse --verify "refs/tags/v$version^{commit}" 2>$null
    if ($LASTEXITCODE -eq 0 -and $existingTag -ne $head) { throw 'This version already identifies a different released commit. Bump src/version.py before signing.' }
    $bundleName = "App04_DataRefinery_v$version"
    $sourceBundle = Assert-ProjectPath (Join-Path $script:ReleaseProjectRoot "dist\$bundleName")
    $sourceFiles = @(Get-ChildItem -LiteralPath $sourceBundle -Recurse -File)
    if ($sourceFiles.Count -ne $inputRecord.bundleFiles.Count) { throw 'Bundle file list changed after building.' }
    foreach ($record in $inputRecord.bundleFiles) {
        $path = Assert-ProjectPath (Join-Path $sourceBundle $record.relativePath)
        if (-not $path.StartsWith($sourceBundle+'\', [StringComparison]::OrdinalIgnoreCase) -or
            (Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant() -ne $record.sha256) { throw 'Bundle changed after building.' }
    }
    $sourceLauncher = Join-Path $script:ReleaseProjectRoot 'dist\App04_DataRefinery_Launcher.exe'
    if ((Get-FileHash -LiteralPath $sourceLauncher).Hash.ToLowerInvariant() -ne $inputRecord.launcherSha256) { throw 'Launcher changed after building.' }
    Start-Service -Name SCardSvr -ErrorAction Stop
    if ((Get-Service SCardSvr).Status -ne 'Running') { throw 'Smart Card service did not start.' }
    foreach ($service in @('CertPropSvc', 'ScDeviceEnum')) {
        try { Start-Service -Name $service -ErrorAction Stop } catch { Write-Warning "Optional service unavailable: $service" }
    }
    $certificate = Get-Item -LiteralPath "Cert:\CurrentUser\My\$CertificateThumbprint" -ErrorAction SilentlyContinue
    if (-not $certificate -or $certificate.NotAfter -le (Get-Date)) { throw 'A current code signing certificate is required in CurrentUser/My.' }
    $sessionId = "$version-" + [guid]::NewGuid().ToString('N')
    $sessionDirectory = Assert-ProjectPath (Join-Path $script:ReleaseProjectRoot "tools\release-history\$sessionId")
    $work = Assert-ProjectPath (Join-Path $script:ReleaseProjectRoot "build\signing\$sessionId")
    $stage = Assert-ProjectPath (Join-Path $script:ReleaseProjectRoot "dist\signed-$sessionId")
    New-Item -ItemType Directory -Path $sessionDirectory,$work,$stage -Force | Out-Null
    Start-Transcript -LiteralPath (Join-Path $sessionDirectory 'signing.log') | Out-Null
    $transcriptStarted = $true
    # Immutable unsigned inputs make retries safe after a partial signing failure.
    Copy-Item -LiteralPath $sourceBundle -Destination $work -Recurse
    Copy-Item -LiteralPath $sourceLauncher -Destination $work
    $signedBundle = Join-Path $work $bundleName
    $app = Join-Path $signedBundle "$bundleName.exe"
    $launcher = Join-Path $work 'App04_DataRefinery_Launcher.exe'
    foreach ($binary in @($app,$launcher)) { Invoke-SignBinary -FilePath $binary -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer -SignToolPath $tool }
    Copy-Item -LiteralPath $app -Destination $sessionDirectory
    Copy-Item -LiteralPath $launcher -Destination $stage
    $archive = Join-Path $sessionDirectory 'uninstaller'
    $temporaryUninstaller = Join-Path $work 'inno-temporary'
    New-Item -ItemType Directory -Path $temporaryUninstaller | Out-Null
    $signArgs = Get-InnoReleaseSignArguments -CertificateThumbprint $CertificateThumbprint -TimestampServer $TimestampServer -SignToolPath $tool -UninstallerArchiveDirectory $archive
    $iscc = Get-InnoCompiler
    & $iscc "/DAppVersion=$version" "/DAppExeName=$bundleName.exe" "/DAppSourceDir=$signedBundle" "/DArtifactDir=$stage" "/DUninstallerTempDir=$temporaryUninstaller" @signArgs 'installer\setup.iss' | Out-Host
    if ($LASTEXITCODE -ne 0) { throw 'Signed installer compilation failed.' }
    $uninstallers = @(Get-ChildItem -LiteralPath $archive -File -Filter 'uninstaller-*.exe')
    if (-not $uninstallers.Count) { throw 'No signed uninstaller was preserved by the signing callback.' }
    $components = @()
    foreach ($binary in (@($app) + @($uninstallers.FullName))) {
        Assert-ReleaseSignature -FilePath $binary -Thumbprint $CertificateThumbprint -ToolPath $tool
        $components += Get-SignedArtifactRecord -FilePath $binary -Thumbprint $CertificateThumbprint
    }
    New-ReleaseAlias -Source (Join-Path $stage "App04_DataRefinery_Setup_v$version.exe") -Destination (Join-Path $stage "DataRefinery-Setup.v$version.exe")
    Write-ReleaseMetadata -Directory $stage -Version $version -Commit $head -BuiltAtUtc $inputRecord.builtAtUtc -Thumbprint $CertificateThumbprint -Components $components -SignedAtUtc ([DateTime]::UtcNow.ToString('o'))
    $verified = Assert-ReleaseDirectory -Directory $stage -Thumbprint $CertificateThumbprint -ToolPath $tool
    $official = Assert-ProjectPath (Join-Path $script:ReleaseProjectRoot 'release')
    $backup = Join-Path $sessionDirectory 'previous-release'
    if (Test-Path -LiteralPath $official) { Move-Item -LiteralPath $official -Destination $backup }
    try { Move-Item -LiteralPath $stage -Destination $official }
    catch {
        if (Test-Path -LiteralPath $backup) { Move-Item -LiteralPath $backup -Destination $official }
        throw
    }
    if ($Publish) { Publish-VerifiedRelease -Directory $official -Manifest $verified }
    @{status='success'; version=$version; commit=$head; published=[bool]$Publish} | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $sessionDirectory 'result.json') -Encoding utf8
    Write-Host "Signed official release ready: release\ (v$version)." -ForegroundColor Green
} catch {
    if ($sessionDirectory) {
        @{status='failed'; error=$_.Exception.Message} | ConvertTo-Json |
            Set-Content -LiteralPath (Join-Path $sessionDirectory 'result.json') -Encoding utf8
    }
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
}
exit 0
