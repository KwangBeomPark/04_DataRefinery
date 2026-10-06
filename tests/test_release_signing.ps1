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
function Assert-True($Condition, $Message) { if (-not $Condition) { throw $Message } }
function Assert-Rejected($Action, $Message) {
    $rejected = $false
    try { & $Action } catch { $rejected = $true }
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
    Assert-Rejected { Invoke-InnoSignAndArchive @arguments } 'A wrong publisher was accepted.'
    $script:testSignature.SignerCertificate.Thumbprint = $thumbprint
    $script:testSignature.TimeStamperCertificate = $null
    Assert-Rejected { Invoke-InnoSignAndArchive @arguments } 'A missing timestamp was accepted.'
    $script:testSignature.TimeStamperCertificate = [pscustomobject]@{Subject='Test timestamp'}
    $script:testSignature.Status = 'NotSigned'
    Assert-Rejected { Invoke-InnoSignAndArchive @arguments } 'An unsigned binary was accepted.'
    $script:testSignature.Status = 'Valid'
    Set-Content -LiteralPath $toolPath -Value @('@echo off','if "%1"=="verify" exit /b 5','exit /b 0') -Encoding ascii
    Assert-Rejected { Invoke-InnoSignAndArchive @arguments } 'A failed SignTool verification was accepted.'
    Assert-True (-not (Test-Path -LiteralPath $arguments.UninstallerArchiveDirectory)) 'Rejected inputs created an archive.'
    Set-Content -LiteralPath $toolPath -Value "@echo off`r`nexit /b 0" -Encoding ascii
    $publicAlias = Join-Path $testRoot 'Public-Setup.exe'
    New-ReleaseAlias -Source $installer -Destination $publicAlias
    Assert-True ((Get-FileHash $publicAlias).Hash -eq (Get-FileHash $installer).Hash) 'Alias content differs.'
    $differentAlias = Join-Path $testRoot 'Different.exe'
    Set-Content -LiteralPath $differentAlias -Value 'different content'
    Assert-Rejected { New-ReleaseAlias -Source $installer -Destination $differentAlias } 'An existing alias was overwritten.'

    $official = Join-Path $testRoot 'release'
    New-Item -ItemType Directory -Path $official | Out-Null
    $binaryNames = @('App04_DataRefinery_Setup_v2.0.1.exe','DataRefinery-Setup.v2.0.1.exe','App04_DataRefinery_Launcher.exe')
    foreach ($binaryName in $binaryNames) { Copy-Item -LiteralPath $installer -Destination (Join-Path $official $binaryName) }
    function Write-TestMetadata {
        Write-ReleaseMetadata -Directory $official -Version '2.0.1' -Commit ('a' * 40) -BuiltAtUtc '2026-10-06T00:00:00Z' -Thumbprint $thumbprint
    }
    Write-TestMetadata
    $null = Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath
    $sumsPath = Join-Path $official 'SHA256SUMS.txt'
    Assert-True (-not ([IO.File]::ReadAllBytes($sumsPath)[0] -eq 239)) 'Checksums have a UTF8 BOM.'
    Add-Content -LiteralPath (Join-Path $official $binaryNames[0]) -Value 'tampered'
    Assert-Rejected { Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath } 'Artifact tampering was accepted.'
    Write-TestMetadata
    $lines = @(Get-Content -LiteralPath $sumsPath)
    Set-Content -LiteralPath $sumsPath -Value $lines[1..($lines.Count-1)]
    Assert-Rejected { Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath } 'Incomplete checksums were accepted.'
    Write-ReleaseMetadata -Directory $official -Version '2.0.1' -Commit ('a' * 40) -BuiltAtUtc '2026-10-06T00:00:00Z' -Thumbprint $thumbprint -RequireSignature $false
    Assert-Rejected { Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath } 'An unsigned preview was accepted as official.'
    Write-TestMetadata
    $manifestPath = Join-Path $official 'build-manifest.json'
    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    $manifest.artifacts = @()
    $manifest | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $manifestPath
    Assert-Rejected { Assert-ReleaseDirectory -Directory $official -Thumbprint $thumbprint -ToolPath $toolPath } 'A manifest omitting all signature records was accepted.'
    Write-Host 'Thirteen release pipeline checks passed.'
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
