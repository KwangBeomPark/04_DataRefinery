[CmdletBinding()]
param(
    [switch]$Publish,
    [switch]$PublishOnly,
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
    foreach ($candidate in $candidates) {
        if (-not (Test-Path -LiteralPath $candidate -PathType Leaf)) { continue }
        # ISCC can expose 0.0.0.0 file metadata. setup.iss checks the compiler's own VER.
        return $candidate
    }
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
        if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch '(^|,)\s*O=Microsoft Corporation(,|$)') {
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
    $entries = @(Get-ChildItem -LiteralPath $Directory -Force)
    if (@($entries | Where-Object { $_.PSIsContainer -or ($_.Attributes -band [IO.FileAttributes]::ReparsePoint) }).Count) {
        throw 'The official release must be a flat directory without linked paths.'
    }
    if ($manifest.status -ne 'signed' -or $manifest.productId -ne 'App04_DataRefinery') { throw 'Only signed official artifacts can be released.' }
    if ($manifest.version -notmatch '^\d+\.\d+\.\d+$' -or $manifest.commit -notmatch '^[0-9a-f]{40}$') { throw 'Invalid release identity.' }
    $binaryNames = @("App04_DataRefinery_Setup_v$($manifest.version).exe", "DataRefinery-Setup.v$($manifest.version).exe", 'App04_DataRefinery_Launcher.exe')
    if (@($manifest.artifacts).Count -ne 3 -or @(Compare-Object ($binaryNames | Sort-Object) (@($manifest.artifacts.name) | Sort-Object)).Count) {
        throw 'The manifest must cover both installer names and the launcher exactly once.'
    }
    if ((Get-FileHash -LiteralPath (Join-Path $Directory $binaryNames[0])).Hash -ne
        (Get-FileHash -LiteralPath (Join-Path $Directory $binaryNames[1])).Hash) { throw 'Installer alias bytes must be identical.' }
    $allowedFiles = @($binaryNames) + @($binaryNames | ForEach-Object { $_ + '.sha256' }) + @("RELEASE_NOTES_v$($manifest.version).md", 'build-manifest.json', 'SHA256SUMS.txt')
    $presentFiles = @($entries | Where-Object { -not $_.PSIsContainer }).Name
    if (@(Compare-Object ($allowedFiles | Sort-Object) ($presentFiles | Sort-Object)).Count) { throw 'Official release contains missing or unexpected files.' }
    foreach ($record in $manifest.artifacts) {
        if ($record.name -ne (Split-Path -Leaf $record.name)) { throw 'Invalid manifest artifact name.' }
        $path = Join-Path $Directory $record.name
        Assert-ReleaseSignature -FilePath $path -Thumbprint $Thumbprint -ToolPath $ToolPath
        if ((Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant() -ne $record.sha256 -or (Get-Item -LiteralPath $path).Length -ne $record.size -or $record.signerThumbprint -ne $Thumbprint) {
            throw "Manifest content mismatch: $path"
        }
        $sidecar = [IO.File]::ReadAllText("$path.sha256").Trim()
        if ($sidecar -ne "$($record.sha256) *$($record.name)") { throw 'The binary checksum sidecar is inconsistent.' }
    }
    $note = "RELEASE_NOTES_v$($manifest.version).md"
    if ((Get-FileHash -LiteralPath (Join-Path $Directory $note)).Hash -ne
        (Get-FileHash -LiteralPath (Join-Path $script:ReleaseProjectRoot "docs\release-notes\$note")).Hash) { throw 'Release notes differ from their source.' }
    $sumBytes = [IO.File]::ReadAllBytes((Join-Path $Directory 'SHA256SUMS.txt'))
    if ($sumBytes.Length -ge 3 -and $sumBytes[0] -eq 239 -and $sumBytes[1] -eq 187 -and $sumBytes[2] -eq 191) { throw 'Checksum files must not have a UTF8 BOM.' }
    $expectedFiles = @($entries | Where-Object { $_.Name -ne 'SHA256SUMS.txt' }).Name
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

function Invoke-NativeQuery {
    param([string]$Command, [string[]]$Arguments)
    $null = Get-Command $Command -ErrorAction Stop
    $savedPreference = $ErrorActionPreference
    try {
        # PS 5.1 turns redirected stderr into ErrorRecords. Inspect the exit code explicitly.
        $ErrorActionPreference = 'Continue'
        $output = & $Command @Arguments 2>$null
        $exitCode = $LASTEXITCODE
    } finally { $ErrorActionPreference = $savedPreference }
    return [pscustomobject]@{ exitCode=$exitCode; output=($output -join "`n") }
}

function ConvertFrom-ReleaseArray {
    param([string]$Json)
    $parsed = ConvertFrom-Json -InputObject $Json
    foreach ($item in $parsed) { Write-Output $item }
}

function Get-LocalReleaseTag {
    param([string]$Version)
    $query = Invoke-NativeQuery -Command git -Arguments @('rev-parse','-q','--verify',"refs/tags/v$Version^{commit}")
    if ($query.exitCode -eq 0) { return $query.output.Trim() }
    if ($query.exitCode -ne 1) { throw 'Local tag lookup failed.' }
    return $null
}

function Get-RemoteReleaseTag {
    param([string]$Version)
    $ref = "refs/tags/v$Version"
    $query = Invoke-NativeQuery -Command git -Arguments @('ls-remote','--tags','origin',$ref,($ref+'^{}'))
    if ($query.exitCode -ne 0) { throw 'Remote tag lookup failed; check connectivity.' }
    $tagCommit = $null
    foreach ($line in ($query.output -split "`n")) {
        if (-not $line) { continue }
        $parts = $line.Trim() -split '\s+',2
        if ($parts[1] -eq ($ref+'^{}')) { return $parts[0] }
        if ($parts[1] -eq $ref) { $tagCommit = $parts[0] }
    }
    return $tagCommit
}

function Assert-UnreleasedVersion {
    param([string]$Version)
    if ((Get-LocalReleaseTag -Version $Version) -or (Get-RemoteReleaseTag -Version $Version)) {
        throw 'Version already tagged. Bump src/version.py, or use -PublishOnly to resume verified uploads without resigning.'
    }
}

function Assert-BuildBundle {
    param([string]$Directory, [object[]]$Records)
    $entries = @(Get-ChildItem -LiteralPath $Directory -Recurse -Force)
    if (@($entries | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) { throw 'Linked bundle inputs are not allowed.' }
    if (@($entries | Where-Object { -not $_.PSIsContainer }).Count -ne $Records.Count) { throw 'Bundle file list changed after building.' }
    $seen = @{}
    foreach ($record in $Records) {
        $path = Assert-ProjectPath (Join-Path $Directory $record.relativePath)
        if ($seen.ContainsKey($record.relativePath) -or
            -not $path.StartsWith($Directory+'\', [StringComparison]::OrdinalIgnoreCase) -or
            (Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant() -ne $record.sha256) { throw 'Bundle changed after building.' }
        $seen[$record.relativePath] = $true
    }
}

function Get-GitHubReleaseState {
    param([string]$Version)
    $repoQuery = Invoke-NativeQuery -Command gh -Arguments @('repo','view','--json','nameWithOwner,isPrivate')
    if ($repoQuery.exitCode -ne 0) { throw 'GitHub repository lookup failed; no remote mutations were attempted.' }
    $repository = $repoQuery.output | ConvertFrom-Json
    $query = Invoke-NativeQuery -Command gh -Arguments @('release','list','--limit','1000','--json','tagName,isDraft')
    if ($query.exitCode -ne 0) { throw 'GitHub release inventory lookup failed; authentication and network errors are not treated as absence.' }
    $releaseMatches = @(ConvertFrom-ReleaseArray -Json $query.output | Where-Object { $_.tagName -eq "v$Version" })
    if ($releaseMatches.Count -gt 1) { throw 'Ambiguous GitHub release identity.' }
    return [pscustomobject]@{ repository=$repository; release=$(if ($releaseMatches.Count) { $releaseMatches[0] } else { $null }) }
}

function Get-UploadedRelease {
    param([object]$Repository, [string]$Tag)
    $query = Invoke-NativeQuery -Command gh -Arguments @('api',"repos/$($Repository.nameWithOwner)/releases/tags/$Tag")
    if ($query.exitCode -eq 0) { return ($query.output | ConvertFrom-Json) }
    # Public GitHub metadata can be independently checked with Windows' HTTP stack.
    if (-not $Repository.isPrivate) {
        return Invoke-RestMethod -Uri "https://api.github.com/repos/$($Repository.nameWithOwner)/releases/tags/$Tag" -Headers @{'User-Agent'='DataRefinery-Release-Verification'} -TimeoutSec 25
    }
    throw 'Cannot verify the private GitHub release.'
}

function Get-MissingReleaseAssets {
    param([object]$Uploaded, [string[]]$Paths)
    foreach ($path in $Paths) {
        $assetMatches = @($Uploaded.assets | Where-Object { $_.name -eq (Split-Path -Leaf $path) })
        if (-not $assetMatches.Count) { Write-Output $path; continue }
        if ($assetMatches.Count -ne 1 -or $assetMatches[0].state -ne 'uploaded' -or
            $assetMatches[0].size -ne (Get-Item -LiteralPath $path).Length -or
            $assetMatches[0].digest -ne ('sha256:'+(Get-FileHash -LiteralPath $path).Hash.ToLowerInvariant())) {
            throw "An existing uploaded file differs; overwriting is forbidden: $path"
        }
    }
}

function Publish-VerifiedRelease {
    param([string]$Directory, [object]$Manifest)
    $tag = 'v' + $Manifest.version
    $state = Get-GitHubReleaseState -Version $Manifest.version
    foreach ($tagCommit in @((Get-LocalReleaseTag -Version $Manifest.version),(Get-RemoteReleaseTag -Version $Manifest.version))) {
        if ($tagCommit -and $tagCommit -ne $Manifest.commit) { throw 'An existing release tag identifies a different source commit.' }
    }
    $assets = @(Get-ChildItem -LiteralPath $Directory -File -Force).FullName
    if ($state.release) {
        $uploaded = Get-UploadedRelease -Repository $state.repository -Tag $tag
        # Refuse any changed existing file before pushing or uploading anything.
        $null = Get-MissingReleaseAssets -Uploaded $uploaded -Paths $assets
    }
    & git push origin main
    if ($LASTEXITCODE -ne 0) { throw 'Source push failed.' }
    $runs = @()
    for ($attempt = 0; $attempt -lt 12; $attempt++) {
        $runJson = & gh run list --commit $Manifest.commit --workflow windows-release-check.yml --limit 1 --json databaseId,status,conclusion
        if ($LASTEXITCODE -ne 0) { throw 'Cannot query the Windows release check.' }
        $runs = @(ConvertFrom-ReleaseArray -Json ($runJson -join "`n"))
        if ($runs.Count) { break }
        Start-Sleep -Seconds 5
    }
    if (-not $runs.Count) { throw 'No Windows release check was found for this source commit.' }
    if ($runs[0].status -ne 'completed') {
        & gh run watch $runs[0].databaseId --exit-status
        if ($LASTEXITCODE -ne 0) { throw 'Windows release check failed.' }
    } elseif ($runs[0].conclusion -ne 'success') { throw 'Windows release check did not pass.' }
    if (-not (Get-LocalReleaseTag -Version $Manifest.version)) {
        & git tag -a $tag $Manifest.commit -m "Data Refinery $tag"
        if ($LASTEXITCODE -ne 0) { throw 'Release tag creation failed.' }
    }
    & git push origin $tag
    if ($LASTEXITCODE -ne 0) { throw 'Release tag push failed.' }
    if (-not $state.release) {
        & gh release create $tag --draft --verify-tag --title "Data Refinery $tag" --notes-file (Join-Path $Directory "RELEASE_NOTES_$tag.md") @assets
        if ($LASTEXITCODE -ne 0) { throw 'Draft upload failed. Resume with -PublishOnly; do not resign.' }
    } else {
        $missing = @(Get-MissingReleaseAssets -Uploaded $uploaded -Paths $assets)
        if ($missing.Count) {
            & gh release upload $tag @missing
            if ($LASTEXITCODE -ne 0) { throw 'Missing asset upload failed. Resume with -PublishOnly.' }
        }
    }
    $uploaded = Get-UploadedRelease -Repository $state.repository -Tag $tag
    if (@(Get-MissingReleaseAssets -Uploaded $uploaded -Paths $assets).Count) { throw 'Uploaded release is incomplete.' }
    if ($uploaded.draft) {
        & gh release edit $tag --draft=false --latest
        if ($LASTEXITCODE -ne 0) { throw 'Verified draft publication failed. Resume with -PublishOnly.' }
    }
    $published = Get-UploadedRelease -Repository $state.repository -Tag $tag
    if ($published.draft -or $published.prerelease -or $published.tag_name -ne $tag -or
        @(Get-MissingReleaseAssets -Uploaded $published -Paths $assets).Count) { throw 'Stable published release verification failed.' }
}

if ($FunctionsOnly) { return }
Set-Location -LiteralPath $script:ReleaseProjectRoot
$sessionDirectory = $null
$transcriptStarted = $false
$promoted = $false
try {
    if ($VerifyOnly) {
        if ($Publish -or $PublishOnly -or $InnoFilePath) { throw 'VerifyOnly cannot sign or publish.' }
        $tool = Find-ReleaseSignTool -RequestedPath $SignToolPath
        $null = Assert-ReleaseDirectory -Directory (Join-Path $script:ReleaseProjectRoot 'release') -Thumbprint $CertificateThumbprint -ToolPath $tool
        Write-Host 'Official release signatures, timestamps, manifest and all checksums verified.'
        exit 0
    }
    if ($PublishOnly) {
        if ($Publish -or $InnoFilePath) { throw 'PublishOnly cannot resign files.' }
        $tool = Find-ReleaseSignTool -RequestedPath $SignToolPath
        $head = & git rev-parse HEAD
        $branch = & git branch --show-current
        $dirty = & git status --porcelain
        if ($LASTEXITCODE -ne 0 -or $dirty -or $branch -ne 'main') { throw 'Publish only a clean committed main branch.' }
        $official = Assert-ProjectPath (Join-Path $script:ReleaseProjectRoot 'release')
        $verified = Assert-ReleaseDirectory -Directory $official -Thumbprint $CertificateThumbprint -ToolPath $tool
        if ($verified.commit -ne $head -or $verified.version -ne (Get-ReleaseVersion)) { throw 'Official artifacts do not match this source commit/version.' }
        $sessionDirectory = Assert-ProjectPath (Join-Path $script:ReleaseProjectRoot ('tools\release-history\publish-' + [guid]::NewGuid().ToString('N')))
        New-Item -ItemType Directory -Path $sessionDirectory | Out-Null
        Publish-VerifiedRelease -Directory $official -Manifest $verified
        @{status='success'; published=$true; promoted=$false; commit=$head} | ConvertTo-Json |
            Set-Content -LiteralPath (Join-Path $sessionDirectory 'result.json') -Encoding utf8
        Write-Host 'Verified uploads completed without accessing the signing key.'
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
    if ($inputRecord.version -ne $version -or $inputRecord.commit -ne $head -or -not $inputRecord.sourceClean -or -not $inputRecord.testsPassed) { throw 'A tested build matching this clean source commit is required.' }
    Assert-UnreleasedVersion -Version $version
    $bundleName = "App04_DataRefinery_v$version"
    $sourceBundle = Assert-ProjectPath (Join-Path $script:ReleaseProjectRoot "dist\$bundleName")
    Assert-BuildBundle -Directory $sourceBundle -Records $inputRecord.bundleFiles
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
    Assert-BuildBundle -Directory $signedBundle -Records $inputRecord.bundleFiles
    $app = Join-Path $signedBundle "$bundleName.exe"
    $launcher = Join-Path $work 'App04_DataRefinery_Launcher.exe'
    if ((Get-FileHash -LiteralPath $launcher).Hash.ToLowerInvariant() -ne $inputRecord.launcherSha256) { throw 'Launcher copy changed after building.' }
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
    $promoted = $true
    if ($Publish) { Publish-VerifiedRelease -Directory $official -Manifest $verified }
    @{status='success'; version=$version; commit=$head; published=[bool]$Publish; promoted=$promoted} | ConvertTo-Json |
        Set-Content -LiteralPath (Join-Path $sessionDirectory 'result.json') -Encoding utf8
    Write-Host "Signed official release ready: release\ (v$version)." -ForegroundColor Green
} catch {
    if ($sessionDirectory) {
        @{status='failed'; error=$_.Exception.Message; promoted=$promoted} | ConvertTo-Json |
            Set-Content -LiteralPath (Join-Path $sessionDirectory 'result.json') -Encoding utf8
    }
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
} finally {
    if ($transcriptStarted) { Stop-Transcript | Out-Null }
}
exit 0
