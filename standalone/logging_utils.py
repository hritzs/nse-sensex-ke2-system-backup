"""Bounded logs, consistent IST timestamps and windowed-worker streams."""
import io
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import time

def handler(path):
    Path(path).parent.mkdir(parents=True,exist_ok=True)
    result=RotatingFileHandler(path,maxBytes=10*1024*1024,backupCount=3,encoding="utf-8")
    formatter=logging.Formatter("%(asctime)s IST %(levelname)s %(name)s %(message)s")
    formatter.converter=lambda seconds:time.gmtime(seconds+19800)
    result.setFormatter(formatter)
    return result

class LogStream(io.TextIOBase):
    def __init__(self,name):
        self.logger=logging.getLogger(name);self.buffered=""
    def write(self,text):
        self.buffered+=str(text)
        while "\n" in self.buffered:
            line,self.buffered=self.buffered.split("\n",1)
            if line:self.logger.info(line)
        return len(text)
    def flush(self):
        if self.buffered:self.logger.info(self.buffered);self.buffered=""
    def isatty(self):return False
