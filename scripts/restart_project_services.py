#!/usr/bin/env python3
"""Stop only positively identified listeners belonging to this project."""
import argparse
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import time


def identity(pid):
    proc = Path("/proc") / str(pid)
    try:
        args = [x.decode() for x in (proc / "cmdline").read_bytes().split(b"\0") if x]
        cwd = (proc / "cwd").resolve(strict=True)
        # comm may contain spaces or parentheses; starttime is field 22.
        start = (proc / "stat").read_text().rsplit(")", 1)[1].split()[19]
        exe_text = os.readlink(proc / "exe")
        # A rebuilt executable remains mapped as "(deleted)" until restart.
        exe = Path(exe_text.removesuffix(" (deleted)"))
        return dict(pid=pid, args=args, cwd=str(cwd), exe=str(exe), start=start)
    except (OSError, ValueError):
        return None


def belongs(info, root):
    if not info or not info["args"]:
        return False
    root = root.resolve()
    executable = Path(info["exe"])
    binaries = {root / p for p in ("build/nse_decoder", "build-ke2/nse_decoder",
                                   "indicative_close_server/indicative_close_server")}
    if executable in {p.resolve() for p in binaries}:
        return True
    args = info["args"]
    if not executable.name.startswith(("python", "uvicorn")):
        return False
    cwd = Path(info["cwd"])
    scripts = {"ke2_feed_server.py", "simulation_server_2.py", "gamma_weekly_server.py"}
    for arg in args[1:]:
        if arg.startswith("-"):
            continue
        path = Path(arg)
        if not path.is_absolute():
            path = cwd / path
        if path.name in scripts and path.resolve() == root / path.name:
            return True
    return cwd == root and (
        any(args[i:i+2] == ["-m", "uvicorn"] for i in range(len(args)-1))
        or executable.name.startswith("uvicorn")) and "ke2_test_api:app" in args


def listeners(port):
    result = subprocess.run(["ss", "-H", "-ltnp", "sport = :" + str(port)],
                            text=True, capture_output=True, check=True)
    if not result.stdout.strip():
        return []
    pids = sorted({int(x) for x in re.findall(r"pid=(\d+)", result.stdout)})
    if not pids:
        raise RuntimeError(f"Cannot identify listener on {port}; no services stopped")
    return pids


def plan(root, ports):
    result = {}
    # Validate every occupied port before stopping anything.
    for port in ports:
        for pid in listeners(port):
            info = identity(pid)
            if not belongs(info, root):
                raise RuntimeError(f"Port {port} belongs to an unknown process PID {pid}; no services stopped")
            result[pid] = info
    return result


def alive(info):
    current = identity(info["pid"])
    return current is not None and current["start"] == info["start"]


def stop(targets):
    for sig, seconds in ((signal.SIGINT, 8), (signal.SIGTERM, 3), (signal.SIGKILL, 2)):
        for info in targets.values():
            if alive(info):
                os.kill(info["pid"], sig)
        end = time.monotonic() + seconds
        while time.monotonic() < end and any(alive(i) for i in targets.values()):
            time.sleep(.1)
        if not any(alive(i) for i in targets.values()):
            return
    raise RuntimeError("Some project listeners could not be stopped")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--ports", nargs="+", type=int, required=True)
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    targets = plan(args.root, args.ports)
    print(json.dumps({"action": "check" if args.check else "restart_stop",
                      "project_pids": sorted(targets), "ports": args.ports}), flush=True)
    if not args.check:
        stop(targets)
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline and any(listeners(p) for p in args.ports):
            time.sleep(.1)
        if any(listeners(p) for p in args.ports):
            raise RuntimeError("A selected port is still occupied; startup cancelled")


if __name__ == "__main__":
    main()
