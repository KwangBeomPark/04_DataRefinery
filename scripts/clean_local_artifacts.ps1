[CmdletBinding(SupportsShouldProcess = $true)]
param([switch]$Apply)

# Without -Apply, list exact candidates only. Never touch AppData or signed releases.
$ErrorActionPreference = 'Stop'
$projectRoot = (Resolve-Path -LiteralPath (Split-Path -Parent $PSScriptRoot)).Path
$relativePaths = @(
    '.pytest_cache',
    '.ruff_cache',
    'build\app',
    'build\launcher',
    'build\venv',
    'dist',
    'sample_data\monthly_ledger_sample_aggregated_20260913_215528.xlsx',
    'sample_data\월별실적원장_샘플_aggregated_20260912_232308.xlsx',
    'sample_data\월별실적원장_샘플_aggregated_20260912_232456.xlsx'
)

# Validate every target and descendant before any recursive deletion.
$targets = @()
foreach ($relativePath in $relativePaths) {
    $path = Join-Path $projectRoot $relativePath
    if (-not (Test-Path -LiteralPath $path)) { continue }
    $resolved = (Resolve-Path -LiteralPath $path).Path
    if (-not $resolved.StartsWith($projectRoot + '\', [StringComparison]::OrdinalIgnoreCase)) {
        throw "Cleanup target is outside the project: $resolved"
    }
    $item = Get-Item -LiteralPath $resolved -Force
    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) {
        throw "Cleanup refuses a linked target: $resolved"
    }
    $files = @($item)
    if ($item.PSIsContainer) {
        $contents = @(Get-ChildItem -LiteralPath $resolved -Recurse -Force)
        if (@($contents | Where-Object { $_.Attributes -band [IO.FileAttributes]::ReparsePoint }).Count) {
            throw "Cleanup refuses a linked descendant: $resolved"
        }
        $files = @($contents | Where-Object { -not $_.PSIsContainer })
        if (@($contents | Where-Object { $_.Name -eq 'UserSetting' -or $_.Extension -in @('.pfx','.p12','.key','.duckdb','.db') }).Count) {
            throw "Cleanup refuses user settings, private certificates or databases: $resolved"
        }
    }
    foreach ($file in @($files | Where-Object { $_.Extension -in @('.exe','.e32','.e64') })) {
        $signature = Get-AuthenticodeSignature -LiteralPath $file.FullName
        if ($signature.SignerCertificate -and $signature.SignerCertificate.Subject -match 'KWANG BEOM PARK') {
            throw "Preserve signed output before cleanup: $($file.FullName)"
        }
    }
    $size = ($files | Measure-Object -Property Length -Sum).Sum
    $targets += [pscustomobject]@{
        Path = $resolved
        Files = $files.Count
        MiB = [Math]::Round($size / 1MB, 2)
    }
}

$targets | Format-Table -AutoSize
if (-not $Apply) {
    Write-Host 'Preview only. Run with -Apply to remove these validated local artifacts.'
    return
}
foreach ($target in $targets) {
    if ($PSCmdlet.ShouldProcess($target.Path, 'Remove local generated artifact')) {
        Remove-Item -LiteralPath $target.Path -Recurse -Force
    }
}
