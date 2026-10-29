#!/usr/bin/env python3
"""Start, stop or watch all five project services from any working directory."""
import argparse
from contextlib import contextmanager
from datetime import datetime
import fcntl
import getpass
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts import restart_project_services as project
IST=ZoneInfo("Asia/Kolkata")
UNIT="nse-ke2-stack.service"
SERVICES={"decoder":9001,"ke2-adapter":9002,"simulator":8000,
          "indicative-close":8100,"api-tester":9003}

def now():
    return datetime.now(IST)

def ports():
    result=dict(SERVICES)
    active=ROOT/"data/bod/active.json"
    if active.exists():
        env=json.loads(active.read_text()).get("environment",{})
        result["simulator"]=int(env.get("MINUTE_HTTP_PORT",8000))
    return result

def audit_event(action,phase,**details):
    """Record control requests without credentials or full parent argv."""
    directory=ROOT/"logs";directory.mkdir(parents=True,exist_ok=True)
    record={"ts":now().isoformat(),"action":action,"phase":phase,
            "user":getpass.getuser(),"uid":os.getuid(),"pid":os.getpid(),
            "parent_pid":os.getppid(),**details}
    with (directory/"service-control.jsonl").open("a") as stream:
        fcntl.flock(stream,fcntl.LOCK_EX)
        stream.write(json.dumps(record)+"\n");stream.flush()

@contextmanager
def locked(path,timeout=180):
    path.parent.mkdir(parents=True,exist_ok=True)
    with path.open("a") as stream:
        deadline=time.monotonic()+timeout
        while True:
            try:
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB);break
            except BlockingIOError:
                if time.monotonic()>=deadline:
                    raise RuntimeError("Another service/BOD operation is busy; retry after it completes")
                time.sleep(.1)
        try:yield
        finally:fcntl.flock(stream,fcntl.LOCK_UN)

def run(command,log):
    process=subprocess.Popen([str(x) for x in command],cwd=ROOT,text=True,
                             stdout=subprocess.PIPE,stderr=subprocess.STDOUT)
    for line in process.stdout:log(line.rstrip("\n"))
    result=process.wait()
    if result:raise RuntimeError("Command failed with exit "+str(result)+": "+str(command[0]))

def unit_loaded():
    result=subprocess.run(["systemctl","--user","show",UNIT,"--property=LoadState","--value"],
                          text=True,capture_output=True)
    return result.returncode==0 and result.stdout.strip()=="loaded"

def start(log):
    python=ROOT/"venv/bin/python"
    if not python.exists() or not (ROOT/"data/bod/installed.json").exists() or not unit_loaded():
        raise RuntimeError("Install BOD supervision first: ./venv/bin/python scripts/install_bod.py")
    project.plan(ROOT,list(ports().values()))
    run([python,ROOT/"scripts/bod_manager.py","--start"],log)
    active=subprocess.run(["systemctl","--user","is-active","--quiet",UNIT]).returncode==0
    status_path=ROOT/"data/bod/status.json"
    state=json.loads(status_path.read_text()).get("state") if status_path.exists() else "UNKNOWN"
    if not active:
        if state in {"WAITING_BOD","WEEKEND","PREPARING"}:
            log("Start pending BOD policy: "+state+". Check logs/bod/ and data/bod/status.json.")
            return state
        raise RuntimeError("Stack is not active; BOD state="+str(state))
    deadline=time.monotonic()+40
    while True:
        missing=[(name,port) for name,port in ports().items() if not project.listeners(port)]
        if not missing:break
        if time.monotonic()>=deadline:
            raise RuntimeError("Services not listening: "+", ".join(name+":"+str(port) for name,port in missing))
        time.sleep(.5)
    project.plan(ROOT,list(ports().values()))
    log("All five services are listening. BOD recovery is enabled.")
    for name,port in ports().items():log("  "+name+": "+str(port))
    log("Readiness does not establish fresh exchange quotes.")
    return "RUNNING"

def stop(log):
    bod=ROOT/"data/bod";bod.mkdir(parents=True,exist_ok=True)
    paused=bod/"paused"
    paused.write_text("Operator stop via stop_all.sh at "+now().isoformat()+"\n")
    log("BOD recovery paused. Waiting for any in-progress BOD refresh before stopping.")
    # Stop after an already-running BOD finishes, so its publication cannot
    # race a new service start against this shutdown.
    with locked(bod/"manager.lock"):
        paused.write_text("Operator stop via stop_all.sh at "+now().isoformat()+"\n")
        if unit_loaded():run(["systemctl","--user","stop",UNIT],log)
        targets=project.plan(ROOT,list(ports().values()))
        if targets:
            log("Stopping remaining verified project listeners: "+str(sorted(targets)))
            project.stop(targets)
        if any(project.listeners(port) for port in ports().values()):
            raise RuntimeError("A selected port is still occupied")
        if unit_loaded():
            subprocess.run(["systemctl","--user","reset-failed",UNIT],capture_output=True)
        paused.write_text("Operator stop via stop_all.sh at "+now().isoformat()+"\n")
        status={"checked_at":now().isoformat(),"state":"PAUSED",
                "trade_date":now().date().isoformat(),"reason":"stop_all.sh"}
        temporary=bod/"status.json.control-incoming"
        temporary.write_text(json.dumps(status,indent=2)+"\n")
        os.replace(temporary,bod/"status.json")
    log("All five services stopped; BOD leaves them paused until an explicit start.")
    log("Existing logs and databases were retained.")
    return "STOPPED"

def log_files():
    result={}
    directories=[p for p in (ROOT/"logs").glob("system-[0-9]*") if p.is_dir()]
    folder=max(directories,key=lambda p:p.name,default=None)
    if folder:
        for name in SERVICES:
            path=folder/(name+".log")
            if path.exists():result[name]=path
    bod=ROOT/"logs/bod"
    daily=bod/(now().strftime("%Y%m%d")+".log")
    if not daily.exists():daily=max(bod.glob("*.log"),key=lambda p:p.name,default=daily)
    if daily.exists():result["bod"]=daily
    audit=ROOT/"logs/service-control.jsonl"
    if audit.exists():result["control-audit"]=audit
    commands=list((ROOT/"logs/control").glob("*.log"))
    if commands:result["control-command"]=max(commands,key=lambda p:(p.stat().st_mtime_ns,p.name))
    return result

def tail_bytes(path,lines):
    with path.open("rb") as stream:
        stream.seek(0,os.SEEK_END);end=position=stream.tell();data=b""
        while position and data.count(b"\n")<=lines:
            amount=min(65536,position);position-=amount;stream.seek(position)
            data=stream.read(amount)+data
        return b"".join(data.splitlines(keepends=True)[-lines:]),end

def show_logs(lines,once):
    print("Log root: "+str(ROOT/"logs"),flush=True)
    if once:
        files=log_files()
        for name,path in files.items():
            print("\n=== "+name+": "+str(path)+" ===",flush=True)
            sys.stdout.write(tail_bytes(path,lines)[0].decode("utf-8",errors="replace"));sys.stdout.flush()
        print("\n=== service supervisor journal ===",flush=True)
        subprocess.run(["journalctl","--user","-u",UNIT,"-n",str(lines),"--no-pager",
                        "-o","short-iso"],check=False,env=dict(os.environ,TZ="Asia/Kolkata"))
        if not files:print("No service log files yet.")
        return
    print("Following service logs, BOD and control audit; new launch directories are detected.")
    print("Ctrl+C closes this viewer only; services keep running.",flush=True)
    followers={}
    try:
        while True:
            for name,path in log_files().items():
                try:
                    info=path.stat();previous=followers.get(name)
                    if previous is None or previous[0]!=path or previous[1]!=info.st_ino:
                        data,end=tail_bytes(path,lines)
                        print("\n=== "+name+": "+str(path)+" ===",flush=True)
                        for line in data.decode("utf-8",errors="replace").splitlines():
                            print("["+name+"] "+line,flush=True)
                        followers[name]=(path,info.st_ino,end,b"")
                        continue
                    position=previous[2] if info.st_size>=previous[2] else 0
                    pending=previous[3] if position else b""
                    with path.open("rb") as stream:
                        stream.seek(position);data=stream.read(256*1024);position=stream.tell()
                    chunks=(pending+data).split(b"\n")
                    for line in chunks[:-1]:
                        print("["+name+"] "+line.decode("utf-8",errors="replace"),flush=True)
                    pending=chunks[-1]
                    if len(pending)>1024*1024:
                        print("["+name+"] "+pending.decode("utf-8",errors="replace"),flush=True);pending=b""
                    followers[name]=(path,info.st_ino,position,pending)
                except FileNotFoundError:followers.pop(name,None)
            time.sleep(.5)
    except KeyboardInterrupt:
        print("\nLog viewer stopped. Services were left unchanged.",flush=True)

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action",choices=["start","stop","logs"])
    parser.add_argument("--once",action="store_true",help="Print recent logs and exit")
    parser.add_argument("--lines",type=int,default=40,help="Initial lines per log (default 40)")
    args=parser.parse_args()
    if args.lines<1 or args.lines>10000:parser.error("--lines must be 1..10000")
    if args.action=="logs":
        show_logs(args.lines,args.once);return
    if args.once or args.lines!=40:parser.error("--once and --lines apply only to logs")
    log_dir=ROOT/"logs/control";log_dir.mkdir(parents=True,exist_ok=True)
    logfile=log_dir/(args.action+"-"+now().strftime("%Y%m%d-%H%M%S")+"-"+str(os.getpid())+".log")
    with logfile.open("a") as stream:
        def log(message):
            print(message,flush=True);stream.write(message+"\n");stream.flush()
        log("Control log: "+str(logfile))
        audit_event(args.action,"requested",control_log=str(logfile),source="service-control")
        try:
            with locked(ROOT/"data/bod/control.lock"):
                state=start(log) if args.action=="start" else stop(log)
            audit_event(args.action,"completed",state=state,control_log=str(logfile))
        except Exception as exc:
            log("ERROR: "+str(exc))
            audit_event(args.action,"failed",error=str(exc),control_log=str(logfile))
            raise SystemExit(1)

if __name__=="__main__":main()
