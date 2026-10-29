from pathlib import Path
import sys
if not getattr(sys,"frozen",False):sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from standalone.launcher import entrypoint
if __name__=="__main__":entrypoint("simulator")
