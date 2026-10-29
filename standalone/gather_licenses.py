"""Collect installed build/runtime dependency notices without executing them."""
from importlib import metadata
import json
from pathlib import Path
import shutil
import sys

ROOT=Path(__file__).resolve().parents[1]
def main():
    out=ROOT/"standalone/assets/licenses";out.mkdir(parents=True,exist_ok=True)
    records=[]
    excluded={"numba","llvmlite","httpx","httpcore"} # tests / compiler not frozen
    for dist in metadata.distributions():
        name=dist.metadata.get("Name","unknown")
        if name.lower() in excluded:continue
        for file in dist.files or ():
            lower=str(file).lower()
            if ".dist-info/" not in lower:continue
            if not any(word in Path(lower).name for word in ("license","copying","notice")):continue
            src=Path(dist.locate_file(file))
            if not src.is_file():continue
            target=out/name/Path(str(file).split(".dist-info/",1)[-1])
            target.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,target)
        records.append({"name":name,"version":dist.version,
            "license":dist.metadata.get("License-Expression") or dist.metadata.get("License","see distribution notices")})
    python_license=Path(sys.base_prefix)/"LICENSE.txt"
    if python_license.exists():shutil.copy2(python_license,out/"PYTHON-LICENSE.txt")
    lzo=ROOT/"data/native_sdk_dependencies/lzo-2.10/COPYING"
    if lzo.exists():shutil.copy2(lzo,out/"LZO-COPYING")
    (out/"DEPENDENCIES.json").write_text(json.dumps(records,indent=2),encoding="utf-8")
    print("Collected notices for",len(records),"distributions")
if __name__=="__main__":main()
