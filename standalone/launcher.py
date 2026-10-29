"""WhiteGamma desktop launcher and owned multi-service runtime."""
from pathlib import Path
import sys
if not getattr(sys,"frozen",False):sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import argparse
import asyncio
import json
import logging
import multiprocessing
import os
import signal
import socket
import threading
import time
import uuid
from urllib.parse import urlparse

def resources():
    if getattr(sys,"frozen",False):return Path(sys._MEIPASS)/"standalone_assets"
    return Path(__file__).parent/"assets"

def validate(value):
    import ipaddress
    from standalone.failover import Selector
    ports=value["ports"]
    required={"raw","publish","tester","routed","routed_raw","gamma","indicative","whitegamma","control"}
    if set(ports)!=required or any(type(p) is not int or not 1024<=p<=65535 for p in ports.values()):
        raise ValueError("All nine named ports must be integers between 1024 and 65535")
    if len(set(ports.values()))!=len(ports):raise ValueError("Ports must be different")
    ipaddress.ip_address(value["interface"]);ipaddress.ip_address(value["publish_host"])
    for key in ("feed_url","source_url"):
        if key in value:
            url=urlparse(value[key])
            if url.scheme not in ("ws","wss") or not url.hostname or url.username or url.password:
                raise ValueError(key+" must be a WebSocket URL without credentials")
    peers=value.get("peers",[])
    if not isinstance(peers,list) or len(peers)>8:raise ValueError("Configure at most eight peers")
    names=["local"]
    for peer in peers:
        name=peer["name"];url=urlparse(peer["raw_url"])
        if not isinstance(name,str) or not name or len(name)>64:raise ValueError("Invalid peer name")
        if url.scheme not in ("ws","wss") or not url.hostname or url.username or url.password:
            raise ValueError("Peer raw_url must be a WebSocket URL without credentials")
        names.append(name)
    Selector(names,float(value.get("stale_seconds",10)),float(value.get("recovery_seconds",15)))
    from datetime import datetime
    datetime.strptime(value.get("bod_time_ist","08:45"),"%H:%M")
    from standalone.inputs_refresh import ALLOWED
    if not isinstance(value.get("input_sources",{}),dict) or set(value.get("input_sources",{}))-ALLOWED:
        raise ValueError("Unsupported input_sources target")
    return value

def acquire_lock(folder):
    lock=(folder/"instance.lock").open("a+b")
    lock.seek(0);lock.write(b"0");lock.flush();lock.seek(0)
    try:
        if os.name=="nt":
            import msvcrt
            msvcrt.locking(lock.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
    except OSError as exc:raise RuntimeError("This data folder already has a running WhiteGamma instance") from exc
    return lock

def main(default_profile=None):
    from standalone.profiles import PROFILES,executable_profile
    multiprocessing.freeze_support()
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--self-test",metavar="RESULT_JSON",help="Offline DLL, model and master diagnostic")
    p.add_argument("--profile",choices=list(PROFILES),default=default_profile or executable_profile(sys.executable))
    p.add_argument("--feed-url",help="Normalized KE2 URL for separate application profiles")
    p.add_argument("--source-url",help="Routed raw source URL for the separate Gamma simulator")
    p.add_argument("--worker",choices=["feed","whitegamma","gamma","tester"])
    p.add_argument("--runtime-config")
    p.add_argument("--data-dir")
    p.add_argument("--headless",action="store_true")
    p.add_argument("--seconds",type=float)
    p.add_argument("--port-offset",type=int,default=0,help="Use isolated ports for testing")
    args=p.parse_args()
    profile=PROFILES[args.profile]
    if args.self_test:
        from standalone.diagnostics import run
        run(resources(),args.self_test)
        return
    if args.worker:
        config=json.loads(Path(args.runtime_config).read_text(encoding="utf-8"))
        log=Path(config["log_dir"])/(args.worker+".log")
        from standalone.logging_utils import handler,LogStream
        logging.basicConfig(level=logging.INFO,handlers=[handler(log)],force=True)
        sys.stdout=LogStream(args.worker+".stdout");sys.stderr=LogStream(args.worker+".stderr")
        from standalone.worker import main as worker
        return worker(args.worker,args.runtime_config)
    from standalone.inputs_refresh import atomic_json
    from standalone.supervisor import Supervisor
    data=Path(args.data_dir or os.environ.get("WHITEGAMMA_DATA_DIR") or
        (Path(os.environ.get("LOCALAPPDATA",Path.home()/".local/share"))/profile["folder"])).resolve()
    data.mkdir(parents=True,exist_ok=True)
    instance=acquire_lock(data)
    (data/"logs").mkdir(exist_ok=True)
    from standalone.logging_utils import handler
    logging.basicConfig(level=logging.INFO,handlers=[handler(data/"logs/launcher.log")],force=True)
    root=resources()
    setting_path=data/"settings.json"
    if setting_path.exists():settings=json.loads(setting_path.read_text(encoding="utf-8"))
    else:
        settings=json.loads((root/"settings.default.json").read_text(encoding="utf-8"))
        settings["node_id"]=uuid.uuid4().hex
        settings["ports"]["control"]=profile["control"]
        settings["feed_url"]="ws://127.0.0.1:19002/"
        settings["source_url"]="ws://127.0.0.1:19002/ke2-source"
        atomic_json(setting_path,settings)
    settings["profile"]=args.profile
    if args.feed_url:settings["feed_url"]=args.feed_url
    if args.source_url:settings["source_url"]=args.source_url
    validate(settings)
    if args.port_offset:
        settings={**settings,"ports":{n:p+args.port_offset for n,p in settings["ports"].items()}}
        validate(settings)
    supervisor=Supervisor(data,root,settings)
    from fastapi import FastAPI,HTTPException
    from fastapi.responses import HTMLResponse
    app=FastAPI(title="WhiteGamma standalone control",docs_url="/control-docs")
    @app.get("/",response_class=HTMLResponse)
    async def index():return (root/"control.html").read_text(encoding="utf-8")
    @app.get("/api/status")
    async def status():return supervisor.status()
    @app.get("/api/settings")
    async def get_settings():return supervisor.settings
    @app.post("/api/settings")
    async def save_settings(body:dict):
        try:
            body["node_id"]=supervisor.settings["node_id"]
            body["profile"]=args.profile
            validate(body)
            if body["ports"]["control"]!=supervisor.settings["ports"]["control"]:
                raise ValueError("Changing control port requires closing the app and editing settings.json")
            atomic_json(setting_path,body)
            supervisor.settings=body
            supervisor.restart.set()
            return {"saved":True,"restarting_owned_services":True}
        except (ValueError,KeyError,TypeError) as exc:raise HTTPException(422,str(exc))
    @app.post("/api/refresh")
    async def refresh():supervisor.refresh.set();return {"scheduled":True}
    @app.post("/api/restart")
    async def restart():supervisor.restart.set();return {"scheduled":True}
    @app.post("/api/shutdown")
    async def shutdown():supervisor.exit.set();return {"stopping":True}
    import uvicorn
    control_socket=socket.socket()
    if os.name!="nt":control_socket.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
    control_socket.bind(("127.0.0.1",settings["ports"]["control"]))
    control_socket.listen(128)
    control=uvicorn.Server(uvicorn.Config(app,log_level="info"))
    control_thread=threading.Thread(target=lambda:control.run(sockets=[control_socket]),daemon=True)
    control_thread.start()
    supervisor.thread=threading.Thread(target=supervisor.loop,name="owned-services",daemon=True)
    supervisor.thread.start()
    url="http://127.0.0.1:%s/"%settings["ports"]["control"]
    logging.info("Standalone control %s; data %s",url,data)
    def shutdown_signal(*unused):supervisor.exit.set()
    signal.signal(signal.SIGINT,shutdown_signal)
    timer=None
    if args.seconds:
        timer=threading.Timer(args.seconds,supervisor.exit.set)
        timer.daemon=True
        timer.start()
    try:
        if not args.headless:
            try:
                import webview
                window=webview.create_window(profile["title"],url,width=1600,height=1000)
                def close_when_stopped():
                    supervisor.exit.wait()
                    try:window.destroy()
                    except Exception:pass
                threading.Thread(target=close_when_stopped,daemon=True).start()
                webview.start(gui="edgechromium")
                supervisor.exit.set()
            except Exception:
                logging.exception("Desktop browser unavailable; opening system browser")
                import webbrowser
                webbrowser.open(url)
                supervisor.exit.wait()
        else:supervisor.exit.wait()
    finally:
        supervisor.exit.set()
        if timer:timer.cancel()
        supervisor.thread.join(timeout=20)
        control.should_exit=True
        control_thread.join(timeout=5)
        control_socket.close()
        instance.close()

def entrypoint(default_profile=None):
    try:main(default_profile)
    except Exception as exc:
        logging.exception("Standalone failed")
        if os.name=="nt" and "--worker" not in sys.argv and "--headless" not in sys.argv and "--self-test" not in sys.argv:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None,str(exc),"WhiteGamma startup error",16)
        raise

if __name__=="__main__":entrypoint()
