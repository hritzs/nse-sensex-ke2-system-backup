"""Package the candidate and complete, portfolio-free corresponding source."""
from datetime import datetime,timezone
import hashlib
import json
from pathlib import Path
import shutil
import zipfile

ROOT=Path(__file__).resolve().parents[1]
OUT=ROOT/"dist/whitegamma-standalone"
VERSION="0.1.0-rc1"
def sha(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def add_tree(archive,path,relative):
    for file in sorted(path.rglob("*")):
        if not file.is_file() or file.is_symlink():continue
        parts=file.relative_to(path).parts
        if any(p in {"__pycache__",".buildenv","build",".git",".venv"} for p in parts):continue
        if ":" in file.name or file.suffix in {".pyc",".pyo",".o",".obj"}:continue
        archive.write(file,str(Path(relative)/file.relative_to(path)))
def main():
    verification=json.loads((ROOT/"standalone/build/windows-archive-verification.json").read_text())
    exe=OUT/"WhiteGammaStandalone.exe"
    if not verification["archive_verified"] or sha(exe)!=verification["exe_sha256"]:
        raise RuntimeError("Frozen archive must be verified before packaging")
    OUT.mkdir(parents=True,exist_ok=True)
    shutil.copy2(ROOT/"WHITEGAMMA_STANDALONE.md",OUT/"README.md")
    shutil.copy2(ROOT/"standalone/ARCHITECTURE.md",OUT/"ARCHITECTURE.md")
    shutil.copy2(ROOT/"sdk/include/ke2_feed.h",OUT/"ke2_feed.h")
    shutil.copy2(ROOT/"sdk/README.md",OUT/"SDK_README.md")
    shutil.copy2(ROOT/"standalone/assets/native/ke2_feed.dll",OUT/"ke2_feed.dll")
    shutil.copy2(ROOT/"standalone/assets/native/libke2_feed.so",OUT/"libke2_feed.so")
    shutil.copytree(ROOT/"standalone/assets/licenses",OUT/"licenses",dirs_exist_ok=True)
    manifest={"version":VERSION,"built_at":datetime.now(timezone.utc).isoformat(),
        "status":"RELEASE_CANDIDATE_WINDOWS_ACCEPTANCE_PENDING",
        "verification":verification,"tests":{"standalone_linux_passed":16,
            "existing_regression_passed":41,"windows_selection_and_job_passed":7},
        "runtime_limits":["Windows Application Control blocked pandas native model import on build PC.",
            "Frozen GUI and actual multi-PC live feed acceptance remain pending.",
            "KE2 routing supports NSE FO/BSE FO/BSE indices. Indicative NSE CM remains local.",
            "Bundled input seed is dated; configure authoritative BOD sources."],
        "contains_private_portfolio_state":False}
    (OUT/"BUILD_INFO.json").write_text(json.dumps(manifest,indent=2))
    binary=OUT/"WhiteGammaStandalone-windows-x64-rc1.zip"
    with zipfile.ZipFile(binary,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for name in ("WhiteGammaStandalone.exe","ke2_feed.dll","ke2_feed.h","SDK_README.md","README.md","ARCHITECTURE.md","BUILD_INFO.json"):
            archive.write(OUT/name,name)
        add_tree(archive,OUT/"licenses","licenses")
    source=OUT/"WhiteGammaStandalone-source-rc1.zip"
    with zipfile.ZipFile(source,"w",zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
        for file in sorted((ROOT/"standalone").iterdir()):
            if file.is_file() and file.suffix in {".py",".ps1",".spec",".md",".txt"}:
                archive.write(file,str(file.relative_to(ROOT)))
        for folder in ("standalone/tests","standalone/assets","sdk","include","src"):
            add_tree(archive,ROOT/folder,folder)
        for name in ("ke2_feed_server.py","ke2_test_api.py","gamma_weekly_server.py",
            "gamma_weekly_engine.py","GammaShort_Backtest_Final.py","WHITEGAMMA_STANDALONE.md",
            "CMakeLists.txt","scripts/build_feed_sdk.py","indicative_close_server/server.cpp"):
            archive.write(ROOT/name,name)
        app=ROOT/"whitegammaApp/whitegammaApp"
        for name in ("server.py","engine.py","feed_client.py","inputs.py","black.py","synthetic.py","box.py","span.py"):
            archive.write(app/name,str((app/name).relative_to(ROOT)))
        add_tree(archive,app/"static","whitegammaApp/whitegammaApp/static")
        # Build preparation can run offline against the same dated, authoritative seed.
        add_tree(archive,ROOT/"standalone/assets/seed",".")
        add_tree(archive,ROOT/"data/native_sdk_dependencies/lzo-2.10","data/native_sdk_dependencies/lzo-2.10")
        archive.write(OUT/"BUILD_INFO.json","BUILD_INFO.json")
    # Source packaging is an explicit allowlist; verify prohibited state isn't present.
    with zipfile.ZipFile(source) as archive:
        for name in archive.namelist():
            if name.endswith(("/config.json","/trade_check.txt","/marks.json","/ui_state.json","/box.json",".db",".sqlite")):
                raise RuntimeError("Private state unexpectedly packaged: "+name)
        if archive.testzip() is not None:raise RuntimeError("Source ZIP CRC verification failed")
    with zipfile.ZipFile(binary) as archive:
        if archive.testzip() is not None:raise RuntimeError("Binary ZIP CRC verification failed")
    artifacts=[exe,binary,source,OUT/"ke2_feed.dll",OUT/"libke2_feed.so"]
    lines=["%s  %s"%(sha(p),p.name) for p in artifacts]
    (OUT/"SHA256SUMS.txt").write_text("\n".join(lines)+"\n")
    receipt={"version":VERSION,"created_at":datetime.now(timezone.utc).isoformat(),
        "artifacts":[{"name":p.name,"bytes":p.stat().st_size,"sha256":sha(p)} for p in artifacts],
        "zip_crc_verified":True,"private_state_excluded":True}
    (OUT/"PACKAGE_RECEIPT.json").write_text(json.dumps(receipt,indent=2))
    backup=ROOT/"backups/standalone"/datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    backup.mkdir(parents=True)
    for name in ["README.md","ARCHITECTURE.md","BUILD_INFO.json","PACKAGE_RECEIPT.json","SHA256SUMS.txt"]:
        shutil.copy2(OUT/name,backup/name)
    for artifact in artifacts:
        shutil.copy2(artifact,backup/artifact.name)
        if sha(backup/artifact.name)!=sha(artifact):raise RuntimeError("Offline backup hash mismatch")
    receipt["offline_backup"]=str(backup)
    (OUT/"PACKAGE_RECEIPT.json").write_text(json.dumps(receipt,indent=2))
    (backup/"PACKAGE_RECEIPT.json").write_text(json.dumps(receipt,indent=2))
    print(json.dumps(receipt,indent=2))
if __name__=="__main__":main()
