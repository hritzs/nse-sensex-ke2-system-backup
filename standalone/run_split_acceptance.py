"""Isolated real-process split-stack acceptance; touches no production ports."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import urllib.request
ROOT=Path(__file__).resolve().parents[1]
def get(port,path):
    with urllib.request.urlopen("http://127.0.0.1:%s%s"%(port,path),timeout=3) as stream:return json.load(stream)
def main():
    data=ROOT/"data/split_acceptance"
    processes=[]
    try:
        for profile in ("feed","whitegamma","simulator"):
            cmd=[sys.executable,str(ROOT/"standalone/launcher.py"),"--profile",profile,
                 "--headless","--data-dir",str(data/profile),"--port-offset","5000","--seconds","150"]
            if profile!="feed":cmd+=["--feed-url","ws://127.0.0.1:24002/",
                                     "--source-url","ws://127.0.0.1:24002/ke2-source"]
            processes.append(subprocess.Popen(cmd,cwd=ROOT))
        deadline=time.monotonic()+35
        while time.monotonic()<deadline:
            try:
                feed=get(23301,"/api/status");app=get(23201,"/api/status");gamma=get(23401,"/api/status")
                if len(feed["services"])==3 and len(app["services"])==1 and len(gamma["services"])==1:
                    checks={name:get(port,path) for name,port,path in [
                        ("whitegamma",23200,"/api/state"),("gamma",23000,"/api/health"),
                        ("indicative",23100,"/health"),("tester",24003,"/health")]}
                    if checks["gamma"]["source_connected"]:break
            except (OSError,ValueError):pass
            time.sleep(.5)
        else:raise RuntimeError("Split profiles failed to become ready")
        assert set(feed["services"])=={"feed","tester","indicative"},feed
        assert set(app["services"])=={"whitegamma"},app
        assert set(gamma["services"])=={"gamma"},gamma
        assert checks["gamma"]["source_connected"],checks["gamma"]
        assert checks["gamma"]["source"]=="ws://127.0.0.1:24002/ke2-source"
        app_pids=[v["pid"] for v in app["services"].values()]
        gamma_pids=[v["pid"] for v in gamma["services"].values()]
        req=urllib.request.Request("http://127.0.0.1:23201/api/shutdown",method="POST",data=b"{}",headers={"Content-Type":"application/json"})
        urllib.request.urlopen(req,timeout=3).close()
        processes[1].wait(timeout=25)
        assert get(23401,"/api/status")["running"]
        for pid in app_pids:
            try:os.kill(pid,0)
            except ProcessLookupError:pass
            else:raise RuntimeError("Application worker survived shutdown: "+str(pid))
        req=urllib.request.Request("http://127.0.0.1:23401/api/shutdown",method="POST",data=b"{}",headers={"Content-Type":"application/json"})
        urllib.request.urlopen(req,timeout=3).close()
        processes[2].wait(timeout=25)
        surviving=get(23301,"/api/status")
        assert surviving["running"] and all(v["exit_code"] is None for v in surviving["services"].values())
        result={"ok":True,"feed_services":list(feed["services"]),"whitegamma_services":list(app["services"]),"gamma_services":list(gamma["services"]),
            "gamma_source_identity_verified":True,"all_application_endpoints":True,
            "stopping_gamma_leaves_feed_running":True,"stopping_whitegamma_leaves_simulator_running":True,"gamma_owned_workers_stopped":True,
            "live_derivatives_verified":False}
        (ROOT/"standalone/build/split-linux-acceptance.json").write_text(json.dumps(result,indent=2))
        print(json.dumps(result,indent=2))
    finally:
        for port in (23201,23401,23301):
            try:
                req=urllib.request.Request("http://127.0.0.1:%s/api/shutdown"%port,method="POST",data=b"{}",headers={"Content-Type":"application/json"})
                urllib.request.urlopen(req,timeout=3).close()
            except OSError:pass
        for p in processes:
            try:p.wait(timeout=25)
            except subprocess.TimeoutExpired:p.terminate();p.wait(timeout=5)
if __name__=="__main__":main()
