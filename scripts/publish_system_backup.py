#!/usr/bin/env python3
"""Publish a prepared, sanitized backup to the requested new repository with explicitly selected visibility."""
import argparse
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import urllib.error
import urllib.request

def run(args):
    return subprocess.check_output(args, text=True, stderr=subprocess.STDOUT).strip()

def api(method, path, data=None):
    token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")
    if token:
        request = urllib.request.Request("https://api.github.com/"+path,
            data=None if data is None else json.dumps(data).encode(), method=method,
            headers={"Authorization":"Bearer "+token,"Accept":"application/vnd.github+json",
                     "Content-Type":"application/json","User-Agent":"nse-ke2-backup"})
        try:
            with urllib.request.urlopen(request, timeout=30) as response:
                return json.load(response)
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return None
            raise RuntimeError("GitHub API returned HTTP "+str(exc.code)) from None
    if shutil.which("gh"):
        cmd=["gh","api","--method",method,path]
        if data is not None:
            for key,value in data.items():
                cmd += ["-F", key+"="+("true" if value is True else "false" if value is False else str(value))]
        try:
            return json.loads(run(cmd))
        except subprocess.CalledProcessError as exc:
            if "HTTP 404" in exc.output:
                return None
            raise RuntimeError("GitHub CLI authentication/API request failed; run gh auth login") from None
    raise RuntimeError("Repository creation requires GitHub API authentication: connect GitHub, use gh auth login, or set GH_TOKEN locally. Do not paste tokens into chat.")

def publish(output, visibility=None):
    manifest=json.loads((output/"MANIFEST.json").read_text())
    prepared=manifest["github"]
    repository=prepared["requested_repository"]
    visibility=visibility or prepared.get("required_visibility","private")
    if visibility not in ("private","public"):raise ValueError("Invalid visibility")
    is_private=visibility=="private"
    if not re.fullmatch(r"hritzs/[A-Za-z0-9_.-]+",repository):
        raise ValueError("Unexpected GitHub destination")
    if repository == "hritzs/nse-fo-low-latency-decoder":
        raise ValueError("Original repository must not be used for this backup")
    root=output/prepared["directory"]
    if run(["git","-C",str(root),"rev-parse","HEAD"]) != prepared["commit"]:
        raise ValueError("Prepared Git commit changed")
    if run(["git","-C",str(root),"status","--porcelain"]):
        raise ValueError("Prepared source has uncommitted changes")
    user=api("GET","user")
    if not user or user.get("login","").lower() != "hritzs":
        raise ValueError("GitHub authentication must belong to hritzs")
    repo=api("GET","repos/"+repository)
    if repo is None:
        repo=api("POST","user/repos",{"name":repository.split("/")[1],"private":is_private,
                                    "description":"NSE, SENSEX, KE2, Gamma simulator and indicative system backup"})
    if not repo or repo.get("private") != is_private or repo.get("full_name","").lower()!=repository.lower():
        raise ValueError("Destination does not match the requested visibility/repository")
    expected="git@github.com:"+repository+".git"
    remotes=run(["git","-C",str(root),"remote"]).splitlines()
    if "origin" in remotes:
        if run(["git","-C",str(root),"remote","get-url","origin"]) != expected:
            raise ValueError("Unexpected staging remote")
    else:
        run(["git","-C",str(root),"remote","add","origin",expected])
    # No force push and no original working repository mutation.
    run(["git","-C",str(root),"push","-u","origin","main"])
    remote=run(["git","-C",str(root),"ls-remote","origin","refs/heads/main"]).split()[0]
    if remote != prepared["commit"]:
        raise RuntimeError("Remote commit verification failed")
    verified=api("GET","repos/"+repository)
    if verified.get("private") != is_private:
        raise RuntimeError("Post-upload visibility verification failed")
    receipt={"uploaded_at":datetime.now(timezone.utc).isoformat(),"repository":repository,
             "url":verified["html_url"],"private":is_private,"branch":"main","commit":remote,
             "scope":"Sanitized source and documentation; private runtime/history remain offline"}
    path=output/"GITHUB_UPLOAD_RECEIPT.json"
    path.write_text(json.dumps(receipt,indent=2)+"\n")
    (output/"GITHUB_UPLOAD_RECEIPT.sha256").write_text(hashlib.sha256(path.read_bytes()).hexdigest()+"  "+path.name+"\n")
    print(json.dumps(receipt,indent=2))

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("backup_directory",type=Path)
    parser.add_argument("--visibility",choices=("private","public"))
    args=parser.parse_args()
    try:
        publish(args.backup_directory.resolve(),args.visibility)
    except (OSError,ValueError,RuntimeError,subprocess.SubprocessError) as exc:
        raise SystemExit(str(exc))
