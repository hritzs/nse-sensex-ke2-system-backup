#!/usr/bin/env python3
"""Daily validated BOD publication and supervised stack lifecycle."""
import argparse
from contextlib import closing
import csv
from datetime import datetime, time as daytime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import tempfile
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from gamma_weekly_engine import load_engine, ReferenceData
from gamma_weekly_server import configured_environment
from ke2_feed_server import Master
from scripts.refresh_master import install_pair

IST=ZoneInfo("Asia/Kolkata")
UNIT="nse-ke2-stack.service"
BOD=ROOT/"data/bod"

def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+".incoming")
    temp.write_text(json.dumps(value,indent=2)+"\n")
    os.replace(temp,path)

def run(args,**kwargs):
    return subprocess.run(args,check=True,text=True,**kwargs)

def status(state,**details):
    value={"checked_at":datetime.now(IST).isoformat(),"state":state,
           "schedule":"Weekdays 08:45 IST; five-minute retries and health checks",
           **details}
    atomic_json(BOD/"status.json",value)
    print(json.dumps(value),flush=True)
    return value

def service_active():
    return subprocess.run(["systemctl","--user","is-active","--quiet",UNIT]).returncode==0

def reference_env(root,base=None):
    return configured_environment(root,os.environ if base is None else base)

def db_state(path,day):
    if not path.exists():return None,False
    with closing(sqlite3.connect("file:"+str(path)+"?mode=ro",uri=True,timeout=5)) as db:
        tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        if "settings" not in tables:return None,False
        row=db.execute("SELECT value FROM settings WHERE key=?",("model:"+day,)).fetchone()
        observed="observations" in tables and db.execute(
            "SELECT 1 FROM observations WHERE day=? LIMIT 1",(day,)).fetchone()
        requested="now_requests" in tables and db.execute(
            "SELECT 1 FROM now_requests WHERE day=? LIMIT 1",(day,)).fetchone()
        results=[]
        if "lane_results" in tables:
            results=[json.loads(r[0]) for r in db.execute(
                "SELECT payload FROM lane_results WHERE day=?",(day,))]
        safe={"WAITING_QUOTES","WAITING_INPUTS","READY","NO_ENTRY_DATA","WAITING_ENTRY"}
        meaningful=bool(observed or requested or any(r.get("status") not in safe for r in results))
        return row[0] if row else None,meaningful

def clear_empty_binding(path,day,expected):
    # Only pre-entry bookkeeping can be rebound. Never remove observations,
    # NOW requests, or results that could represent an actual trade.
    with closing(sqlite3.connect(path,timeout=10)) as db:
        db.execute("BEGIN IMMEDIATE")
        observed=db.execute("SELECT 1 FROM observations WHERE day=? LIMIT 1",(day,)).fetchone()
        requested=db.execute("SELECT 1 FROM now_requests WHERE day=? LIMIT 1",(day,)).fetchone()
        rows=[json.loads(r[0]) for r in db.execute(
            "SELECT payload FROM lane_results WHERE day=?",(day,))]
        safe={"WAITING_QUOTES","WAITING_INPUTS","READY","NO_ENTRY_DATA","WAITING_ENTRY"}
        row=db.execute("SELECT value FROM settings WHERE key=?",("model:"+day,)).fetchone()
        if observed or requested or any(r.get("status") not in safe for r in rows):
            raise RuntimeError("Current-day activity appeared; refusing model rebind")
        if row and row[0]!=expected:raise RuntimeError("Model binding changed during BOD")
        db.execute("DELETE FROM lane_results WHERE day=?",(day,))
        db.execute("DELETE FROM settings WHERE key=?",("model:"+day,))
        db.commit()

def freeze_existing_references(stage,environment):
    frozen=stage/"frozen_current_day"
    frozen.mkdir()
    for key,name in [("GS_DAILY_CSV","GammaShortDailydata.csv"),
                     ("GS_EVENT_PATH","EventDates.xlsx")]:
        source=Path(environment[key])
        target=frozen/name
        shutil.copy2(source,target);environment[key]=str(target)
    lut=frozen/"lut";shutil.copytree(environment["GS_LUT_DIR"],lut)
    environment["GS_LUT_DIR"]=str(lut)
    return environment

def validate_stage(stage):
    install_pair(stage/"data/reference_import/GreekTokens.csv",
                 stage/"data/reference_import/BSEIndexTokens.csv",root=stage,check=True)
    # Do not trust headers alone: require data rows in each reference export.
    for name in ["shared/cm_bhavcopy/current.csv",
                 "shared/bod_bhavcopy/nse-fo/current.csv",
                 "shared/bod_bhavcopy/bse-fo/current.csv"]:
        with (stage/name).open(encoding="utf-8-sig",newline="") as stream:
            reader=csv.DictReader(stream)
            if not reader.fieldnames or next(reader,None) is None:
                raise ValueError("Empty bhavcopy: "+name)
    refs=ReferenceData(load_engine(),reference_env(stage,{}))
    if refs.events is None or any(table is None for table in refs.tables):
        raise ValueError("BOD calendar/LUT validation failed: "+str(refs.errors))
    return refs

def prepare_and_publish():
    now=datetime.now(IST);day=now.date().isoformat()
    config=json.loads((BOD/"config.json").read_text()) if (BOD/"config.json").exists() else {}
    base=dict(os.environ);base.update(config.get("environment",{}))
    stage=BOD/"staging"/now.strftime("%Y%m%d-%H%M%S")
    stage.mkdir(parents=True,exist_ok=False)
    windows_stage=subprocess.check_output(["wslpath","-w",str(stage)],text=True).strip()
    script=subprocess.check_output(["wslpath","-w",str(ROOT/"scripts/refresh_market_inputs.ps1")],text=True).strip()
    command=["powershell.exe","-NoProfile","-NonInteractive","-ExecutionPolicy","Bypass",
             "-File",script,"-OutputRoot",windows_stage]
    if config.get("share"):command+=["-ShareRoot",config["share"]]
    status("PREPARING",trade_date=day)
    run(command)
    for name in ["GreekTokens.csv","BSEIndexTokens.csv"]:
        modified=datetime.fromtimestamp((stage/"data/reference_import"/name).stat().st_mtime,IST).date()
        if modified != now.date():
            raise ValueError("Authoritative "+name+" is not dated today: "+str(modified))
    fresh_refs=validate_stage(stage)
    db=Path(base.get("GS_SIM_DB",str(ROOT/"data/gamma_weekly_simulation.db")))
    bound,activity=db_state(db,day)
    chosen=reference_env(stage,{})
    deferred=False
    if bound and bound!=fresh_refs.fingerprint and activity:
        active=json.loads((BOD/"active.json").read_text()) if (BOD/"active.json").exists() else {}
        old_base=dict(base);old_base.update(active.get("environment",{}))
        old=reference_env(ROOT,old_base)
        refs=ReferenceData(load_engine(),old)
        if refs.fingerprint!=bound:
            raise RuntimeError("Cannot locate current-day reference version; existing data retained")
        chosen=freeze_existing_references(stage,old)
        deferred=True
    # All input validation completes before touching any live process.
    run(["systemctl","--user","stop",UNIT])
    run([str(ROOT/"venv/bin/python"),str(ROOT/"scripts/restart_project_services.py"),
         "--root",str(ROOT),"--ports","9001","9002","9003","8000","8100"])
    if bound and bound!=fresh_refs.fingerprint and not activity:
        clear_empty_binding(db,day,bound)
    final=BOD/"snapshots"/stage.name
    final.parent.mkdir(parents=True,exist_ok=True);stage.rename(final)
    def moved(value):
        return str(final)+value[len(str(stage)):] if value.startswith(str(stage)+"/") else value
    environment={key:moved(value) for key,value in chosen.items()
                 if key.startswith("GS_") and isinstance(value,str)}
    environment["GS_SIM_DB"]=str(db)
    for key in ["BSE_LOCAL_IP","BSE_EQ_LOCAL_IP","KE2_PUBLIC_HOST","MINUTE_HTTP_PORT"]:
        if key in base:environment[key]=base[key]
    environment["NSE_BHAVCOPY_FILE"]=str(final/"shared/bod_bhavcopy/nse-fo/current.csv")
    environment["BSE_BHAVCOPY_FILE"]=str(final/"shared/bod_bhavcopy/bse-fo/current.csv")
    masters=install_pair(final/"data/reference_import/GreekTokens.csv",
                         final/"data/reference_import/BSEIndexTokens.csv",root=ROOT)
    cm=ROOT/"shared/cm_bhavcopy/current.csv";cm.parent.mkdir(parents=True,exist_ok=True)
    incoming=cm.with_name(".bod-current.csv");shutil.copy2(final/"shared/cm_bhavcopy/current.csv",incoming)
    os.replace(incoming,cm)
    master=Master(ROOT)
    dates=sorted({i.expiry for i in master.by_id.values()
                  if i.exchange=="NSE" and i.symbol=="NIFTY" and i.option_type and i.expiry>=day})
    receipt={"trade_date":day,"completed_at":datetime.now(IST).isoformat(),
             "snapshot":str(final),"environment":environment,"all_nifty_expiries":dates,
             "references_deferred_to_next_bod":deferred,"masters":masters}
    atomic_json(BOD/"active.json",receipt)
    run(["systemctl","--user","start",UNIT])
    # The wrapper establishes all dependencies before announcing readiness.
    import urllib.request
    import time
    ready=False
    for _ in range(60):
        try:
            with urllib.request.urlopen("http://127.0.0.1:"+environment.get("MINUTE_HTTP_PORT","8000")+"/api/health",timeout=2) as response:
                health=json.load(response)
            if health.get("expiries")==dates and health.get("service")=="gamma_weekly_simulator":
                ready=True;break
        except (OSError,ValueError):pass
        time.sleep(.5)
    if not ready:raise RuntimeError("Stack did not expose the expected all-expiry Gamma health")
    atomic_json(BOD/"receipts"/(day+".json"),receipt)
    return status("READY",trade_date=day,snapshot=str(final),expiries=dates,
                  references_deferred_to_next_bod=deferred)

def tick(force=False):
    now=datetime.now(IST);day=now.date().isoformat()
    if (BOD/"paused").exists() and not force:return status("PAUSED",trade_date=day)
    receipt=BOD/"receipts"/(day+".json")
    if receipt.exists() and not force:
        if not service_active():run(["systemctl","--user","start",UNIT])
        saved=json.loads(receipt.read_text())
        return status("READY",trade_date=day,receipt=str(receipt),
                      expiries=saved.get("all_nifty_expiries",[]),
                      references_deferred_to_next_bod=saved.get("references_deferred_to_next_bod",False))
    if now.weekday()>=5 and not force:return status("WEEKEND",trade_date=day)
    if now.time().replace(tzinfo=None)<daytime(8,45) and not force:
        return status("WAITING_BOD",trade_date=day)
    # Retry failed/missed BOD, including late machine startup. Meaningful
    # same-day simulator state forces reference freezing inside publication.
    return prepare_and_publish()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    group=parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--tick",action="store_true")
    group.add_argument("--start",action="store_true")
    group.add_argument("--refresh",action="store_true")
    group.add_argument("--stop",action="store_true")
    group.add_argument("--status",action="store_true")
    args=parser.parse_args()
    BOD.mkdir(parents=True,exist_ok=True)
    if args.start or args.stop or args.refresh:
        from scripts.service_control import audit_event
        try:
            action = "start" if args.start else "stop" if args.stop else "refresh"
            audit_event(action, "requested", source="bod-manager",
                        pause_before=(BOD/"paused").exists())
        except OSError as exc:
            print("Control audit could not be written: "+str(exc),file=sys.stderr)
    if args.status:
        print((BOD/"status.json").read_text() if (BOD/"status.json").exists() else "{}")
        return
    if args.stop:
        (BOD/"paused").write_text("Explicit operator stop\n")
        run(["systemctl","--user","stop",UNIT]);status("PAUSED");return
    if (args.start or args.refresh) and (BOD/"paused").exists():(BOD/"paused").unlink()
    with (BOD/"manager.lock").open("a") as lock:
        try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            print("BOD preparation already running",flush=True);return
        try:
            tick(force=args.refresh)
        except Exception as exc:
            now=datetime.now(IST)
            stopped=False
            if not (BOD/"receipts"/(now.date().isoformat()+".json")).exists() and now.time().replace(tzinfo=None)>=daytime(9,10):
                subprocess.run(["systemctl","--user","stop",UNIT])
                stopped=True
            status("FAILED",error=str(exc),stopped_to_prevent_stale_day=stopped)
            raise

if __name__=="__main__":
    main()
