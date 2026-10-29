param([switch]$MastersOnly, [string]$OutputRoot, [string]$ShareRoot)
# Copy authoritative feed/reference inputs; never stop or restart services.
$ErrorActionPreference = 'Stop'
$root = if ($OutputRoot) { $OutputRoot } else { Split-Path -Parent $PSScriptRoot }
$share = if ($ShareRoot) { $ShareRoot } elseif ($env:KE2_INPUT_SHARE) { $env:KE2_INPUT_SHARE } else { '\\172.16.1.85\Shared' }
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
Copy-Verified (Join-Path $share 'BSEIndexTokens.csv') (Join-Path $root 'data\reference_import\BSEIndexTokens.csv')
if ($MastersOnly) { return }
$daily = Join-Path $share 'Hardik\Project_Codes\GammaShortDailyProcess'
Copy-Verified (Join-Path $daily 'GammaShortDailydata.csv') (Join-Path $root 'data\gamma_inputs\GammaShortDailydata.csv')
Copy-Verified (Join-Path $share 'EventDates.xlsx') (Join-Path $root 'data\gamma_inputs\EventDates.xlsx')
$lut = Join-Path $share 'GammaShortData\GammaShort\LUTS_r46V2_cwnw_adj007_005_T13_5_2_2_6'
foreach ($name in @('LUT_0916.csv','LUT_0917.csv','LUT_0918.csv','LUT_0919.csv','LUT_0920_Onwards.csv','NW_LUT.csv')) {
    Copy-Verified (Join-Path $lut $name) (Join-Path $root ('live_lut\final_r46V2\' + $name))
}

# Previous available trading-day references. Dates come from filenames, not
# filesystem order; never label an older file as today's exchange data.
$bhav = @()
foreach ($item in @(
    @('NSEfobhav','NSEfo','shared\bod_bhavcopy\nse-fo'),
    @('BSEfobhav','BSEfo','shared\bod_bhavcopy\bse-fo'),
    @('NSEcmbhav','NSEcm','shared\cm_bhavcopy')
)) {
    $directory = Join-Path $share ('Hardik\Bhavcopy\' + $item[0])
    $pattern = '^' + $item[1] + '(\d{2}[A-Za-z]{3}\d{4})bhav\.csv$'
    $latest = Get-ChildItem -LiteralPath $directory -File |
        ForEach-Object {
            if ($_.Name -match $pattern) {
                $date = [datetime]::ParseExact($Matches[1], 'ddMMMyyyy',
                    [Globalization.CultureInfo]::InvariantCulture)
                if ($date.Date -lt [datetime]::Today) {
                    [PSCustomObject]@{ File = $_; TradeDate = $date }
                }
            }
        } | Sort-Object TradeDate -Descending | Select-Object -First 1
    if (!$latest) { throw "No previous available bhavcopy in $directory" }
    $target = Join-Path $root ($item[2] + '\current.csv')
    Copy-Verified $latest.File.FullName $target
    $bhav += [PSCustomObject]@{ Segment=$item[1]; Source=$latest.File.FullName;
        TradeDate=$latest.TradeDate.ToString('yyyy-MM-dd'); Target=$target }
}
$bhav | ConvertTo-Json | Set-Content -LiteralPath (Join-Path $root 'data\reference_import\BHAVCOPY_SOURCES.json') -Encoding UTF8
