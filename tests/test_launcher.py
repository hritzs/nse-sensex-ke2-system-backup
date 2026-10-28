import os
import fcntl
from pathlib import Path
import signal
import re
import urllib.request
import socket
import subprocess
import tempfile
import time
import unittest

ROOT = Path(__file__).resolve().parents[1]

class LauncherTest(unittest.TestCase):
    def test_full_launcher_refreshes_masters_by_default_and_check_is_read_only(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"run_all.sh").write_text((ROOT/"run_all.sh").read_text())
            (root/"start_system.sh").write_text('printf "%s\\n" "$@"\n')
            for extra, refresh in [([],True),(["--check"],False),
                                   (["--no-refresh-masters"],False),
                                   (["--refresh-inputs"],False)]:
                result = subprocess.run(["bash","run_all.sh",*extra],cwd=root,
                                        text=True,capture_output=True,check=True)
                self.assertEqual("--refresh-masters" in result.stdout.splitlines(),refresh)
                for flag in ("--restart","--gamma-weekly","--with-api","--lan-all"):
                    self.assertIn(flag,result.stdout.splitlines())

    def test_start_and_ctrl_c_stop_only_owned_children(self):
        self.exercise(False)

    @unittest.skipUnless((ROOT/".venv-ke2-api/bin/python").exists(), "API environment not installed")
    def test_optional_fastapi_launch_and_cleanup(self):
        self.exercise(True)

    @unittest.skipUnless((ROOT/".venv-ke2-api/bin/python").exists(), "API environment not installed")
    def test_run_all_includes_indicative_gamma_and_lan_api(self):
        self.exercise(True, all_services=True)

    def exercise(self, with_api, all_services=False):
        # Exercise the actual launcher with stand-in processes on temporary ports.
        # Never start a real decoder, adapter, simulator, or production DB.
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp)
            sockets=[]
            for _ in range(5):
                sock=socket.socket();sock.bind(("127.0.0.1",0));sockets.append(sock)
            ports=[sock.getsockname()[1] for sock in sockets]
            text=(ROOT/"start_system.sh").read_text()
            for old,new in zip((9001,9002,8000,8100,9003),ports):
                text=text.replace(str(old),str(new))
            (root/"start_system.sh").write_text(text)
            (root/"scripts").mkdir()
            for name in ("system_endpoints.py", "restart_project_services.py"):
                (root/"scripts"/name).write_text((ROOT/"scripts"/name).read_text())
            (root/"run_all.sh").write_text((ROOT/"run_all.sh").read_text())
            (root/"build-ke2").mkdir()
            (root/"shared").mkdir()
            for name in ("GreekTokens.csv","BSEIndexTokens.csv"):
                (root/"shared"/name).write_text("fixture-only\n")
            stub='''#!/usr/bin/env python3
import os,signal,socket,sys,time
from pathlib import Path
port=PORT_NUMBER
if "--port" in sys.argv: port=int(sys.argv[sys.argv.index("--port")+1])
if "simulation_server" in sys.argv[0] or "gamma_weekly_server" in sys.argv[0]: port=int(os.environ["MINUTE_HTTP_PORT"])
signal.signal(signal.SIGINT,lambda *_:sys.exit(0))
sock=socket.socket();sock.bind(("127.0.0.1",port));sock.listen()
Path(sys.argv[0]+".pid").write_text(str(os.getpid()))
while True:time.sleep(.05)
'''
            for name in ("build-ke2/nse_decoder","ke2_feed_server.py","simulation_server_2.py"):
                file=root/name
                file.write_text(stub.replace("PORT_NUMBER",str(ports[0])))
                file.chmod(0o755)
            if all_services:
                file=root/"gamma_weekly_server.py"
                file.write_text(stub.replace("PORT_NUMBER",str(ports[2])))
                (root/"gamma_weekly_engine.py").write_text("def load_engine(): return None\n")
                (root/"scripts/check_gamma_inputs.py").write_text("pass\n")
                (root/"indicative_close_server").mkdir()
                file=root/"indicative_close_server/indicative_close_server"
                file.write_text(stub.replace("PORT_NUMBER",str(ports[3])))
                file.chmod(0o755)
                (root/"shared/cm_bhavcopy").mkdir()
                (root/"shared/cm_bhavcopy/current.csv").write_text("fixture\n")
            for sock in sockets:sock.close()
            env=dict(os.environ,KE2_PYTHON=str(ROOT/"venv/bin/python"),MINUTE_HTTP_PORT=str(ports[2]),
                     KE2_API_PYTHON=str(ROOT/".venv-ke2-api/bin/python"),
                     KE2_TEST_UPSTREAM=f"ws://127.0.0.1:{ports[1]}/")
            if with_api:
                (root/"ke2_test_api.py").write_text((ROOT/"ke2_test_api.py").read_text())
            output=root/"launcher-output.txt"
            process=None
            owned=[]
            try:
                with output.open("w") as stream:
                    args=["bash","run_all.sh","--no-refresh-masters"] if all_services else ["bash","start_system.sh","--no-indicative"]
                    if with_api and not all_services:args.append("--with-api")
                    process=subprocess.Popen(args,
                        cwd=root,env=env,stdout=stream,stderr=subprocess.STDOUT)
                    deadline=time.monotonic()+10
                    while time.monotonic()<deadline:
                        if "TCP readiness confirmed" in output.read_text():break
                        if process.poll() is not None:self.fail(output.read_text())
                        time.sleep(.05)
                    else:self.fail(output.read_text())
                    owned=[int(pid) for pid in re.findall(r"PID=(\d+)",output.read_text())]
                    self.assertEqual(len(owned),5 if all_services else 4 if with_api else 3)
                    if all_services:
                        printed=output.read_text()
                        self.assertIn("Indicative dashboard:",printed)
                        self.assertIn("gamma_weekly_server.py",printed)
                        self.assertIn("/api/now",printed)
                        self.assertIn("/sessions/{session_id}/stream",printed)
                        self.assertTrue((root/"indicative_close_server/indicative_close_server.pid").exists())
                        with socket.socket() as external:
                            external.setsockopt(socket.SOL_SOCKET,socket.SO_REUSEADDR,1)
                            with self.assertRaises(OSError):external.bind(("127.0.0.2",ports[4]))
                        self.assertTrue(list((root/"logs").glob("system-*/endpoints.txt")))
                        # A running stack must allow the next verified restart.
                        with (root/"logs/system-restart.lock").open("a") as lock:
                            fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
                            fcntl.flock(lock,fcntl.LOCK_UN)
                    if with_api:
                        with urllib.request.urlopen(f"http://127.0.0.1:{ports[4]}/docs",timeout=2) as response:
                            self.assertEqual(response.status,200)
                    process.send_signal(signal.SIGINT)
                    self.assertEqual(process.wait(timeout=10),130)
                for pid in owned:
                    with self.assertRaises(ProcessLookupError):os.kill(pid,0)
            finally:
                if process and process.poll() is None:
                    process.terminate()
                    try:process.wait(timeout=10)
                    except subprocess.TimeoutExpired:process.kill();process.wait()
                for pid in owned:
                    try:os.kill(pid,signal.SIGTERM)
                    except ProcessLookupError:pass

if __name__=="__main__":unittest.main()
