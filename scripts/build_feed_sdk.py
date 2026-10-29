#!/usr/bin/env python3
"""Reproduce x64 Linux .so / Windows .dll builds from WSL."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import tarfile
import urllib.request
import zipfile

ROOT=Path(__file__).resolve().parents[1]
URL="https://deb.debian.org/debian/pool/main/l/lzo2/lzo2_2.10.orig.tar.gz"
SHA="c0f892943208266f9b6543b3ae308fab6284c5c90e627931446fb49b4221a072"

def run(args,cwd=ROOT):
    print("BUILD",args,flush=True)
    subprocess.run([str(a) for a in args],cwd=cwd,check=True)

def lzo_source():
    deps=ROOT/"data/native_sdk_dependencies";deps.mkdir(parents=True,exist_ok=True)
    archive=deps/"lzo-2.10.tar.gz"
    if not archive.exists():
        temp=archive.with_suffix(".incoming")
        with urllib.request.urlopen(URL,timeout=60) as response:temp.write_bytes(response.read())
        if hashlib.sha256(temp.read_bytes()).hexdigest()!=SHA:
            raise RuntimeError("LZO source checksum mismatch")
        temp.replace(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=SHA:
        raise RuntimeError("LZO source checksum mismatch")
    source=deps/"lzo-2.10"
    if not source.exists():
        with tarfile.open(archive) as tar:tar.extractall(deps,filter="data")
    return source

def build(platform):
    if platform=="linux":
        folder=ROOT/"build-sdk-linux"
        run(["cmake","-S",ROOT/"sdk","-B",folder,"-DCMAKE_BUILD_TYPE=Release"])
    else:
        if not shutil.which("x86_64-w64-mingw32-g++"):
            raise SystemExit("Install g++-mingw-w64-x86-64-posix first")
        folder=ROOT/"build-sdk-windows/library"
        source=lzo_source()
        lzo_build=ROOT/"build-sdk-windows/lzo-build";lzo_build.mkdir(parents=True,exist_ok=True)
        prefix=ROOT/"build-sdk-windows/lzo-install"
        if not (prefix/"lib/liblzo2.a").exists():
            run([source/"configure","--build="+subprocess.check_output(["gcc","-dumpmachine"],text=True).strip(),
                 "--host=x86_64-w64-mingw32","--disable-shared",
                 "--enable-static","--prefix="+str(prefix)],lzo_build)
            run(["make","-j2"],lzo_build);run(["make","install"],lzo_build)
        run(["cmake","-S",ROOT/"sdk","-B",folder,"-DCMAKE_BUILD_TYPE=Release",
             "-DCMAKE_TOOLCHAIN_FILE="+str(ROOT/"sdk/cmake/mingw-x64.cmake"),
             "-DLZO_INCLUDE_DIR="+str(prefix/"include"),
             "-DLZO_LIBRARY="+str(prefix/"lib/liblzo2.a")])
    run(["cmake","--build",folder,"-j2"])
    if platform=="linux":run(["ctest","--test-dir",folder,"--output-on-failure"])
    package=ROOT/"dist/feed-sdk"/(platform+"-x64");package.mkdir(parents=True,exist_ok=True)
    names=["ke2_feed.dll","libke2_feed.dll.a","ke2_feed_smoke.exe"] if platform=="windows" else [
        "libke2_feed.so.1.0.0","ke2_feed_smoke"]
    for name in names:shutil.copy2(folder/name,package/name)
    if platform=="linux":
        # Preserve SONAME without depending on archive/symlink support on Windows.
        shutil.copy2(folder/"libke2_feed.so.1.0.0",package/"libke2_feed.so.1")
        shutil.copy2(folder/"libke2_feed.so.1.0.0",package/"libke2_feed.so")
    shutil.copy2(ROOT/"sdk/include/ke2_feed.h",package/"ke2_feed.h")
    shutil.copy2(ROOT/"sdk/ke2_feed.def",package/"ke2_feed.def")
    shutil.copy2(ROOT/"sdk/examples/feed_client.py",package/"feed_client.py")
    shutil.copy2(ROOT/"sdk/README.md",package/"README.md")
    source=lzo_source()
    shutil.copy2(source/"COPYING",package/"COPYING.LZO")
    manifest={"platform":platform,"architecture":"x86_64","abi_version":1,
              "lzo_source":URL,"lzo_source_sha256":SHA,"files":{}}
    for path in package.iterdir():
        if path.is_file() and path.name!="MANIFEST.json":
            manifest["files"][path.name]=hashlib.sha256(path.read_bytes()).hexdigest()
    (package/"MANIFEST.json").write_text(json.dumps(manifest,indent=2)+"\n")
    archive=shutil.make_archive(str(package.parent/("ke2-feed-sdk-"+platform+"-x64")),"zip",root_dir=package.parent,base_dir=package.name)
    print("SDK_ARCHIVE="+archive,flush=True)
    print("SDK_PACKAGE="+str(package),flush=True)


def source_archive():
    destination=ROOT/"dist/feed-sdk/ke2-feed-sdk-source.zip"
    paths=list((ROOT/"sdk").rglob("*"))+list((ROOT/"include").rglob("*.h"))
    paths += [ROOT/"src"/name for name in ["decoder.cpp","parser_7208.cpp","market_feed.cpp"]]
    paths += [ROOT/"scripts/build_feed_sdk.py",ROOT/"data/native_sdk_dependencies/lzo-2.10.tar.gz"]
    with zipfile.ZipFile(destination,"w",zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(set(paths)):
            if path.is_file() and "__pycache__" not in path.parts and not path.name.endswith(".pyc"):
                archive.write(path,str(Path("ke2-feed-sdk-source")/path.relative_to(ROOT)))
    print("SDK_SOURCE_ARCHIVE="+str(destination),flush=True)

if __name__=="__main__":
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--platform",choices=["linux","windows","all"],default="all")
    args=parser.parse_args()
    for target in (["linux","windows"] if args.platform=="all" else [args.platform]):build(target)
    source_archive()
