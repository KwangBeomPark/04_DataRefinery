[CmdletBinding()]
param([string]$CertificateThumbprint = 'E9C72CF5090840A1805296525D56BE680622A7FD')
$ErrorActionPreference = 'Stop'
$identity = [Security.Principal.WindowsIdentity]::GetCurrent()
$principal = [Security.Principal.WindowsPrincipal]::new($identity)
if (-not $principal.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
    throw 'Open your own administrator PowerShell and rerun this command.'
}
$thumbprint = ($CertificateThumbprint -replace '\s', '').ToUpperInvariant()
if ($thumbprint -notmatch '^[0-9A-F]{40}$') { throw 'Invalid public certificate thumbprint.' }
$certificate = Get-Item "Cert:\CurrentUser\My\$thumbprint" -ErrorAction SilentlyContinue
if (-not $certificate) { $certificate = Get-Item "Cert:\LocalMachine\My\$thumbprint" -ErrorAction SilentlyContinue }
if (-not $certificate -or -not $certificate.HasPrivateKey -or $certificate.NotBefore -gt (Get-Date) -or $certificate.NotAfter -le (Get-Date)) {
    throw 'Selected certificate/private-key reference is not available in this signing session.'
}
$signTool = Join-Path (Split-Path -Parent $PSScriptRoot) 'tools\signtool\signtool.exe'
$signature = Get-AuthenticodeSignature -LiteralPath $signTool
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notlike '*Microsoft Corporation*') {
    throw 'The signing tool must carry a valid Microsoft signature.'
}
$cardService = Get-Service SCardSvr
if ($cardService.Status -ne 'Running') { Start-Service SCardSvr; $cardService.WaitForStatus('Running', [TimeSpan]::FromSeconds(15)) }
$env:SIGNTOOL_PATH = $signTool
$env:SIGN_CERT_THUMBPRINT = $thumbprint
Write-Host 'SESSION_PREFLIGHT_OK' -ForegroundColor Green
Write-Host "Certificate: $thumbprint / expires $($certificate.NotAfter.ToString('yyyy-MM-dd'))"
Write-Host "Microsoft SignTool: $signTool"
Write-Host 'Certificate visibility is confirmed; private-key signing has not been tested by this check.'
