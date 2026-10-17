# Copy authoritative feed/reference inputs; never stop or restart services.
$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $PSScriptRoot
$share = if ($env:KE2_INPUT_SHARE) { $env:KE2_INPUT_SHARE } else { '\\172.16.1.85\Shared' }
function Copy-Verified([string]$source, [string]$target) {
    $before = Get-Item -LiteralPath $source
    $parent = Split-Path -Parent $target
    New-Item -ItemType Directory -Path $parent -Force | Out-Null
    $temp = $target + '.incoming-' + $PID
    try {
        Copy-Item -LiteralPath $source -Destination $temp
        $after = Get-Item -LiteralPath $source
        if ($before.Length -ne $after.Length -or $before.LastWriteTimeUtc -ne $after.LastWriteTimeUtc) {
            throw "Source changed during copy: $source"
        }
        if ((Get-FileHash -LiteralPath $source -Algorithm SHA256).Hash -ne (Get-FileHash -LiteralPath $temp -Algorithm SHA256).Hash) {
            throw "Checksum mismatch: $source"
        }
        Move-Item -LiteralPath $temp -Destination $target -Force
        Write-Output ('VERIFIED_INPUT=' + $target)
    } finally {
        if (Test-Path -LiteralPath $temp) { Remove-Item -LiteralPath $temp }
    }
}
Copy-Verified (Join-Path $share 'GreekTokens.csv') (Join-Path $root 'data\reference_import\GreekTokens.csv')
$daily = Join-Path $share 'Hardik\Project_Codes\GammaShortDailyProcess'
Copy-Verified (Join-Path $daily 'GammaShortDailydata.csv') (Join-Path $root 'data\gamma_inputs\GammaShortDailydata.csv')
Copy-Verified (Join-Path $share 'EventDates.xlsx') (Join-Path $root 'data\gamma_inputs\EventDates.xlsx')
$lut = Join-Path $share 'GammaShortData\GammaShort\LUTS_r46V2_cwnw_adj007_005_T13_5_2_2_6'
foreach ($name in @('LUT_0916.csv','LUT_0917.csv','LUT_0918.csv','LUT_0919.csv','LUT_0920_Onwards.csv','NW_LUT.csv')) {
    Copy-Verified (Join-Path $lut $name) (Join-Path $root ('live_lut\final_r46V2\' + $name))
}
