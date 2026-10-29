param([string]$Python = "$PSScriptRoot\.buildenv\Scripts\python.exe")
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$Arguments = @("-m","PyInstaller","--noconfirm","--workpath","$env:LOCALAPPDATA\WhiteGammaBuild\split","--distpath",(Join-Path $ProjectRoot "dist\windows-split"),(Join-Path $PSScriptRoot "SeparateApps.spec"))
& $Python @Arguments
if ($LASTEXITCODE) { throw "Separate executable build failed" }
