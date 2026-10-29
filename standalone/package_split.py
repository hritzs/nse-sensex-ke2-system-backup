"""Package verified split EXEs for public release, excluding portfolio state."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile
ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"dist/windows-split"
VERSION="0.2.0-rc1"
PRODUCTS=("KE2Feed","WhiteGamma","GammaSimulator")
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def main():
    records=[]
    for name in PRODUCTS:
        exe=OUT/(name+".exe")
        verified=json.loads((ROOT/"standalone/build"/(name+"-verified.json")).read_text())
        if not verified["archive_verified"] or sha(exe)!=verified["exe_sha256"]:raise RuntimeError("Unverified EXE "+name)
        records.append({"name":exe.name,"bytes":exe.stat().st_size,"sha256":sha(exe),
            "windows_execution_verified":False})
    shutil.copy2(ROOT/"SEPARATE_WINDOWS_APPS.md",OUT/"README.md")
    shutil.copy2(ROOT/"standalone/assets/native/ke2_feed.dll",OUT/"ke2_feed.dll")
    with zipfile.ZipFile(OUT/"THIRD_PARTY_LICENSES.zip","w",zipfile.ZIP_DEFLATED) as archive:
        for path in sorted((ROOT/"standalone/assets/licenses").rglob("*")):
            if path.is_file():archive.write(path,str(path.relative_to(ROOT/"standalone/assets/licenses")))
    checks=json.loads((ROOT/"standalone/build/split-linux-acceptance.json").read_text())
    if not checks["ok"]:raise RuntimeError("Split acceptance has not passed")
    manifest={"version":VERSION,"created_at":datetime.now(timezone.utc).isoformat(),
        "status":"PRERELEASE_WINDOWS_ACCEPTANCE_PENDING","products":records,
        "linux_process_acceptance":checks,
        "windows_blocker":"Build machine Application Control previously blocked pandas native model import; full frozen Windows GUI/multicast has not been validated.",
        "private_portfolio_state_included":False,"dated_reference_seed_included":True,
        "native_sdk_abi":1}
    (OUT/"BUILD_INFO.json").write_text(json.dumps(manifest,indent=2))
    (OUT/"RELEASE_NOTES.md").write_text("""Separate Windows x64 applications, version 0.2.0-rc1.

- KE2Feed.exe: native NSE/BSE decoder, KE2 feed, independent indicative server and Swagger tester.
- WhiteGamma.exe: WhiteGamma interface only, using a separately running feed.
- GammaSimulator.exe: independent all-expiry Gamma simulator.
- Separate data folders, service ownership and control ports; stopping Gamma leaves feed running.
- Routed raw endpoint /ke2-source, source metadata verification and stale-quote rejection.
- Existing numerical WhiteGamma and backtest sources are retained.

Start KE2Feed.exe, WhiteGamma.exe and GammaSimulator.exe. Each owns different services.
See README.md for LAN configuration.

This is a prerelease candidate. Linux split-process integration and deterministic
transport checks passed. Archive/resource checks passed for all three Windows
EXEs. Full frozen Windows GUI, live multicast and real multi-PC acceptance is
pending; Application Control blocked a required pandas native module during
earlier Windows model testing. No Windows policy is bypassed or disabled.

No portfolios, positions, trades, marks or historical databases are included.
Dated authoritative master/reference seeds are bundled; configure daily BOD
sources for fresh data. Indicative's independent NSE CM stream remains local.
The earlier NSE packet-layout discrepancy is not fixed by this packaging.
""")
    names=[p+".exe" for p in PRODUCTS]+["ke2_feed.dll","README.md","BUILD_INFO.json","THIRD_PARTY_LICENSES.zip"]
    (OUT/"SHA256SUMS.txt").write_text("\n".join(sha(OUT/n)+"  "+n for n in names)+"\n")
    archive=OUT/"SeparateWindowsApps-0.2.0-rc1.zip"
    with zipfile.ZipFile(archive,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as zip:
        for n in names+["SHA256SUMS.txt","RELEASE_NOTES.md"]:zip.write(OUT/n,n)
    with zipfile.ZipFile(archive) as zip:
        if zip.testzip():raise RuntimeError("Release ZIP verification failed")
    backup=ROOT/"backups/standalone/split-0.2.0-rc1"
    backup.mkdir(parents=True,exist_ok=True)
    for n in names+["SHA256SUMS.txt","RELEASE_NOTES.md",archive.name]:
        shutil.copy2(OUT/n,backup/n)
        if sha(OUT/n)!=sha(backup/n):raise RuntimeError("Offline backup mismatch")
    print(json.dumps({"version":VERSION,"products":records,"offline_backup":str(backup),"zip_bytes":archive.stat().st_size},indent=2))
if __name__=="__main__":main()
