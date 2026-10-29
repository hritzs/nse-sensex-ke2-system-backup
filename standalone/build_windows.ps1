param(
    [string]$Python = "$env:LOCALAPPDATA\WhiteGammaBuild\Python314\python.exe"
)
$ErrorActionPreference = "Stop"
$ProjectRoot = Split-Path $PSScriptRoot -Parent
$BuildPython = Join-Path $PSScriptRoot ".buildenv\Scripts\python.exe"
if (!(Test-Path -LiteralPath $Python)) { throw "Python build runtime missing: $Python" }
if (!(Test-Path -LiteralPath $BuildPython)) {
    & $Python -m venv (Join-Path $PSScriptRoot ".buildenv")
    if ($LASTEXITCODE) { throw "venv creation failed" }
}
& $BuildPython -m pip install -r (Join-Path $PSScriptRoot "requirements-windows.lock.txt")
if ($LASTEXITCODE) { throw "Build dependency install failed" }
$LocalBuild = Join-Path $env:LOCALAPPDATA "WhiteGammaBuild\pyinstaller"
New-Item -ItemType Directory -Force -Path $LocalBuild | Out-Null
$BuildArguments = @("-m", "PyInstaller", "--noconfirm", "--clean", "--workpath", $LocalBuild, "--distpath", (Join-Path $ProjectRoot "dist\whitegamma-standalone"), (Join-Path $PSScriptRoot "WhiteGammaStandalone.spec"))
& $BuildPython @BuildArguments
if ($LASTEXITCODE) { throw "PyInstaller build failed" }
Write-Host "Built: $(Join-Path $ProjectRoot 'dist\whitegamma-standalone\WhiteGammaStandalone.exe')"
