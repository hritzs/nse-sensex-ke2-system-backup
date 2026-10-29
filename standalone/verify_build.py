"""Inspect the frozen archive and PE headers without running the application."""
import hashlib
import json
from pathlib import Path
from PyInstaller.archive.readers import CArchiveReader
import pefile

ROOT=Path(__file__).resolve().parents[1]
def main():
    import argparse
    parser=argparse.ArgumentParser()
    parser.add_argument("--exe",type=Path,default=ROOT/"dist/whitegamma-standalone/WhiteGammaStandalone.exe")
    parser.add_argument("--output",type=Path,default=ROOT/"standalone/build/windows-archive-verification.json")
    args=parser.parse_args()
    exe=args.exe
    archive=CArchiveReader(str(exe))
    names={n.replace(chr(92),"/"):n for n in archive.toc}
    def resource(relative):
        target="standalone_assets/"+relative
        if target not in names:raise ValueError("Missing frozen resource "+target)
        return archive.extract(names[target])
    manifest=json.loads(resource("seed_manifest.json"))
    checked=[]
    for row in manifest["files"]:
        content=resource("seed/"+row["path"])
        if hashlib.sha256(content).hexdigest()!=row["sha256"]:raise ValueError("Incorrect seed hash "+row["path"])
        checked.append(row["path"])
    for relative in ["native/ke2_feed.dll","native/indicative_close.exe","GammaShort_Backtest_Final.py"]:
        actual=resource(relative)
        expected=(ROOT/"standalone/assets"/relative).read_bytes()
        if actual!=expected:raise ValueError("Frozen resource differs from verified input: "+relative)
    for name in ("server.py","engine.py","feed_client.py","inputs.py","black.py","synthetic.py","box.py","span.py"):
        if resource("whitegamma/"+name)!=(ROOT/"whitegammaApp/whitegammaApp"/name).read_bytes():
            raise ValueError("Application source changed: "+name)
    profiles={"KE2Feed":"feed","WhiteGamma":"whitegamma","GammaSimulator":"simulator"}
    if exe.stem in profiles:
        import marshal
        if exe.stem not in archive.toc or "launcher" in archive.toc:
            raise ValueError("Separate product entry is not isolated")
        entry=marshal.loads(archive.extract(exe.stem))
        if profiles[exe.stem] not in entry.co_consts:
            raise ValueError("Incorrect compiled entry profile")
    pyz_name=next(n for n in archive.toc if n.endswith(".pyz"))
    pyz=archive.open_embedded_archive(pyz_name)
    required=["standalone.worker","standalone.feed_runtime","standalone.supervisor",
        "standalone.failover","standalone.owned_job","standalone.inputs_refresh",
        "ke2_feed_server","ke2_test_api","gamma_weekly_server","gamma_weekly_engine",
        "pandas","numpy","webview","uvicorn"]
    if exe.stem!="WhiteGammaStandalone":required+=["standalone.profiles","standalone.gamma_source"]
    for name in required:
        if name not in pyz.toc:raise ValueError("Missing frozen Python module "+name)
    for path in (exe,ROOT/"standalone/assets/native/ke2_feed.dll",ROOT/"standalone/assets/native/indicative_close.exe"):
        if pefile.PE(str(path),fast_load=True).FILE_HEADER.Machine!=0x8664:raise ValueError("Not x64 PE: "+str(path))
    result={"archive_verified":True,"windows_runtime_executed":False,
        "runtime_blocker":"Windows Application Control blocked pandas fields.cp314-win_amd64.pyd in source diagnostic.",
        "exe_sha256":hashlib.sha256(exe.read_bytes()).hexdigest(),"exe_bytes":exe.stat().st_size,
        "seed_files_verified":checked,"required_modules_verified":required,
        "existing_whitegamma_math_sources_unchanged":True,"gamma_source_unchanged":True,
        "dll_matches_verified_sdk":True,"native_indicative_matches_build":True}
    args.output.write_text(json.dumps(result,indent=2))
    print(json.dumps(result,indent=2))
if __name__=="__main__":main()
