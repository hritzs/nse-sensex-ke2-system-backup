import importlib.util
import json
import os
from pathlib import Path
import signal
import socket
import sqlite3
import subprocess
import tempfile
import time
import unittest
from unittest.mock import patch

from scripts import backup_system as backup
from scripts import restart_project_services as restart

ROOT = Path(__file__).resolve().parents[1]


class BackupTests(unittest.TestCase):
    def test_live_wal_snapshot_and_private_separation(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"indicative_close_server").mkdir()
            (root/"indicative_close_server/server.cpp").write_text("// indicative fixture\n")
            (root/".env").write_text("PRIVATE_VALUE=fixture\n")
            (root/"key.lic").write_text("offline-license-fixture")
            (root/"accidental.txt").write_text("ghp_"+"A"*40)
            (root/"data").mkdir()
            live = sqlite3.connect(root/"data/live.db")
            live.execute("PRAGMA journal_mode=WAL")
            live.execute("CREATE TABLE ledger(value)")
            live.execute("INSERT INTO ledger VALUES(1)")
            live.commit()
            live.execute("INSERT INTO ledger VALUES(2)")  # uncommitted: must not leak
            try:
                with patch.object(backup, "native_sources", return_value={"available": False}), patch.object(backup, "optional", return_value="fixture"):
                    output = backup.create(root, False, "fixture/private")
                manifest = json.loads((output/"MANIFEST.json").read_text())
                sources = {r["path"] for r in manifest["source_files"]}
                private = {r["path"] for r in manifest["private_runtime_files"]}
                self.assertIn("indicative_close_server/server.cpp", sources)
                self.assertTrue({".env","key.lic","data/live.db","accidental.txt"} <= private)
                self.assertFalse(sources & private)
                import tarfile
                with tarfile.open(output/"private-runtime.tar.gz") as archive:
                    data = archive.extractfile("runtime/data/live.db").read()
                restored = root/"restored.db"
                restored.write_bytes(data)
                with sqlite3.connect(restored) as db:
                    self.assertEqual(db.execute("SELECT value FROM ledger").fetchall(), [(1,)])
                for line in (output/"SHA256SUMS").read_text().splitlines():
                    digest, name = line.split("  ",1)
                    self.assertEqual(backup.sha(output/name), digest)
            finally:
                live.rollback()
                live.close()


class RestartTests(unittest.TestCase):
    def test_unknown_listener_aborts_before_any_stop_and_verified_stop_works(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            stub = """import socket,time
s=socket.socket();s.bind(('127.0.0.1',0));s.listen()
from pathlib import Path
Path(__file__+'.port').write_text(str(s.getsockname()[1]))
while True:time.sleep(.1)
"""
            paths = [root/"ke2_feed_server.py",root/"unrelated.py"]
            processes = []
            try:
                for path in paths:
                    path.write_text(stub)
                    processes.append(subprocess.Popen([str(ROOT/"venv/bin/python"),str(path)],
                                                     stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
                until = time.monotonic()+5
                while time.monotonic()<until and not all(Path(str(p)+".port").exists() for p in paths):
                    time.sleep(.02)
                ports = [int(Path(str(p)+".port").read_text()) for p in paths]
                with self.assertRaises(RuntimeError):
                    restart.plan(root, ports)
                self.assertTrue(all(p.poll() is None for p in processes))
                targets = restart.plan(root, [ports[0]])
                restart.stop(targets)
                processes[0].wait(timeout=3)
                self.assertIsNone(processes[1].poll())
                self.assertFalse(restart.listeners(ports[0]))
            finally:
                for p in processes:
                    if p.poll() is None:
                        p.terminate()
                    p.wait(timeout=3)

    def test_python_elsewhere_is_not_project_owned(self):
        root=ROOT.resolve()
        info={"args":["python","/tmp/ke2_feed_server.py"],"exe":"/usr/bin/python3",
              "cwd":"/tmp","pid":123,"start":"1"}
        self.assertFalse(restart.belongs(info, root))



class MasterRefreshTests(unittest.TestCase):
    def test_validate_and_install_with_explicit_identifier_exclusion(self):
        from datetime import datetime,timedelta
        from zoneinfo import ZoneInfo
        import csv
        from scripts.refresh_master import install
        today=datetime.now(ZoneInfo("Asia/Kolkata")).date()
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"shared").mkdir()
            old=root/"shared/GreekTokens.csv";old.write_text("old master")
            source=root/"incoming.csv"
            with source.open("w",newline="") as f:
                writer=csv.writer(f);writer.writerow(["Token","Symbol","Expiry","Strike","OpType","Exchange"])
                for i in range(1000):
                    writer.writerow([10000+i,"NIFTY",(today+timedelta(days=3 if i%2 else 10)).strftime("%d-%b-%y"),25000+i,"CE","NSE"])
                writer.writerow(["1e+05","PAGEIND",(today+timedelta(days=3)).strftime("%d-%b-%y"),44000,"PE","NSE"])
            result=install(source,root=root)
            self.assertEqual(result["instruments"],1000)
            self.assertEqual(result["excluded_rows"][0]["token"],"1e+05")
            self.assertEqual(old.read_bytes(),source.read_bytes())
            self.assertEqual((Path(result["previous_master_backup"])/"GreekTokens.csv").read_text(),"old master")

if __name__ == "__main__":
    unittest.main()
