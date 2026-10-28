#!/usr/bin/env python3
"""Validate and atomically install an authoritative NSE GreekTokens export."""
import argparse
import csv
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import shutil
import tempfile
from zoneinfo import ZoneInfo

ROOT=Path(__file__).resolve().parents[1]
def digest(path):
    h=hashlib.sha256()
    with path.open("rb") as f:
        for block in iter(lambda:f.read(1024*1024),b""):h.update(block)
    return h.hexdigest()

def validate(path, today):
    tokens={}
    dates=set()
    excluded=[]
    with path.open(encoding="utf-8-sig",newline="") as stream:
        reader=csv.DictReader(stream)
        if not {"Token","Symbol","Expiry","Strike","OpType"} <= set(reader.fieldnames or []):
            raise ValueError("Required master columns are missing")
        for row in reader:
            if row.get("Exchange","NSE").strip().upper()!="NSE":
                raise ValueError("NSE master contains another exchange")
            side=row["OpType"].strip().upper()
            if side not in ("CE","PE","XX","FUT"):continue
            if not row["Token"].isdigit():
                excluded.append({"symbol":row["Symbol"],"token":row["Token"],"reason":"nondecimal_identifier"})
                continue
            token=int(row["Token"])
            if token<=0:raise ValueError("Invalid token")
            key=(row["Symbol"],row["Expiry"],row["Strike"],side)
            if token in tokens and tokens[token]!=key:raise ValueError("Conflicting token "+str(token))
            tokens[token]=key
            if row["Symbol"]=="NIFTY" and side in ("CE","PE"):
                expiry=None
                for fmt in ("%d-%b-%y","%d-%b-%Y","%Y-%m-%d"):
                    try:expiry=datetime.strptime(row["Expiry"],fmt).date();break
                    except ValueError:pass
                if expiry is None:raise ValueError("Invalid NIFTY expiry")
                if 0 <= (expiry-today).days <= 42:dates.add(expiry.isoformat())
    if len(tokens)<1000 or len(dates)<2:
        raise ValueError("Master lacks adequate NSE/current weekly coverage")
    return dict(instruments=len(tokens),near_term_nifty_expiries=sorted(dates),sha256=digest(path),excluded_rows=excluded)

def install(source, root=ROOT, check=False):
    today=datetime.now(ZoneInfo("Asia/Kolkata")).date()
    before=source.stat()
    result=validate(source,today)
    if source.stat().st_mtime_ns!=before.st_mtime_ns:raise RuntimeError("Source changed; retry")
    result.update(source=str(source.resolve()),checked_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat())
    if check:return result
    target=root/"shared/GreekTokens.csv"
    directory=root/"backups/master_refresh"/datetime.now().strftime("%Y%m%d-%H%M%S")
    directory.mkdir(parents=True,exist_ok=False)
    if target.exists():shutil.copy2(target,directory/"GreekTokens.csv")
    with tempfile.NamedTemporaryFile(prefix=".master-",dir=target.parent,delete=False) as f:temp=Path(f.name)
    try:
        shutil.copy2(source,temp)
        if digest(temp)!=result["sha256"]:raise RuntimeError("Copy checksum mismatch")
        os.replace(temp,target)
    finally:
        if temp.exists():temp.unlink()
    result["previous_master_backup"]=str(directory)
    result["restart_required"]="decoder, KE2 adapter, Gamma and indicative load at startup"
    (root/"shared/NSE_MASTER_PROVENANCE.json").write_text(json.dumps(result,indent=2)+"\n")
    return result

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source",type=Path,required=True)
    parser.add_argument("--check",action="store_true")
    args=parser.parse_args()
    print(json.dumps(install(args.source.resolve(),check=args.check),indent=2))
