[CmdletBinding()]
param(
    [Parameter(Mandatory = $true)][string]$FilePath,
    [Parameter(Mandatory = $true)][string]$SignToolPath,
    [Parameter(Mandatory = $true)][string]$CertificateThumbprint,
    [Parameter(Mandatory = $true)][string]$TimestampServer,
    [Parameter(Mandatory = $true)][string]$UninstallerArchiveDirectory
)
$ErrorActionPreference = 'Stop'
try {
    . (Join-Path $PSScriptRoot 'Signing.ps1')
    Invoke-InnoSignAndArchive @PSBoundParameters
    exit 0
} catch {
    Write-Error -Message $_.Exception.Message -ErrorAction Continue
    exit 1
}
