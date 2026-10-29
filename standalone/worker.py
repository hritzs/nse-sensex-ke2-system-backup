"""Owned service entry points for source and frozen builds."""
import asyncio
import json
import os
from pathlib import Path
import sys
import threading
import time

def watch(stop_file,callback):
    def run():
        while not Path(stop_file).exists():time.sleep(.25)
        callback()
    threading.Thread(target=run,daemon=True).start()

def run_uvicorn(app,port,stop_file):
    import uvicorn
    server=uvicorn.Server(uvicorn.Config(app,host="127.0.0.1",port=port,log_level="info"))
    watch(stop_file,lambda:setattr(server,"should_exit",True))
    server.run()

async def until_stopped(coroutine,stop_file):
    task=asyncio.create_task(coroutine)
    try:
        while not Path(stop_file).exists():
            if task.done():return await task
            await asyncio.sleep(.25)
    finally:
        task.cancel()
        await asyncio.gather(task,return_exceptions=True)

def main(name,config_path):
    c=json.loads(Path(config_path).read_text(encoding="utf-8"))
    root=Path(c["root"])
    stop=c["stop_file"]
    ports=c["ports"]
    if name=="feed":
        from standalone.feed_runtime import Runtime
        asyncio.run(until_stopped(Runtime(c).run(),stop))
    elif name=="whitegamma":
        sys.path.insert(0,str(Path(c["resources"])/"whitegamma"))
        import server
        server.BASE=Path(c["data_dir"])/"whitegamma"
        server.STATIC=Path(c["resources"])/"whitegamma/static"
        app,_=server.build(c["whitegamma_config"])
        run_uvicorn(app,ports["whitegamma"],stop)
    elif name=="tester":
        from ke2_test_api import create_app
        run_uvicorn(create_app("ws://127.0.0.1:%s/"%ports["routed"]),ports["tester"],stop)
    elif name=="gamma":
        import gamma_weekly_server as gamma
        env=gamma.configured_environment(root,os.environ)
        env.update(GS_SIM_DB=str(Path(c["data_dir"])/"data/gamma_weekly_simulation.db"),
            GS_SOURCE_WS=(c["source_url"] if c.get("profile","all") in ("gamma","simulator") else "ws://127.0.0.1:%s/ke2-source"%ports["routed_raw"]),
            GS_WEEKLY_HORIZON_DAYS="366")
        master=gamma.Master(root)
        from datetime import datetime
        today=datetime.now(gamma.IST).date()
        # Explicit listed expiries include the longer-dated contracts too.
        env["GS_WEEKLY_EXPIRIES"]=",".join(sorted({i.expiry for i in master.by_id.values()
            if i.exchange=="NSE" and i.symbol=="NIFTY" and i.option_type
            and i.expiry>=today.isoformat()}))
        from standalone.inputs_refresh import release_empty_binding
        release_empty_binding(c["data_dir"],today.isoformat())
        sim=gamma.Simulator(root=root,environ=env)
        http=gamma.ThreadingHTTPServer(("127.0.0.1",ports["gamma"]),gamma.make_handler(sim))
        threading.Thread(target=http.serve_forever,daemon=True).start()
        async def run():
            async with asyncio.TaskGroup() as tasks:
                from standalone.gamma_source import source_loop
                tasks.create_task(source_loop(sim))
                tasks.create_task(sim.clock_loop())
                tasks.create_task(sim.evaluation_loop())
        try:asyncio.run(until_stopped(run(),stop))
        finally:
            http.shutdown();http.server_close();sim.store.db.close()
    else:raise ValueError("Unknown worker "+name)
