#!/usr/bin/env python3
"""Print the endpoints for services selected by the stack launcher."""
import argparse
import os
import subprocess

API_ROUTES = [
    ("GET", "/health"), ("GET", "/capabilities"), ("GET", "/instruments"),
    ("GET", "/indices"), ("GET", "/bod/status"),
    ("GET", "/sessions"), ("POST", "/sessions"),
    ("GET", "/sessions/{session_id}"), ("DELETE", "/sessions/{session_id}"),
    ("POST", "/sessions/{session_id}/subscribe"),
    ("POST", "/sessions/{session_id}/snapshot"),
    ("POST", "/sessions/{session_id}/unsubscribe"),
    ("POST", "/sessions/{session_id}/reconnect"),
    ("POST", "/sessions/{session_id}/request"),
    ("GET", "/sessions/{session_id}/quotes"),
    ("GET", "/sessions/{session_id}/events"),
    ("GET", "/sessions/{session_id}/stream"),
    ("GET", "/docs"), ("GET", "/redoc"), ("GET", "/openapi.json"),
]

def address():
    configured = os.environ.get("KE2_PUBLIC_HOST")
    if configured:
        return configured
    try:
        values = subprocess.check_output(["hostname", "-I"], text=True).split()
        return next((v for v in values if ":" not in v and not v.startswith("127.")),
                    "<server-LAN-IP>")
    except (OSError, subprocess.SubprocessError):
        return "<server-LAN-IP>"

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--sim-port", type=int, default=8000)
    p.add_argument("--ke2-host", default="127.0.0.1")
    p.add_argument("--sim-host", default="127.0.0.1")
    p.add_argument("--api-host", default="127.0.0.1")
    p.add_argument("--simulator", default="gamma_weekly_server.py")
    p.add_argument("--no-simulator", action="store_true")
    p.add_argument("--no-indicative", action="store_true")
    p.add_argument("--no-api", action="store_true")
    a = p.parse_args(argv)
    lan = address()
    print("\nService endpoints (local):")
    print("  Decoder legacy WebSocket: ws://127.0.0.1:9001/")
    print("  Internal raw source:      ws://127.0.0.1:9001/ke2-source")
    print("  KE2 feed WebSocket:       ws://127.0.0.1:9002/")
    if not a.no_simulator:
        print(f"  Simulator dashboard:      http://127.0.0.1:{a.sim_port}/")
    if not a.no_indicative:
        print("  Indicative dashboard:     http://127.0.0.1:8100/")
    if not a.no_api:
        print("  FastAPI Swagger:          http://127.0.0.1:9003/docs")
    print(f"\nLAN address: {lan} (override detection with KE2_PUBLIC_HOST)")
    if a.ke2_host == "0.0.0.0":
        print(f"  KE2 feed:                 ws://{lan}:9002/")
    if not a.no_simulator and a.sim_host == "0.0.0.0":
        print(f"  Simulator:                http://{lan}:{a.sim_port}/")
    if not a.no_indicative:
        print(f"  Indicative:               http://{lan}:8100/")
    if not a.no_api and a.api_host == "0.0.0.0":
        print(f"  FastAPI Swagger:          http://{lan}:9003/docs")
    print("\nKE2 uses WebSocket JSON messages, not HTTP REST:")
    print("  instrument_master, subscribe, unsubscribe, snapshot, resume, indices")
    print("  Server: capabilities, quote, heartbeat and error.")
    if not a.no_simulator:
        print(f"\nSimulator routes (port {a.sim_port}, {a.simulator}):")
        paths = ["/", "/api/health", "/api/expiries", "/api/runs"]
        if a.simulator == "gamma_weekly_server.py":
            paths.append("/api/state")
        else:
            paths += ["/api/expiry-view", "/api/expiry_view", "/api/market-history",
                      "/api/runs/{run_id}"]
        for path in paths:
            print(f"  GET    {path}")
        if a.simulator == "gamma_weekly_server.py":
            print('  POST   /api/now  JSON: {"expiry":"<listed-expiry>"}')
            print("         Schedules a simulation lane; startup does not call it.")
        else:
            for path in ["/api/select_expiry_view", "/api/select_view",
                         "/api/auto-next-expiry", "/api/auto_next_expiry",
                         "/api/select_expiry", "/api/runs/NOW/start",
                         "/api/runs/{run_id}/clear"]:
                print(f"  POST   {path}")
    if not a.no_indicative:
        print("\nIndicative routes (port 8100):")
        for path in ["/", "/health", "/api/data"]:
            print(f"  GET    {path}")
    if not a.no_api:
        print("\nFastAPI tester routes (port 9003):")
        for method, path in API_ROUTES:
            print(f"  {method:<6} {path}")
    print("\nConnection readiness is separate from fresh exchange prices.")
    print("Clients must subscribe to KE2 before expecting quote updates.")

if __name__ == "__main__":
    main()
