"""Owns only processes started by this standalone application."""
from datetime import datetime
import json
import logging
import os
from pathlib import Path
import socket
import subprocess
import sys
import threading
import time
import uuid
from standalone.profiles import PROFILES,owned_ports
from standalone.inputs_refresh import atomic_json, prepare, current, IST

LOG=logging.getLogger(__name__)
WORKERS=("feed","whitegamma","gamma","tester")
class Supervisor:
    def __init__(self,data_dir,resources,settings):
        self.data=Path(data_dir);self.resources=Path(resources);self.settings=settings
        self.profile=settings.get("profile","all")
        self.services=PROFILES[self.profile]["services"]
        self.children={};self.handles=[];self.errors={}
        self.lock=threading.RLock();self.exit=threading.Event()
        self.restart=threading.Event();self.refresh=threading.Event()
        self.running=False;self.config=None;self.attempts={};self.job=None
        self.log_dir=self.data/"logs"/datetime.now(IST).strftime("%Y%m%d-%H%M%S")
        self.log_dir.mkdir(parents=True,exist_ok=True)
        self.thread=None

    def command(self,name,config):
        if getattr(sys,"frozen",False):
            return [sys.executable,"--worker",name,"--runtime-config",str(config)]
        return [sys.executable,str(Path(__file__).parent/"launcher.py"),
                "--worker",name,"--runtime-config",str(config)]

    def spawn(self,name):
        config=self.config
        env=os.environ.copy()
        env.update(PYTHONUNBUFFERED="1",NUMBA_CACHE_DIR=str(self.data/"cache/numba"),
            INDICATIVE_HTTP_HOST="127.0.0.1",INDICATIVE_HTTP_PORT=str(config["ports"]["indicative"]),
            FEED_LOCAL_IP=self.settings["interface"])
        command=self.command(name,config["config_path"])
        if name=="indicative":
            binary=self.resources/"native"/("indicative_close.exe" if os.name=="nt" else "indicative_close")
            if not binary.exists():raise FileNotFoundError("Native indicative binary missing: "+str(binary))
            command=[str(binary)]
        flags=subprocess.CREATE_NO_WINDOW if os.name=="nt" else 0
        self.children[name]=subprocess.Popen(command,cwd=config["root"],env=env,
            stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,creationflags=flags)
        child=self.children[name]
        try:self.job.assign(child)
        except Exception:
            child.terminate();child.wait(timeout=3)
            raise
        def capture():
            from standalone.logging_utils import handler
            target=self.log_dir/(name+".log" if name=="indicative" else name+"-bootstrap.log")
            sink=handler(target)
            try:
                for line in iter(child.stdout.readline,b""):
                    record=logging.LogRecord(name,logging.INFO,"",0,line.decode("utf-8",errors="replace").rstrip(),(),None)
                    sink.emit(record)
            finally:child.stdout.close();sink.close()
        threading.Thread(target=capture,daemon=True).start()
        LOG.info("Started owned %s PID %s",name,self.children[name].pid)

    def start(self):
        with self.lock:
            if self.running:return
            ports=self.settings["ports"]
            for name,port in owned_ports(self.profile,ports).items():
                if name=="control":continue
                with socket.socket() as sock:
                    sock.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,0 if os.name=="nt" else 1)
                    try:sock.bind(("127.0.0.1",port))
                    except OSError as exc:raise RuntimeError("Port %s (%s) occupied; existing process left untouched"%(port,name)) from exc
            self.errors.clear()
            try:active,_=prepare(self.data,self.resources,self.settings.get("input_sources",{}),validate_model=self.profile!="feed")
            except Exception as exc:
                LOG.exception("Configured inputs unavailable; retaining validated snapshot")
                self.errors["bod"]=str(exc)
                active=current(self.data)
                if active is None:active,_=prepare(self.data,self.resources,{},validate_model=self.profile!="feed")
            run_dir=self.data/"run"/uuid.uuid4().hex
            run_dir.mkdir(parents=True)
            app_dir=self.data/"whitegamma";app_dir.mkdir(exist_ok=True)
            cfg_path=app_dir/"config.json"
            if not cfg_path.exists():
                cfg=json.loads((self.resources/"whitegamma.default.json").read_text(encoding="utf-8"))
            else:cfg=json.loads(cfg_path.read_text(encoding="utf-8"))
            cfg["feed_url"]=("ws://127.0.0.1:%s/"%ports["routed"] if "feed" in self.services else self.settings["feed_url"])
            cfg["http_host"]="127.0.0.1";cfg["http_port"]=ports["whitegamma"]
            cfg["span"]["folder"]=str(app_dir/"span")
            cfg["files"]["tokens"]=[str(Path(active["root"])/"shared"/n)
                for n in ("GreekTokens.csv","BSEIndexTokens.csv")]
            # Defaults use authoritative BOD bhavcopies; user trade paths stay explicit.
            if not cfg["files"].get("bhavcopy"):
                cfg["files"]["bhavcopy"]=[str(Path(active["root"])/"shared/bod_bhavcopy"/ex/"current.csv")
                    for ex in ("nse-fo","bse-fo")]
            atomic_json(cfg_path,cfg)
            config={**self.settings,"root":active["root"],"data_dir":str(self.data),
                "resources":str(self.resources),"whitegamma_config":str(cfg_path),
                "stop_file":str(run_dir/"stop"),"status_file":str(self.data/"feed-status.json"),
                "library":str(self.resources/"native"/("ke2_feed.dll" if os.name=="nt" else "libke2_feed.so")),
                "publish_port":ports["publish"],"raw_port":ports["raw"],
                "routed_port":ports["routed"],"routed_raw_port":ports["routed_raw"],
                "log_dir":str(self.log_dir),"config_path":str(run_dir/"runtime.json")}
            atomic_json(config["config_path"],config);self.config=config
            (self.data/"data").mkdir(exist_ok=True)
            from standalone.owned_job import OwnedJob
            self.job=OwnedJob()
            self.running=True
            for name in self.services:
                try:self.spawn(name)
                except Exception as exc:self.errors[name]=str(exc);LOG.exception("Failed to start %s",name)

    def stop(self):
        with self.lock:
            if not self.running:return
            Path(self.config["stop_file"]).touch()
            deadline=time.monotonic()+12
            for name,process in list(self.children.items()):
                if name=="indicative" and process.poll() is None:process.terminate()
            for name,process in list(self.children.items()):
                if process.poll() is None:
                    try:process.wait(timeout=max(.1,deadline-time.monotonic()))
                    except subprocess.TimeoutExpired:
                        LOG.warning("Terminating unresponsive owned %s PID %s",name,process.pid)
                        process.terminate()
                        try:process.wait(timeout=3)
                        except subprocess.TimeoutExpired:process.kill();process.wait()
            if self.job:self.job.close();self.job=None
            self.children.clear()
            for handle in self.handles:handle.close()
            self.handles.clear();self.running=False

    def status(self):
        with self.lock:
            feed=None
            try:feed=json.loads((self.data/"feed-status.json").read_text(encoding="utf-8"))
            except (OSError,ValueError):pass
            if feed and time.time()*1000-feed.get("updated_at_ms",0)>5000:
                feed={**feed,"selected":{},"status":"STALE_STATUS_FILE"}
            return {"running":self.running,"data_dir":str(self.data),"logs":str(self.log_dir),
                "profile":self.profile,"owned_services":list(self.services),"feed_url":self.settings.get("feed_url"),"source_url":self.settings.get("source_url"),
                "ports":self.settings["ports"],"services":{n:{"pid":p.pid,"exit_code":p.poll()}
                    for n,p in self.children.items()},"errors":dict(self.errors),
                "feed":feed,"bod":current(self.data)}

    def loop(self):
        try:
            try:self.start()
            except Exception as exc:self.errors["startup"]=str(exc);LOG.exception("Standalone startup failed")
            next_check=0
            while not self.exit.wait(1):
                if self.restart.is_set():
                    self.restart.clear()
                    try:self.stop();self.start()
                    except Exception as exc:self.errors["startup"]=str(exc);LOG.exception("Restart failed")
                if time.monotonic()>=next_check or self.refresh.is_set():
                    force=self.refresh.is_set();self.refresh.clear();next_check=time.monotonic()+60
                    try:
                        active=current(self.data)
                        now=datetime.now(IST)
                        due=now.strftime("%H:%M")>=self.settings.get("bod_time_ist","08:45")
                        if force or (due and self.settings.get("input_sources")):
                            _,changed=prepare(self.data,self.resources,self.settings.get("input_sources",{}),force,validate_model=self.profile!="feed")
                            if changed and self.running:self.stop();self.start()
                        self.errors.pop("bod",None)
                    except Exception as exc:self.errors["bod"]=str(exc);LOG.exception("BOD refresh retained previous inputs")
                if self.running:
                    for name,p in list(self.children.items()):
                        if p.poll() is not None:
                            self.errors[name]="Service exited %s; see %s.log"%(p.returncode,name)
                            # Avoid a tight crash loop; surface errors for missing DLL/runtime/network.
                            if time.monotonic()-self.attempts.get(name,0)>30:
                                self.attempts[name]=time.monotonic()
                                try:self.spawn(name)
                                except Exception as exc:self.errors[name]=str(exc)
        except Exception as exc:self.errors["supervisor"]=str(exc);LOG.exception("Supervisor stopped")
        finally:self.stop()
