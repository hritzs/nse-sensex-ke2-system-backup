"""Deterministic local-first selection, independent for each exchange stream."""
from __future__ import annotations
from dataclasses import dataclass, field
import hashlib
import json
import math
import time

STREAMS = ("NSE", "BSE", "BSE_INDEX")

def fingerprints(master):
    result = {}
    for exchange in ("NSE", "BSE"):
        rows = [i.metadata() for i in master.by_id.values() if i.exchange == exchange]
        rows.sort(key=lambda row: row["instrument_id"])
        result[exchange] = hashlib.sha256(json.dumps(rows, sort_keys=True,
            separators=(",", ":"), allow_nan=False).encode()).hexdigest()
    return result

@dataclass
class Source:
    name: str
    verified: bool = False
    connected: bool = False
    error: str | None = None
    last: dict = field(default_factory=dict)
    since: dict = field(default_factory=dict)
    cache: dict = field(default_factory=dict)

class Selector:
    def __init__(self, names, stale_seconds=10, recovery_seconds=15):
        if not names or names[0] != "local" or len(set(names)) != len(names):
            raise ValueError("Sources must be unique and begin with local")
        if stale_seconds <= 0 or recovery_seconds < 0:
            raise ValueError("Invalid freshness/recovery thresholds")
        self.sources = {name: Source(name) for name in names}
        self.selected = dict.fromkeys(STREAMS)
        self.stale = stale_seconds
        self.recovery = recovery_seconds

    def disconnect(self, name, error=None):
        src = self.sources[name]
        src.connected = False
        src.verified = False
        src.error = error
        src.last.clear()
        src.since.clear()
        src.cache.clear()

    def observe(self, name, stream, event, now=None, wall_ms=None):
        now = time.monotonic() if now is None else now
        wall_ms = time.time()*1000 if wall_ms is None else wall_ms
        src = self.sources[name]
        if not src.connected or not src.verified or stream not in STREAMS:
            return False
        stamp = event.get("received_ms")
        if (isinstance(stamp, bool) or not isinstance(stamp, (int,float))
                or not math.isfinite(stamp) or wall_ms-stamp > self.stale*1000
                or stamp-wall_ms > 2000):
            return False
        key = (stream, event.get("type"), event.get("token", event.get("symbol")))
        previous = src.cache.get(key)
        if previous and stamp < previous["received_ms"]:
            return False
        src.cache[key] = dict(event)
        if event.get("type") in ("market_quote", "market_index"):
            # Replayed snapshots must not extend source freshness.
            observed_at = now - max(0, (wall_ms-stamp)/1000)
            old = src.last.get(stream, float("-inf"))
            if observed_at-old > self.stale:
                src.since[stream] = observed_at
            src.last[stream] = max(old, observed_at)
        return True

    def healthy(self, name, stream, now):
        src = self.sources[name]
        return (src.connected and src.verified
                and now-src.last.get(stream,float("-inf")) <= self.stale)

    def choose(self, now=None):
        now = time.monotonic() if now is None else now
        changes = {}
        names = list(self.sources)
        for stream in STREAMS:
            old = self.selected[stream]
            healthy = [n for n in names if self.healthy(n,stream,now)]
            new = old if old in healthy else next(iter(healthy),None)
            if old and old != "local" and "local" in healthy:
                if now-self.sources["local"].since.get(stream, now) >= self.recovery:
                    new = "local"
            if old != new:
                self.selected[stream] = new
                changes[stream] = (old,new)
        return changes

    def snapshot(self, name, stream, wall_ms=None):
        wall_ms = time.time()*1000 if wall_ms is None else wall_ms
        if name is None:
            return []
        return [e for (s,_,_),e in self.sources[name].cache.items()
                if s == stream and -2000 <= wall_ms-e["received_ms"] <= self.stale*1000]

    def status(self):
        now = time.monotonic()
        return {"selected": dict(self.selected), "stale_seconds": self.stale,
                "recovery_seconds": self.recovery,
                "sources": {name: {"connected":s.connected, "verified":s.verified,
                    "error":s.error, "age_seconds": {k:max(0,now-v) for k,v in s.last.items()}}
                    for name,s in self.sources.items()}}
