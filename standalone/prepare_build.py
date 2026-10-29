"""Create an allowlisted, portfolio-free resource tree for redistribution."""
from datetime import datetime,timezone
import hashlib
import csv
import json
from pathlib import Path
import shutil
ROOT=Path(__file__).resolve().parents[1]
ASSETS=ROOT/"standalone/assets"
def main():
    seed=ASSETS/"seed"
    seed.mkdir(exist_ok=True)
    files=[
        "shared/GreekTokens.csv","shared/BSEIndexTokens.csv",
        "shared/cm_bhavcopy/current.csv",
        "shared/bod_bhavcopy/nse-fo/current.csv","shared/bod_bhavcopy/bse-fo/current.csv",
        "data/gamma_inputs/GammaShortDailydata.csv","data/gamma_inputs/EventDates.xlsx"]
    files += ["live_lut/final_r46V2/"+n for n in
        ("LUT_0916.csv","LUT_0917.csv","LUT_0918.csv","LUT_0919.csv","LUT_0920_Onwards.csv")]
    rows=[]
    for name in files:
        src=ROOT/name
        active=ROOT/"data/bod/active.json"
        if active.exists() and name.startswith(("data/gamma_inputs/","live_lut/")):
            latest=Path(json.loads(active.read_text())["snapshot"])/name
            if latest.is_file():src=latest
        if not src.is_file() and name.startswith("shared/"):
            active=ROOT/"data/bod/active.json"
            if active.exists():
                snapshot=Path(json.loads(active.read_text())["snapshot"])
                if (snapshot/name).is_file():src=snapshot/name
        if not src.is_file():
            if name in files[:2]:raise FileNotFoundError(src)
            continue
        dst=seed/name;dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
        rows.append({"path":name,"bytes":src.stat().st_size,
            "sha256":hashlib.sha256(src.read_bytes()).hexdigest(),
            "source_modified_utc":datetime.fromtimestamp(src.stat().st_mtime,timezone.utc).isoformat()})
    source=ROOT/"whitegammaApp/whitegammaApp"
    app=ASSETS/"whitegamma";app.mkdir(exist_ok=True)
    for name in ("server.py","engine.py","feed_client.py","inputs.py","black.py","synthetic.py","box.py","span.py"):
        shutil.copy2(source/name,app/name)
    shutil.copytree(source/"static",app/"static",dirs_exist_ok=True,
        ignore=shutil.ignore_patterns("*:Zone.Identifier"))
    shutil.copy2(ROOT/"GammaShort_Backtest_Final.py",ASSETS/"GammaShort_Backtest_Final.py")
    native=ASSETS/"native";native.mkdir(exist_ok=True)
    shutil.copy2(ROOT/"dist/feed-sdk/windows-x64/ke2_feed.dll",native/"ke2_feed.dll")
    shutil.copy2(ROOT/"dist/feed-sdk/linux-x64/libke2_feed.so",native/"libke2_feed.so")
    shutil.copy2(ROOT/"standalone/native/indicative_close.exe",native/"indicative_close.exe")
    linux=ROOT/"standalone/native/indicative_close"
    if linux.is_file():shutil.copy2(linux,native/"indicative_close")
    daily_last_date=None
    daily=seed/"data/gamma_inputs/GammaShortDailydata.csv"
    if daily.exists():
        with daily.open(encoding="utf-8-sig",newline="") as stream:
            daily_last_date=max((r.get("Date","") for r in csv.DictReader(stream)),default=None)
    manifest={"daily_last_date":daily_last_date,"built_at":datetime.now(timezone.utc).isoformat(),"files":rows,
              "excluded":"No positions, trade inputs, marks, private configuration, history databases or logs."}
    (ASSETS/"seed_manifest.json").write_text(json.dumps(manifest,indent=2))
    print(json.dumps({"files":len(rows),"seed_bytes":sum(r["bytes"] for r in rows)},indent=2))
if __name__=="__main__":main()
