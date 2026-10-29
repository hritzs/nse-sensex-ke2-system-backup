#!/usr/bin/env python3
"""Install the user-owned supervised stack and Windows BOD wake-up task."""
import getpass
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

ROOT=Path(__file__).resolve().parents[1]
BOD=ROOT/"data/bod"

def run(args):
    return subprocess.run(args,check=True,text=True)

def main():
    if sys.prefix==sys.base_prefix:
        raise SystemExit("Use ./venv/bin/python scripts/install_bod.py")
    BOD.mkdir(parents=True,exist_ok=True)
    config=BOD/"config.json"
    if not config.exists():
        config.write_text(json.dumps({"environment":{
            "GS_SIM_DB":str(ROOT/"data/gamma_weekly_simulation.db"),
            "BSE_LOCAL_IP":"0.0.0.0","BSE_EQ_LOCAL_IP":"0.0.0.0"}},indent=2)+"\n")
    unit=f"""[Unit]
Description=NSE BSE KE2 and all-expiry Gamma supervised stack
StartLimitIntervalSec=120
StartLimitBurst=5

[Service]
Type=simple
WorkingDirectory={ROOT}
ExecStart={ROOT}/venv/bin/python -u {ROOT}/scripts/managed_stack.py
Restart=on-failure
RestartSec=10
KillMode=control-group
KillSignal=SIGTERM
TimeoutStopSec=25
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=default.target
"""
    template=ROOT/"ops/systemd/nse-ke2-stack.service"
    template.parent.mkdir(parents=True,exist_ok=True);template.write_text(unit)
    directory=Path.home()/".config/systemd/user";directory.mkdir(parents=True,exist_ok=True)
    target=directory/"nse-ke2-stack.service"
    if target.exists() and target.read_text()!=unit:
        shutil.copy2(target,BOD/"previous-stack.service")
    target.write_text(unit)
    run(["systemctl","--user","daemon-reload"])
    script=subprocess.check_output(["wslpath","-w",str(ROOT/"scripts/install_bod_task.ps1")],text=True).strip()
    # A task registered once with UAC can be queried by the normal user.
    # Reuse only this project's expected task; never overwrite unrelated tasks.
    query=subprocess.run(["powershell.exe","-NoProfile","-NonInteractive","-Command",
        "$ErrorActionPreference='Stop'; $t=Get-ScheduledTask -TaskName 'NSE-KE2-Daily-BOD'; [pscustomobject]@{Actions=@($t.Actions | ForEach-Object {[pscustomobject]@{Arguments=$_.Arguments}}); Triggers=@($t.Triggers | ForEach-Object {[pscustomobject]@{StartBoundary=$_.StartBoundary;Repetition=@{Interval=$_.Repetition.Interval}}})} | ConvertTo-Json -Depth 6"],
        capture_output=True,text=True)
    installed_task=False
    if query.returncode==0:
        task=json.loads(query.stdout)
        actions=task.get("Actions",[])
        if isinstance(actions,dict):actions=[actions]
        triggers=task.get("Triggers",[])
        if isinstance(triggers,dict):triggers=[triggers]
        installed_task=(any(str(ROOT)+"/scripts/bod_tick.sh" in a.get("Arguments","") for a in actions)
            and any("T08:45:00+05:30" in (t.get("StartBoundary") or "")
                    and t.get("Repetition",{}).get("Interval")=="PT5M" for t in triggers))
    if not installed_task:
        run(["powershell.exe","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass",
             "-File",script,"-Distro",os.environ.get("WSL_DISTRO_NAME","Ubuntu"),
             "-LinuxUser",getpass.getuser(),"-ProjectPath",str(ROOT)])
    (BOD/"installed.json").write_text(json.dumps({
        "windows_task":"NSE-KE2-Daily-BOD","user_service":"nse-ke2-stack.service",
        "bod_time":"08:45 Asia/Kolkata","retry_minutes":5,
        "windows_login_required":True},indent=2)+"\n")
    run([sys.executable,"-u",str(ROOT/"scripts/bod_manager.py"),"--start"])

if __name__=="__main__":
    main()
