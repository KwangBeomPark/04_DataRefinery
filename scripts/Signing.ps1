Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Invoke-SignBinary {
    [CmdletBinding()]
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,

        [string]$CertificateThumbprint = $env:SIGN_CERT_THUMBPRINT,

        [string]$TimestampServer = "http://timestamp.digicert.com"
    )

    if ([string]::IsNullOrWhiteSpace($CertificateThumbprint)) {
        # Default to user's code signing certificate thumbprint
        $CertificateThumbprint = "E9C72CF5090840A1805296525D56BE680622A7FD"
    }

    if (-not (Test-Path -LiteralPath $FilePath)) {
        throw "File to sign not found: $FilePath"
    }

    $cert = Get-Item "Cert:\CurrentUser\My\$CertificateThumbprint" -ErrorAction SilentlyContinue
    if (-not $cert) {
        $certs = @(Get-ChildItem Cert:\CurrentUser\My -CodeSigningCert | Where-Object { $_.Thumbprint -eq $CertificateThumbprint })
        if ($certs.Count -gt 0) {
            $cert = $certs[0]
        }
    }

    if (-not $cert) {
        Write-Warning "Code signing certificate '$CertificateThumbprint' not found in Cert:\CurrentUser\My. Skipping signing."
        return $false
    }

    Write-Host "Signing $FilePath with certificate: $($cert.Subject) [$($cert.Thumbprint)]"

    # Check for signtool on PATH or standard Windows SDK locations
    $signtoolPath = $null
    $onPath = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($null -ne $onPath) {
        $signtoolPath = $onPath.Source
    }

    if ($null -ne $signtoolPath) {
        Write-Host "Using signtool.exe at: $signtoolPath"
        $signArgs = @("sign", "/sha1", $CertificateThumbprint, "/fd", "sha256")
        if (-not [string]::IsNullOrWhiteSpace($TimestampServer)) {
            $signArgs += @("/tr", $TimestampServer, "/td", "sha256")
        }
        $signArgs += $FilePath
        & $signtoolPath $signArgs
        if ($LASTEXITCODE -ne 0) {
            throw "signtool failed with exit code $LASTEXITCODE"
        }
    } else {
        Write-Host "Using PowerShell Set-AuthenticodeSignature..."
        $signParams = @{
            FilePath      = $FilePath
            Certificate   = $cert
            HashAlgorithm = "SHA256"
        }
        if (-not [string]::IsNullOrWhiteSpace($TimestampServer)) {
            $signParams.TimestampServer = $TimestampServer
        }
        $result = Set-AuthenticodeSignature @signParams
        if ($result.Status -ne "Valid") {
            Write-Warning "Set-AuthenticodeSignature status: $($result.Status) ($($result.StatusMessage))"
        }
    }

    $verified = Get-AuthenticodeSignature -LiteralPath $FilePath
    if ($verified.Status -eq "Valid") {
        Write-Host "Signature successfully verified for: $FilePath" -ForegroundColor Green
        return $true
    } else {
        Write-Warning "Signature verification returned status: $($verified.Status)"
        return $false
    }
}
