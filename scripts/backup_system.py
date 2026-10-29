#!/usr/bin/env python3
"""Create a verified source/runtime backup without restarting live services."""
import argparse
from contextlib import closing
from datetime import datetime
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import tarfile
import tempfile
import time
from urllib.parse import quote
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
EXCLUDED_DIRS = {".git", ".buildenv", "venv", "venvdash", ".venv", ".venv-ke2-api",
                 "node_modules", "build", "build-ke2", "__pycache__", "logs",
                 ".aws", ".azure", ".ssh", ".codex", ".agents", ".cache"}
SECRET = re.compile(rb"(?:gh[pousr]_[A-Za-z0-9]{30,}|github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|-----BEGIN (?:OPENSSH|RSA|EC) PRIVATE KEY-----)")

def sha(path):
    digest=hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda:stream.read(1024*1024),b""):
            digest.update(block)
    return digest.hexdigest()

def record(path, relative):
    return {"path":relative, "bytes":path.stat().st_size, "sha256":sha(path)}

def run(command, timeout=180):
    return subprocess.check_output(command,stderr=subprocess.STDOUT,text=True,timeout=timeout).strip()

def optional(command):
    try:return run(command,30)
    except (OSError,subprocess.SubprocessError):return "unavailable"

def stable_copy(source,target,private=False):
    before=source.stat()
    target.parent.mkdir(parents=True,exist_ok=True)
    shutil.copy2(source,target)
    after=source.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):
        raise RuntimeError("File changed during backup; retry: "+str(source))
    if private:target.chmod(0o600)

def sqlite_snapshot(source,target):
    target.parent.mkdir(parents=True,exist_ok=True)
    started=time.monotonic()
    def progress(status,remaining,total):
        if time.monotonic()-started>120:
            raise TimeoutError("SQLite backup exceeded 120 seconds: "+str(source))
    with closing(sqlite3.connect("file:"+quote(str(source))+"?mode=ro",uri=True,timeout=5)) as original:
        # Pin a consistent read snapshot. Otherwise frequent external WAL
        # commits can repeatedly restart a large incremental backup.
        original.execute("BEGIN")
        original.execute("SELECT rootpage FROM sqlite_master LIMIT 1").fetchall()
        with closing(sqlite3.connect(target)) as copy:
            original.backup(copy,pages=1024,progress=progress,sleep=.01)
            result=copy.execute("PRAGMA quick_check").fetchall()
            if result!=[("ok",)]:
                raise RuntimeError("Backup SQLite integrity check failed: "+str(source))
    target.chmod(0o600)

def candidates(root):
    for base,dirs,files in os.walk(root,followlinks=False):
        base=Path(base)
        dirs[:]=sorted(d for d in dirs if d not in EXCLUDED_DIRS
                      and not d.startswith("build-")
                      and not (base==root/"backups" and d=="system")
                      and not (base/d).is_symlink())
        for name in sorted(files):
            path=base/name
            rel=path.relative_to(root)
            if path.is_symlink() or ":" in name or name.endswith((".pyc",".pyo",".log",".pid","-wal","-shm")):
                continue
            if name=="core" or name.startswith("core."):continue
            yield path,rel

def private_file(path,rel,header):
    return (rel.parts[0] in ("data","docker-data","user_configs")
            or (rel.parts[0]=="whitegammaApp" and path.name in {"config.json","config.wsl.json","trade_check.txt","marks.json","ui_state.json","box.json"})
            or (rel.parts[0]=="whitegammaApp" and "samples" in rel.parts)
            or (len(rel.parts)>1 and rel.parts[:2]==("backups","whitegamma_standalone_before_changes"))
            or (rel.parts[0]=="standalone" and "assets" in rel.parts and "seed" in rel.parts)
            or path.name in {"screener_snapshot.jsonl",".npmrc",".pypirc",".netrc",".git-credentials","credentials.json"}
            or path.name.startswith(".env")
            or path.suffix.lower() in (".lic",".token",".secret",".pem",".key",".zip",".gz",".tgz",".bundle")
            or header.startswith((b"SQLite format 3",b"\x7fELF",b"MZ")))

def archive(directory,output,prefix):
    with tarfile.open(output,"w:gz",compresslevel=5) as tar:
        for path in sorted(directory.rglob("*")):
            if path.is_file() and not path.is_symlink():
                tar.add(path,arcname=str(Path(prefix)/path.relative_to(directory)),recursive=False)
    output.chmod(0o600)

def verify_members(archive_path,prefix,expected):
    found={}
    with tarfile.open(archive_path,"r:gz") as tar:
        for member in tar:
            path=Path(member.name)
            if path.is_absolute() or ".." in path.parts or not member.isfile():
                raise RuntimeError("Unsafe archive member: "+member.name)
            relative=str(path.relative_to(prefix))
            digest=hashlib.sha256()
            stream=tar.extractfile(member)
            for chunk in iter(lambda:stream.read(1024*1024),b""):digest.update(chunk)
            found[relative]=(member.size,digest.hexdigest())
    wanted={entry["path"]:(entry["bytes"],entry["sha256"]) for entry in expected}
    if found!=wanted:raise RuntimeError("Archive content verification failed: "+str(archive_path))

def native_sources(output):
    path=Path("/opt/uWebSockets")
    info={"path":str(path),"available":path.exists()}
    if not path.exists():return info
    resolved=path.resolve()
    info.update({"resolved_path":str(resolved),
                 "commit":optional(["git","-C",str(resolved),"rev-parse","HEAD"]),
                 "usockets_commit":optional(["git","-C",str(resolved/"uSockets"),"rev-parse","HEAD"])})
    with tarfile.open(output,"w:gz",compresslevel=5) as tar:
        for base,dirs,files in os.walk(resolved,followlinks=False):
            base=Path(base)
            dirs[:]=[d for d in dirs if d not in (".git","node_modules") and not (base/d).is_symlink()]
            for name in files:
                file=base/name
                if name==".git" or file.is_symlink():continue
                tar.add(file,arcname=str(Path("native/uWebSockets")/file.relative_to(resolved)),recursive=False)
    output.chmod(0o600)
    info["archive"]=output.name
    return info

def prepare_git(source,output,source_entries,repo_name,visibility="private"):
    target=output/"github-source"
    shutil.copytree(source,target)
    large=[e["path"] for e in source_entries if e["bytes"]>50*1024*1024]
    with (target/".gitattributes").open("a") as stream:
        stream.write("\n# System backup: large market files use Git LFS\n")
        for name in large:stream.write(json.dumps(name)+" filter=lfs diff=lfs merge=lfs -text\n")
    (target/"BACKUP_SOURCE_MANIFEST.json").write_text(json.dumps(
        {"scope":"Sanitized current source only; private runtime/history excluded",
         "files_before_backup_attributes":source_entries,
         "note":"Backup preparation appends LFS rules to .gitattributes after these baseline hashes."},indent=2)+"\n")
    run(["git","init","-b","main",str(target)])
    run(["git","-C",str(target),"lfs","install","--local"])
    remote="git@github.com:"+repo_name+".git"
    existing=subprocess.run(["git","ls-remote",remote,"refs/heads/main"],
                            capture_output=True,text=True,timeout=30)
    parent=None
    if existing.returncode==0 and existing.stdout.strip():
        run(["git","-C",str(target),"remote","add","origin",remote])
        run(["git","-C",str(target),"fetch","--no-tags","origin","main"])
        parent=run(["git","-C",str(target),"rev-parse","FETCH_HEAD"])
        # This is a new isolated staging repository with an empty index.
        # Setting its initial branch parent preserves published history.
        run(["git","-C",str(target),"update-ref","refs/heads/main",parent])
    elif existing.returncode and "Repository not found" not in existing.stderr:
        raise RuntimeError("Cannot verify the publication parent; offline archives are retained. Retry GitHub preparation when reachable.")
    run(["git","-C",str(target),"add","--all","--force"])
    run(["git","-C",str(target),"commit","-m","System architecture and source backup"])
    commit=run(["git","-C",str(target),"rev-parse","HEAD"])
    # No remote is created or pushed here. Private repository verification is mandatory.
    return {"status":"prepared_not_uploaded","requested_repository":repo_name,
            "required_visibility":visibility,"directory":"github-source",
            "branch":"main","commit":commit,"parent_commit":parent,"lfs_paths":large}

def create(root,prepare,repo_name,visibility="private"):
    root=root.resolve()
    stamp=datetime.now(ZoneInfo("Asia/Kolkata")).strftime("%Y%m%dT%H%M%S%z")
    output=root/"backups"/"system"/(stamp+"-"+str(os.getpid()))
    output.mkdir(parents=True,mode=0o700,exist_ok=False)
    print("BACKUP_DIRECTORY="+str(output),flush=True)
    manifest={"format":1,"created_at":datetime.now(ZoneInfo("Asia/Kolkata")).isoformat(),
              "source_root":str(root),"scope":"Source plus private offline runtime/history; no service restart",
              "git_head":optional(["git","-C",str(root),"rev-parse","HEAD"]),
              "git_branch":optional(["git","-C",str(root),"branch","--show-current"]),
              "source_files":[],"private_runtime_files":[],"sqlite_snapshots":[],
              "omitted":["virtual environments","node_modules","logs","Windows Zone.Identifier",
                         "credential directories outside project","live SQLite WAL/SHM files (database backup API used)"],
              "github":{"status":"not_uploaded","requested_repository":repo_name,"required_visibility":visibility}}
    with tempfile.TemporaryDirectory(prefix=".staging-",dir=output) as temp:
        stage=Path(temp);source=stage/"source";runtime=stage/"runtime"
        source.mkdir();runtime.mkdir()
        visited=set()
        paths=list(candidates(root))
        for name in ("build/nse_decoder","build-ke2/nse_decoder"):
            path=root/name
            if path.is_file():paths.append((path,Path(name)))
        for path,rel in paths:
            if str(rel) in visited:continue
            visited.add(str(rel))
            with path.open("rb") as stream:header=stream.read(64)
            private=private_file(path,rel,header)
            destination=(runtime if private else source)/rel
            if header.startswith(b"SQLite format 3"):
                sqlite_snapshot(path,destination)
                manifest["sqlite_snapshots"].append(str(rel))
            else:
                stable_copy(path,destination,private)
            # Check staged source for recognizable credentials; never print values.
            if not private and destination.stat().st_size<=4*1024*1024:
                if SECRET.search(destination.read_bytes()):
                    private=True
                    moved=runtime/rel;moved.parent.mkdir(parents=True,exist_ok=True)
                    destination.rename(moved);destination=moved;destination.chmod(0o600)
            entry=record(destination,str(rel))
            manifest["private_runtime_files" if private else "source_files"].append(entry)
        print("CAPTURED_SOURCE_FILES="+str(len(manifest["source_files"])),flush=True)
        print("CONSISTENT_SQLITE_SNAPSHOTS="+str(len(manifest["sqlite_snapshots"])),flush=True)
        archive(source,output/"source.tar.gz","source")
        archive(runtime,output/"private-runtime.tar.gz","runtime")
        verify_members(output/"source.tar.gz","source",manifest["source_files"])
        verify_members(output/"private-runtime.tar.gz","runtime",manifest["private_runtime_files"])
        print("SOURCE_AND_RUNTIME_ARCHIVES_VERIFIED",flush=True)
        if (root/".git").exists():
            run(["git","-C",str(root),"bundle","create",str(output/"git-history.bundle"),"--all"])
            run(["git","-C",str(root),"bundle","verify",str(output/"git-history.bundle")])
            (output/"git-history.bundle").chmod(0o600)
            print("ORIGINAL_GIT_HISTORY_BUNDLE_VERIFIED",flush=True)
        manifest["native_sources"]=native_sources(output/"native-dependencies.tar.gz")
        manifest["dependencies"]={
            "compiler":optional(["g++","--version"]),
            "cmake":optional(["cmake","--version"]),
            "feed_python":optional([str(root/"venv/bin/python"),"--version"]),
            "feed_packages":optional([str(root/"venv/bin/python"),"-m","pip","freeze"]),
            "api_packages":optional([str(root/".venv-ke2-api/bin/python"),"-m","pip","freeze"]),
            "decoder_ldd":optional(["ldd",str(root/"build-ke2/nse_decoder")]),
            "indicative_ldd":optional(["ldd",str(root/"indicative_close_server/indicative_close_server")])}
        if prepare:
            manifest["github"]=prepare_git(source,output,manifest["source_files"],repo_name,visibility)
            print("SANITIZED_GITHUB_SOURCE_PREPARED",flush=True)
    artifacts=[]
    for path in sorted(output.iterdir()):
        if path.is_file():artifacts.append(record(path,path.name))
    manifest["artifacts"]=artifacts
    manifest["verification"]="Archive member hashes and SQLite quick_check passed; original Git bundle verified when present."
    (output/"MANIFEST.json").write_text(json.dumps(manifest,indent=2)+"\n")
    checks=artifacts+[record(output/"MANIFEST.json","MANIFEST.json")]
    (output/"SHA256SUMS").write_text("".join(f"{e['sha256']}  {e['path']}\n" for e in checks))
    for entry in checks:
        if sha(output/entry["path"])!=entry["sha256"]:raise RuntimeError("Final checksum mismatch")
    (output/"VERIFIED.txt").write_text("Offline backup verified. GitHub status is in MANIFEST.json; not uploaded without a receipt.\n")
    print("OFFLINE_BACKUP_VERIFIED="+str(output),flush=True)
    print("GITHUB_STATUS="+manifest["github"]["status"],flush=True)
    return output

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root",type=Path,default=ROOT)
    parser.add_argument("--prepare-github",action="store_true")
    parser.add_argument("--repository",default="hritzs/nse-sensex-ke2-system-backup")
    parser.add_argument("--visibility",choices=("private","public"),default="private")
    args=parser.parse_args()
    os.umask(0o077)
    create(args.root,args.prepare_github,args.repository,args.visibility)
