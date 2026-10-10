$ErrorActionPreference = 'Stop'
. (Join-Path (Split-Path -Parent $PSScriptRoot) 'scripts\sign.ps1') -FunctionsOnly
$testRoot = Join-Path ([IO.Path]::GetTempPath()) ('DataRefinery-signing-test-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $testRoot | Out-Null
$toolPath = Join-Path $testRoot 'signtool.cmd'
Set-Content -LiteralPath $toolPath -Value "@echo off`r`nexit /b 0" -Encoding ascii
$thumbprint = 'E9C72CF5090840A1805296525D56BE680622A7FD'
$script:testSignature = [pscustomobject]@{
    Status='Valid'; SignerCertificate=[pscustomobject]@{Thumbprint=$thumbprint; Subject='Test publisher'};
    TimeStamperCertificate=[pscustomobject]@{Subject='Test timestamp'}
}
# No certificate/private key is accessed by these fault-injection tests.
function Get-AuthenticodeSignature { param($LiteralPath) return $script:testSignature }
$script:passedAssertions = 0
function Assert-True($Condition, $Message) {
    if (-not $Condition) { throw $Message }
    $script:passedAssertions++
}
function Assert-Rejected($Action, $Message, $ExpectedPattern) {
    $rejected = $false
    try { & $Action } catch {
        if ($_.Exception.Message -notmatch $ExpectedPattern) { throw "Unexpected failure: $($_.Exception.Message)" }
        $rejected = $true
    }
    Assert-True $rejected $Message
}
$archiveDirectory = Join-Path $testRoot 'archive'
$tempUninstaller = Join-Path $testRoot 'uninst.e64.tmp'
Set-Content -LiteralPath $tempUninstaller -Value 'compiler uninstaller input' -Encoding ascii
$arguments = @{
    FilePath=$tempUninstaller; SignToolPath=$toolPath; CertificateThumbprint=$thumbprint;
    TimestampServer='http://timestamp.digicert.com'; UninstallerArchiveDirectory=$archiveDirectory
}
try {
    Invoke-InnoSignAndArchive @arguments
    $archives = @(Get-ChildItem -LiteralPath $archiveDirectory -Filter 'uninstaller-*.exe')
    Assert-True ($archives.Count -eq 1) 'The temporary uninstaller was not archived.'
    Assert-True ((Get-FileHash $archives[0].FullName).Hash -eq (Get-FileHash $tempUninstaller).Hash) 'Archive content changed.'
    Remove-Item -LiteralPath $tempUninstaller
    Assert-True (Test-Path -LiteralPath $archives[0].FullName) 'Compiler cleanup lost the verification artifact.'

    $installer = Join-Path $testRoot 'Setup.exe'
    Set-Content -LiteralPath $installer -Value 'installer input' -Encoding ascii
    $arguments.FilePath = $installer
    Invoke-InnoSignAndArchive @arguments
    Assert-True (@(Get-ChildItem $archiveDirectory -File).Count -eq 1) 'The setup executable was misidentified as an uninstaller.'

    $arguments.UninstallerArchiveDirectory = Join-Path $testRoot 'must-not-exist'
    $script:testSignature.SignerCertificate.Thumbprint = ('A' * 40)
    Assert-Rejected { Invoke-InnoSignAndArchive @arguments } 'A wrong publisher was accepted.' 'Invalid publisher signature or timestamp'
    $script:testSignature.SignerCertificate.Thumbprint = $thumbprint
    $script:testSignature.TimeStamperCertificate = $null
    Assert-Rejected { Invoke-InnoSignAndArchive @arguments } 'A missing timestamp was accepted.' 'Invalid publisher signature or timestamp'
    $script:testSignature.TimeStamperCertificate = [pscustomobject]@{Subject='Test timestamp'}
    $script:testSignature.Status = 'NotSigned'
    Assert-Rejected { Invoke-InnoSignAndArchive @arguments } 'An unsigned binary was accepted.' 'Invalid publisher signature or timestamp'
    $script:testSignature.Status = 'Valid'
    Set-Content -LiteralPath $toolPath -Value @('@echo off','if "%1"=="verify" exit /b 5','exit /b 0') -Encoding ascii
    Assert-Rejected { Invoke-InnoSignAndArchive @arguments } 'A failed SignTool verification was accepted.' 'SignTool verification failed'
    Assert-True (-not (Test-Path -LiteralPath $arguments.UninstallerArchiveDirectory)) 'Rejected inputs created an archive.'
    Set-Content -LiteralPath $toolPath -Value "@echo off`r`nexit /b 0" -Encoding ascii
    $publicAlias = Join-Path $testRoot 'Public-Setup.exe'
    New-ReleaseAlias -Source $installer -Destination $publicAlias
    Assert-True ((Get-FileHash $publicAlias).Hash -eq (Get-FileHash $installer).Hash) 'Alias content differs.'
    $differentAlias = Join-Path $testRoot 'Different.exe'
    Set-Content -LiteralPath $differentAlias -Value 'different content'
    Assert-Rejected { New-ReleaseAlias -Source $installer -Destination $differentAlias } 'An existing alias was overwritten.' 'Refusing to overwrite'

    $official = Join-Path $testRoot 'release'
    New-Item -ItemType Directory -Path $official | Out-Null
    $binaryNames = @('App04_DataRefinery_Setup_v2.0.1.exe')
    foreach ($binaryName in $binaryNames) { Copy-Item -LiteralPath $installer -Destination (Join-Path $official $binaryName) }
    function Write-TestMetadata {
        foreach ($binaryName in $binaryNames) { Copy-Item -LiteralPath $installer -Destination (Join-Path $official $binaryName) -Force }
        Write-ReleaseMetadata -Directory $official -Version '2.0.1' -Commit ('a' * 40) -BuiltAtUtc '2026-10-06T00:00:00Z' -Thumbprint $thumbprint
    }
    Write-TestMetadata
    $null = Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath
    $contract = Get-Content -LiteralPath (Join-Path $official 'build-manifest.json') -Raw | ConvertFrom-Json
    Assert-True (@($contract.artifacts).Count -eq 1) 'Manifest has more than one installer.'
    Assert-True (@($contract.aliases.PSObject.Properties).Count -eq 0) 'New manifest declares a legacy alias.'
    Assert-True (@(Get-ChildItem -LiteralPath $official -File).Count -eq 3) 'Official set does not contain exactly three files.'
    $sumsPath = Join-Path $official 'SHA256SUMS.txt'
    Assert-True (-not ([IO.File]::ReadAllBytes($sumsPath)[0] -eq 239)) 'Checksums have a UTF8 BOM.'
    Add-Content -LiteralPath (Join-Path $official $binaryNames[0]) -Value 'tampered'
    Assert-Rejected { Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath } 'Artifact tampering was accepted.' 'alias bytes|Manifest content mismatch'
    Write-TestMetadata
    $lines = @(Get-Content -LiteralPath $sumsPath)
    [IO.File]::WriteAllText($sumsPath, '', [Text.UTF8Encoding]::new($false))
    Assert-Rejected { Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath } 'Incomplete checksums were accepted.' 'Checksum mismatch'
    Write-ReleaseMetadata -Directory $official -Version '2.0.1' -Commit ('a' * 40) -BuiltAtUtc '2026-10-06T00:00:00Z' -Thumbprint $thumbprint -RequireSignature $false
    Assert-Rejected { Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath } 'An unsigned preview was accepted as official.' 'Only signed official artifacts'
    Write-TestMetadata
    $manifestPath = Join-Path $official 'build-manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $manifest.artifacts = @()
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath
    Assert-Rejected { Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath } 'A manifest omitting all signature records was accepted.' 'manifest must cover'
    Write-TestMetadata
    $unexpected = Join-Path $official 'DataRefinery-Setup.v2.0.1.exe'
    Copy-Item -LiteralPath $installer -Destination $unexpected
    Assert-Rejected { Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath } 'An extra installer alias was accepted.' 'missing or unexpected files'
    Remove-Item -LiteralPath $unexpected
    Write-TestMetadata
    $sumContent = [IO.File]::ReadAllText($sumsPath)
    [IO.File]::WriteAllText($sumsPath,$sumContent,[Text.UTF8Encoding]::new($true))
    Assert-Rejected { Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath } 'A BOM was accepted.' 'must not have a UTF8 BOM'
    Write-TestMetadata
    New-Item -ItemType Directory -Path (Join-Path $official 'extra') | Out-Null
    Assert-Rejected { Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath } 'A nested release folder was accepted.' 'flat directory'
    Remove-Item -LiteralPath (Join-Path $official 'extra')
    Assert-True (@(ConvertFrom-ReleaseArray -Json '[]').Count -eq 0) 'An empty JSON array was boxed as one run in PS 5.1.'
    Assert-True (@(ConvertFrom-ReleaseArray -Json '[{"status":"completed"}]').Count -eq 1) 'A singleton JSON run was not parsed.'

    $savedPath = $env:PATH
    $fakeGit = Join-Path $testRoot 'git.cmd'
    $fakeGh = Join-Path $testRoot 'gh.cmd'
    $tagMarker = Join-Path $testRoot 'tag-created'
    $runMarker = Join-Path $testRoot 'run-queried'
    $absentInventory = Join-Path $testRoot 'absent-inventory'
    $callLog = Join-Path $testRoot 'native-calls.log'
    $partialApi = Join-Path $testRoot 'partial-api.json'
    $completeApi = Join-Path $testRoot 'complete-api.json'
    $publicApi = Join-Path $testRoot 'public-api.json'
    $activeApi = Join-Path $testRoot 'active-api.json'
    $uploadRecords = @()
    $publicPaths = @(Get-ChildItem -LiteralPath $official -File).FullName
    foreach ($path in $publicPaths) {
        $uploadRecords += [pscustomobject]@{name=(Split-Path -Leaf $path); state='uploaded'; size=(Get-Item $path).Length; digest=('sha256:'+(Get-FileHash $path).Hash.ToLowerInvariant())}
    }
    foreach ($fixture in @(@($partialApi,@($uploadRecords[0]),$true),@($completeApi,$uploadRecords,$true),@($publicApi,$uploadRecords,$false))) {
        [IO.File]::WriteAllText($fixture[0],(@{tag_name='v2.0.1'; draft=$fixture[2]; prerelease=$false; assets=$fixture[1]} | ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
    }
    Copy-Item -LiteralPath $partialApi -Destination $activeApi
    $gitLines = @('@echo off',('echo git %*>>"'+$callLog+'"'),
        'if "%1 %2 %3"=="remote get-url origin" (','  echo https://github.com/KwangBeomPark/04_DataRefinery.git','  exit /b 0',')',
        'if "%1"=="rev-parse" (',('  if exist "'+$tagMarker+'" (echo '+('a'*40)+'&exit /b 0)'),
        '  echo fatal: Needed a single revision 1>&2','  exit /b 1',')',
        'if "%1"=="ls-remote" (',('  if exist "'+$tagMarker+'" echo '+('a'*40)+' refs/tags/v2.0.1'),'  exit /b 0',')',
        ('if "%1"=="tag" type nul>"'+$tagMarker+'"'),'exit /b 0')
    Set-Content -LiteralPath $fakeGit -Value $gitLines -Encoding ascii
    $ghLines = @('@echo off',('echo gh %*>>"'+$callLog+'"'),
        'if "%1 %2"=="repo view" (','  echo {"nameWithOwner":"KwangBeomPark/04_DataRefinery","isPrivate":true}','  exit /b 0',')',
        'if "%1 %2"=="release list" (',('  if exist "'+$absentInventory+'" (echo []&exit /b 0)'),
        '  echo [{"tagName":"v2.0.1","isDraft":true}]','  exit /b 0',')',
        'if "%1 %2"=="release view" (','  echo {"apiUrl":"https://api.github.com/repos/KwangBeomPark/04_DataRefinery/releases/42","isDraft":true}','  exit /b 0',')',
        'if "%1 %2"=="run list" (',('  if exist "'+$runMarker+'" (echo [{"databaseId":123,"status":"completed","conclusion":"success"}]&exit /b 0)'),
        ('  type nul>"'+$runMarker+'"'),'  echo []','  exit /b 0',')',
        'if "%1"=="api" (','  if not "%2"=="repos/KwangBeomPark/04_DataRefinery/releases/42" (echo 404 Not Found 1>&2&exit /b 1)',
        ('  type "'+$activeApi+'"'),'  exit /b 0',')',
        ('if "%1 %2"=="release create" copy /y "'+$completeApi+'" "'+$activeApi+'">nul'),
        ('if "%1 %2"=="release upload" copy /y "'+$completeApi+'" "'+$activeApi+'">nul'),
        ('if "%1 %2"=="release edit" copy /y "'+$publicApi+'" "'+$activeApi+'">nul'),'exit /b 0')
    Set-Content -LiteralPath $fakeGh -Value $ghLines -Encoding ascii
    $env:PATH = $testRoot + ';' + $savedPath
    try {
        Assert-True ($null -eq (Get-LocalReleaseTag -Version '2.0.1')) 'Native stderr aborted a missing-tag lookup in PS 5.1.'
        Assert-True ($null -eq (Get-RemoteReleaseTag -Version '2.0.1')) 'An absent remote tag was misread.'
        New-Item -ItemType File -Path $tagMarker | Out-Null
        $mockManifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
        Publish-VerifiedRelease -Directory $official -Manifest $mockManifest
        $trace = [IO.File]::ReadAllText($callLog)
        Assert-True (([regex]::Matches($trace,'gh run list')).Count -eq 2) 'The empty CI result did not retry.'
        Assert-True ($trace -match 'gh release upload' -and $trace -notmatch '--clobber') 'Upload recovery was not limited to missing assets.'
        Assert-True ($trace -notmatch 'sign ' -and $trace -notmatch 'release create') 'Resume attempted to sign or recreate a draft.'
        Assert-True ($trace -match 'gh api repos/KwangBeomPark/04_DataRefinery/releases/42' -and $trace -notmatch 'releases/tags/') 'A draft was queried through the published-only tag endpoint.'
        Assert-Rejected { Assert-UnreleasedVersion -Version '2.0.1' } 'An already tagged version could be resigned.' 'Version already tagged'
        $published = Get-Content -LiteralPath $activeApi -Raw | ConvertFrom-Json
        Assert-True (@(Get-MissingReleaseAssets -Uploaded $published -Paths $publicPaths).Count -eq 0) 'Completed upload was not recognized.'
        $published.assets[0].digest = 'sha256:' + ('0'*64)
        Assert-Rejected { Get-MissingReleaseAssets -Uploaded $published -Paths $publicPaths } 'A remote mismatch would be overwritten.' 'overwriting is forbidden'
        $published.assets[0].digest = $uploadRecords[0].digest
        $published.assets[0].state = 'starter'
        Assert-Rejected { Get-MissingReleaseAssets -Uploaded $published -Paths $publicPaths } 'An incomplete upload residue was overwritten.' 'overwriting is forbidden'
        $published.assets[0].state = 'uploaded'
        $published.assets += [pscustomobject]@{name='unexpected.zip'; state='uploaded'; size=1; digest=('sha256:'+('a'*64))}
        Assert-Rejected { Get-MissingReleaseAssets -Uploaded $published -Paths $publicPaths } 'An unexpected remote asset was accepted.' 'Unexpected remote release asset'
        $published = Get-Content -LiteralPath $publicApi -Raw | ConvertFrom-Json
        $published.assets += $published.assets[0]
        Assert-Rejected { Get-MissingReleaseAssets -Uploaded $published -Paths $publicPaths } 'A duplicate remote asset was accepted.' 'overwriting is forbidden'
        $published = Get-Content -LiteralPath $publicApi -Raw | ConvertFrom-Json
        $published.assets[0].PSObject.Properties.Remove('state')
        Assert-Rejected { Get-MissingReleaseAssets -Uploaded $published -Paths $publicPaths } 'An upload without state was accepted.' 'overwriting is forbidden'
        Assert-Rejected { Get-MissingReleaseAssets -Uploaded $published -Paths @($publicPaths[0],$publicPaths[0]) } 'Duplicate local names were accepted.' 'Duplicate local release asset name'
        foreach ($scenario in @('prerelease','wrong-tag','missing-draft','missing-remote-tag')) {
            $invalidRelease = Get-Content -LiteralPath $completeApi -Raw | ConvertFrom-Json
            $expectedPattern = 'Unexpected remote release identity or prerelease state'
            switch ($scenario) {
                'prerelease' { $invalidRelease.prerelease=$true }
                'wrong-tag' { $invalidRelease.tag_name='v9.9.9' }
                'missing-draft' { $invalidRelease.PSObject.Properties.Remove('draft') }
                'missing-remote-tag' { Remove-Item -LiteralPath $tagMarker; $expectedPattern='requires a verified remote tag' }
            }
            [IO.File]::WriteAllText($activeApi,($invalidRelease | ConvertTo-Json -Depth 6),[Text.UTF8Encoding]::new($false))
            [IO.File]::WriteAllText($callLog,'')
            Assert-Rejected { Publish-VerifiedRelease -Directory $official -Manifest $mockManifest } "Unsafe existing release accepted: $scenario" $expectedPattern
            $invalidTrace = [IO.File]::ReadAllText($callLog)
            Assert-True ($invalidTrace -notmatch 'git push|git tag|gh release (create|upload|edit)') "Unsafe preflight mutated remote state: $scenario"
            if (-not (Test-Path -LiteralPath $tagMarker)) { New-Item -ItemType File -Path $tagMarker | Out-Null }
        }
        $canonicalPath = Join-Path $official $binaryNames[0]
        $casePath = Join-Path $official $binaryNames[0].ToLowerInvariant()
        Rename-Item -LiteralPath $canonicalPath -NewName 'case-only-fixture.tmp'
        Rename-Item -LiteralPath (Join-Path $official 'case-only-fixture.tmp') -NewName (Split-Path -Leaf $casePath)
        try {
            [IO.File]::WriteAllText($callLog,'')
            Assert-Rejected { Publish-VerifiedRelease -Directory $official -Manifest $mockManifest } 'Noncanonical filename casing was accepted.' 'requires exactly the canonical installer'
            Assert-True ([IO.File]::ReadAllText($callLog) -notmatch 'git push|git tag|gh release (create|upload|edit)') 'Noncanonical local names caused a remote mutation.'
        } finally {
            Rename-Item -LiteralPath $casePath -NewName 'case-only-fixture.tmp'
            Rename-Item -LiteralPath (Join-Path $official 'case-only-fixture.tmp') -NewName $binaryNames[0]
        }
        Copy-Item -LiteralPath $publicApi -Destination $activeApi
        [IO.File]::WriteAllText($callLog,'')
        Publish-VerifiedRelease -Directory $official -Manifest $mockManifest
        Assert-True ([IO.File]::ReadAllText($callLog) -notmatch 'git push|git tag|gh release (create|upload|edit)') 'A complete public release was mutated during verification.'
        # Also exercise a newly created draft, rather than only resuming an existing one.
        Remove-Item -LiteralPath $tagMarker
        New-Item -ItemType File -Path $absentInventory | Out-Null
        [IO.File]::WriteAllText($callLog,'')
        Publish-VerifiedRelease -Directory $official -Manifest $mockManifest
        $newTrace = [IO.File]::ReadAllText($callLog)
        Assert-True ($newTrace -match 'docs\\release-notes\\RELEASE_NOTES_v2.0.1.md') 'Draft notes were read from the official artifact directory.'
        Assert-True ($newTrace -match 'gh release create.*--draft' -and $newTrace -match 'gh release edit') 'A new draft did not pass verification and publish.'
        Assert-True ($newTrace -notmatch 'releases/tags/|--clobber') 'The new draft used a published-only lookup or replaced uploaded content.'
        Set-Content -LiteralPath $fakeGit -Value @('@echo off','echo https://github.com/example/wrong.git','exit /b 0') -Encoding ascii
        Assert-Rejected { Publish-VerifiedRelease -Directory $official -Manifest $mockManifest } 'A wrong origin was accepted.' 'Unexpected origin repository'
        Set-Content -LiteralPath $fakeGh -Value @('@echo off','echo authentication failed 1>&2','exit /b 1') -Encoding ascii
        Assert-Rejected { Get-GitHubReleaseState -Version '2.0.1' } 'A failed GitHub lookup was treated as absence.' 'repository lookup failed'
    } finally { $env:PATH = $savedPath }
    Write-Host "Release pipeline assertions passed: $script:passedAssertions"
} finally {
    $resolvedTestRoot = [IO.Path]::GetFullPath($testRoot)
    $temporaryBase = [IO.Path]::GetFullPath([IO.Path]::GetTempPath()).TrimEnd('\') + '\'
    if ($resolvedTestRoot.StartsWith($temporaryBase, [StringComparison]::OrdinalIgnoreCase) -and
        (Split-Path -Leaf $resolvedTestRoot) -like 'DataRefinery-signing-test-*') {
        Remove-Item -LiteralPath $resolvedTestRoot -Recurse -Force
    }
}
# Fault injection intentionally leaves a native failure code; do not leak it to CI.
exit 0
