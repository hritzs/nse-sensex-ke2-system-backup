"""Portable immutable BOD snapshots. No shares are assumed or invented."""
from datetime import datetime, timezone, timedelta
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
import uuid
from ke2_feed_server import Master

IST=timezone(timedelta(hours=5,minutes=30))
ALLOWED={"shared/GreekTokens.csv","shared/BSEIndexTokens.csv","shared/cm_bhavcopy/current.csv",
 "shared/bod_bhavcopy/nse-fo/current.csv","shared/bod_bhavcopy/bse-fo/current.csv",
 "data/gamma_inputs/GammaShortDailydata.csv","data/gamma_inputs/EventDates.xlsx"}
ALLOWED.update("live_lut/final_r46V2/"+n for n in
 ("LUT_0916.csv","LUT_0917.csv","LUT_0918.csv","LUT_0919.csv","LUT_0920_Onwards.csv"))

def atomic_json(path,value):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+".tmp")
    tmp.write_text(json.dumps(value,indent=2,allow_nan=False),encoding="utf-8")
    tmp.replace(path)

def source_signature(sources):
    result={}
    for key,value in sources.items():
        if key not in ALLOWED:raise ValueError("Unsupported reference target "+key)
        p=Path(value)
        stat=p.stat()
        result[key]=[str(p),stat.st_size,stat.st_mtime_ns]
    return result

def current(data_dir):
    p=Path(data_dir)/"bod/active.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else None

def day_activity(data_dir,day):
    db=Path(data_dir)/"data/gamma_weekly_simulation.db"
    if not db.exists():return False
    connection=sqlite3.connect(db,timeout=2)
    try:
        tables={r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        for table in ("observations","now_requests"):
            if table in tables and connection.execute("SELECT 1 FROM "+table+" WHERE day=? LIMIT 1",(day,)).fetchone():
                return True
        if "lane_results" in tables:
            for row in connection.execute("SELECT payload FROM lane_results WHERE day=?",(day,)):
                state=json.loads(row[0]).get("status","")
                if state!="READY" and not state.startswith("WAITING"):return True
        return False
    finally:connection.close()

def release_empty_binding(data_dir,day):
    """Called before this app's only Gamma worker opens its private database."""
    db=Path(data_dir)/"data/gamma_weekly_simulation.db"
    if not db.exists() or day_activity(data_dir,day):return False
    connection=sqlite3.connect(db,timeout=5)
    try:
        with connection:
            connection.execute("DELETE FROM settings WHERE key=?",("model:"+day,))
            connection.execute("DELETE FROM lane_results WHERE day=?",(day,))
        return True
    finally:connection.close()

def file_hashes(root):
    result={}
    for relative in sorted(ALLOWED|{"GammaShort_Backtest_Final.py"}):
        path=Path(root)/relative
        if path.is_file():result[relative]=hashlib.sha256(path.read_bytes()).hexdigest()
    return result

def prepare(data_dir,resources,sources,force=False,validate_model=True):
    data_dir=Path(data_dir);resources=Path(resources)
    old=current(data_dir)
    stamp=datetime.now(IST)
    day=stamp.date().isoformat()
    signature=source_signature(sources)
    if old and not force and old["trade_date"]==day and old.get("source_signature")==signature:
        return old,False
    base=Path(old["root"]) if old else resources/"seed"
    target=data_dir/"bod/snapshots"/(stamp.strftime("%Y%m%d-%H%M%S")+"-"+uuid.uuid4().hex[:8])
    target.parent.mkdir(parents=True,exist_ok=True)
    try:
        shutil.copytree(base,target)
        frozen=[]
        locked=day_activity(data_dir,day) or (old and old["trade_date"]==day and stamp.strftime("%H:%M")>="09:15")
        for relative,source in sources.items():
            # Preserve a day's calculation-reference identity on intraday reloads.
            if old and locked and relative.startswith(("data/gamma_inputs/","live_lut/")):
                frozen.append(relative)
                continue
            dst=target/relative
            dst.parent.mkdir(parents=True,exist_ok=True)
            shutil.copy2(source,dst)
        if signature!=source_signature(sources):
            raise RuntimeError("Inputs changed during copy; retaining previous complete snapshot")
        shutil.copy2(resources/"GammaShort_Backtest_Final.py",target/"GammaShort_Backtest_Final.py")
        hashes=file_hashes(target)
        if old and old["trade_date"]==day and old.get("file_hashes")==hashes:
            record={**old,"source_signature":signature,"deferred_reference_updates":frozen}
            atomic_json(data_dir/"bod/active.json",record)
            shutil.rmtree(target)
            return record,False
        master=Master(target)
        symbols={i.symbol for i in master.by_id.values()}
        if not {"NIFTY","SENSEX"}<=symbols:
            raise ValueError("Master must contain both NIFTY and SENSEX")
        if not any(i.expiry>=day for i in master.by_id.values() if i.symbol=="NIFTY"):
            raise ValueError("NIFTY master has no current/future listed contract")
        # Supplied reference files must parse; missing expiry bindings remain diagnostics.
        if validate_model:
            import pandas as pd
            daily=target/"data/gamma_inputs/GammaShortDailydata.csv"
            calendar=target/"data/gamma_inputs/EventDates.xlsx"
            if daily.exists():pd.read_csv(daily,nrows=3)
            if calendar.exists():pd.read_excel(calendar,nrows=3)
            folder=target/"live_lut/final_r46V2"
            if any(folder.glob("LUT_*.csv")):
                from gamma_weekly_engine import load_engine
                engine=load_engine(target/"GammaShort_Backtest_Final.py")
                engine.load_cw_luts(str(folder))
        from standalone.failover import fingerprints
        seed=json.loads((resources/"seed_manifest.json").read_text(encoding="utf-8"))
        record={"trade_date":day,"root":str(target),"completed_at":stamp.isoformat(),
            "source_signature":signature,"file_hashes":hashes,"refresh_enabled":bool(sources),
            "seed_built_at":seed["built_at"],"seed_daily_last_date":seed.get("daily_last_date"),"master_fingerprints":fingerprints(master),
            "deferred_reference_updates":frozen,
            "note":"Configure authoritative input_sources for automatic updates; bundled seed is a dated snapshot."}
        atomic_json(data_dir/"bod/active.json",record)
        return record,True
    except BaseException:
        # Keep failed staging for diagnosis; it never becomes the active snapshot.
        raise
