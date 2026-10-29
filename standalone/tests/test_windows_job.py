import os
import subprocess
import sys
import unittest
from standalone.owned_job import OwnedJob

@unittest.skipUnless(os.name=="nt","Windows process ownership test")
class JobTests(unittest.TestCase):
    def test_close_stops_only_assigned_child(self):
        job=OwnedJob()
        child=subprocess.Popen([sys.executable,"-c","import time; time.sleep(30)"],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
            creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            job.assign(child)
            self.assertIsNone(child.poll())
            job.close()
            self.assertIsNotNone(child.wait(timeout=5))
        finally:
            job.close()
            if child.poll() is None:child.terminate();child.wait(timeout=5)
if __name__=="__main__":unittest.main()
