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

def install_pair(source, bse_source, root=ROOT, check=False):
    # Validate a stable pair before replacing either live master. Consumers
    # retain their loaded maps until the launcher performs a coordinated restart.
    import sys
    sys.path.insert(0, str(ROOT))
    from ke2_feed_server import Master
    root = Path(root).resolve()
    target_dir = root / "shared"
    target_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date()
    sources = {"GreekTokens.csv": Path(source), "BSEIndexTokens.csv": Path(bse_source)}
    with tempfile.TemporaryDirectory(prefix=".validated-masters-", dir=target_dir) as tmp:
        stage = Path(tmp)
        (stage / "shared").mkdir()
        for name, original in sources.items():
            before = original.stat()
            shutil.copy2(original, stage / "shared" / name)
            after = original.stat()
            if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
                raise RuntimeError("Master changed during copy: " + name)
        result = validate(stage / "shared/GreekTokens.csv", today)
        master = Master(stage)
        bse = [i for i in master.by_id.values() if i.exchange == "BSE"]
        future_dates = sorted({i.expiry for i in bse if i.symbol == "SENSEX"
                               and i.option_type is None
                               and datetime.fromisoformat(i.expiry).date() >= today})
        if len(bse) < 500 or not future_dates:
            raise ValueError("BSE master lacks adequate contracts or an unexpired SENSEX future")
        result.update(source=str(Path(source).resolve()),
                      checked_at=datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
                      bse={"source": str(Path(bse_source).resolve()),
                           "instruments": len(bse), "sensex_future_expiries": future_dates,
                           "sha256": digest(stage / "shared/BSEIndexTokens.csv")})
        if check:
            return result
        expected = {"GreekTokens.csv": result["sha256"],
                    "BSEIndexTokens.csv": result["bse"]["sha256"]}
        changed = [name for name in sources
                   if not (target_dir/name).exists() or digest(target_dir/name) != expected[name]]
        result["changed_files"] = changed
        if changed:
            stamp = datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y%m%d-%H%M%S-%f")
            backup = root / "backups/master_refresh" / stamp
            backup.mkdir(parents=True, exist_ok=False)
            for name in changed:
                if (target_dir/name).exists():
                    shutil.copy2(target_dir/name, backup/name)
            installed = []
            try:
                for name in changed:
                    os.replace(stage/"shared"/name, target_dir/name)
                    installed.append(name)
            except OSError:
                # A failed second replace must not leave mismatched live files.
                for name in installed:
                    previous = backup/name
                    if previous.exists():
                        shutil.copy2(previous, stage/name)
                        os.replace(stage/name, target_dir/name)
                    else:
                        (target_dir/name).unlink()
                raise
            result["previous_master_backup"] = str(backup)
        result["restart_required"] = bool(changed)
        result["consumers"] = ["decoder", "KE2 adapter", "Gamma simulator", "indicative"]
        (target_dir/"NSE_MASTER_PROVENANCE.json").write_text(json.dumps(result,indent=2)+"\n")
        return result

def install(source, root=ROOT, check=False, bse_source=None):
    if bse_source is not None:
        return install_pair(source, bse_source, root, check)
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
    parser.add_argument("--bse-source",type=Path)
    args=parser.parse_args()
    print(json.dumps(install(args.source.resolve(),check=args.check, bse_source=args.bse_source),indent=2))
