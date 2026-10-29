param(
    [string]$Distro = 'Ubuntu',
    [string]$LinuxUser = 'administrator',
    [string]$ProjectPath = '/home/administrator/projects/nse-fo-low-latency-decoder'
)
$ErrorActionPreference = 'Stop'
function Quote-PS([string]$value) { return "'" + $value.Replace("'", "''") + "'" }
$wsl = Join-Path $env:SystemRoot 'System32\wsl.exe'
$command = '& ' + (Quote-PS $wsl) + ' -d ' + (Quote-PS $Distro) +
    ' -u ' + (Quote-PS $LinuxUser) + ' --exec /bin/bash ' +
    (Quote-PS ($ProjectPath + '/scripts/bod_tick.sh')) + '; exit $LASTEXITCODE'
$action = New-ScheduledTaskAction -Execute 'powershell.exe' -Argument (
    '-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -Command "' +
    $command + '"')
$daily = New-ScheduledTaskTrigger -Daily -At '08:45'
$ist = [TimeZoneInfo]::ConvertTimeBySystemTimeZoneId([DateTime]::UtcNow, 'India Standard Time')
$daily.StartBoundary = $ist.ToString('yyyy-MM-dd') + 'T08:45:00+05:30'
$repeat = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(5) -RepetitionInterval (New-TimeSpan -Minutes 5) -RepetitionDuration (New-TimeSpan -Days 1)
$daily.Repetition = $repeat.Repetition
$identity = [Security.Principal.WindowsIdentity]::GetCurrent().Name
$logon = New-ScheduledTaskTrigger -AtLogOn -User $identity
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 30) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -Hidden
Register-ScheduledTask -TaskName 'NSE-KE2-Daily-BOD' -Action $action -Trigger @($daily,$logon) -Principal $principal -Settings $settings -Description '08:45 IST BOD, five-minute retries/health checks, WSL startup and supervised NSE/BSE feeds.' -Force | Out-Null
Get-ScheduledTask -TaskName 'NSE-KE2-Daily-BOD' | Select-Object TaskName,State
Get-ScheduledTaskInfo -TaskName 'NSE-KE2-Daily-BOD' | Select-Object NextRunTime,LastTaskResult
