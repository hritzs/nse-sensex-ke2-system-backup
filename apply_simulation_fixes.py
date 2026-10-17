#!/usr/bin/env python3
from pathlib import Path
import re
import shutil
from datetime import datetime

ROOT = Path(__file__).resolve().parent
SERVER = ROOT / "simulation_server_2.py"
REPLAY = ROOT / "replay_all_running_lanes.py"

stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
SERVER_BAK = SERVER.with_name(SERVER.name + f".backup_full_fix_{stamp}")
REPLAY_BAK = REPLAY.with_name(REPLAY.name + f".backup_full_fix_{stamp}")

def backup(p, b):
    shutil.copy2(p, b)
    print(f"[BACKUP] {b.name}")

def remove_method(src: str, method_name: str) -> str:
    pattern = re.compile(
        rf"(?ms)^    def {re.escape(method_name)}\b.*?(?=^    def |\Z)"
    )
    new, n = pattern.subn("", src, count=1)
    if n != 1:
        raise RuntimeError(f"Could not remove method {method_name!r}")
    return new

def remove_field(src: str, field_name: str) -> str:
    pattern = re.compile(rf"(?m)^    {re.escape(field_name)}:.*?\n")
    new, n = pattern.subn("", src, count=1)
    if n != 1:
        raise RuntimeError(f"Could not remove field {field_name!r}")
    return new

def remove_call_block(src: str, marker: str) -> str:
    pos = src.find(marker)
    if pos < 0:
        return src

    start = src.rfind("\n", 0, pos) + 1
    open_pos = src.find("(", pos)
    if open_pos < 0:
        raise RuntimeError(f"Malformed call: {marker}")

    depth = 0
    quote = None
    escape = False
    i = open_pos

    while i < len(src):
        ch = src[i]
        if quote:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == quote:
                quote = None
        else:
            if ch in ("'", '"'):
                quote = ch
            elif ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
                if depth == 0:
                    end = i + 1
                    while end < len(src) and src[end] in " \t":
                        end += 1
                    if end < len(src) and src[end] == "\n":
                        end += 1
                    return src[:start] + src[end:]
        i += 1

    raise RuntimeError(f"Unbalanced call: {marker}")

def insert_after_first(src: str, needle: str, addition: str) -> str:
    pos = src.find(needle)
    if pos < 0:
        raise RuntimeError(f"Could not find insertion anchor: {needle!r}")
    end = pos + len(needle)
    return src[:end] + addition + src[end:]

# =========================
# simulation_server_2.py
# =========================
backup(SERVER, SERVER_BAK)
s = SERVER.read_text()

# No EMA / smoothed delta.
s = remove_method(s, "_smooth_display_delta")
s = remove_field(s, "smoothed_delta")
s = re.sub(r'(?m)^\s*self\.smoothed_delta = None\s*\n', '', s)
s = s.replace('"display_delta": self.smoothed_delta', '"display_delta": self.option_delta')
s = s.replace("'display_delta": self.smoothed_delta", "'display_delta": self.option_delta")
s = s.replace('"smoothed_delta",', '"option_delta",')
s = s.replace("'smoothed_delta',", "'option_delta',")

# Remove the invocation if it survived the function removal.
if "self._smooth_display_delta(" in s:
    s = remove_call_block(s, "self._smooth_display_delta(")

if "smoothed_delta" in s:
    raise RuntimeError("EMA removal incomplete: smoothed_delta still exists")

# Persist hedge history in snapshot().
if '"hedge_events": list(self.hedge_events)' not in s:
    anchor = '"hedge": self.hedge,'
    pos = s.find(anchor)
    if pos < 0:
        raise RuntimeError("Could not find snapshot hedge anchor")
    s = s[:pos + len(anchor)] + '\n                "hedge_events": list(self.hedge_events),' + s[pos + len(anchor):]

# Restore hedge history from payload.
if 'self.hedge_events = list(payload.get("hedge_events") or [])' not in s:
    m = re.search(r'(?ms)^    def restore\(self, payload:.*?(?=^    def |\Z)', s)
    if not m:
        raise RuntimeError("Could not find MinuteSimulation.restore()")
    block = m.group(0)
    insert_at = block.rfind("\n")
    block = (
        block[:insert_at]
        + '\n        self.hedge_events = list(payload.get("hedge_events") or [])[:MAX_HEDGE_EVENTS]'
        + block[insert_at:]
    )
    s = s[:m.start()] + block + s[m.end():]

# Replay-only persistence suppression.
if "replay_mode: bool = False" not in s:
    anchor = "expiry_view: str = DEFAULT_EXPIRY_VIEW\n"
    s = insert_after_first(
        s,
        anchor,
        "\n    # Historical replay: don't persist transient intermediate states.\n"
        "    replay_mode: bool = False\n",
    )

# Guard every MinuteSimulation persistence call.
s = s.replace(
    "            self.persistence.save_run(self)",
    "            if not self.replay_mode:\n                self.persistence.save_run(self)",
)
s = s.replace(
    "        self.persistence.save_run(self)",
    "        if not self.replay_mode:\n            self.persistence.save_run(self)",
)

# save_run(force=False): reject stale/downgrade writes unless explicitly forced.
old_sig = '    def save_run(self, run: "MinuteSimulation") -> None:'
new_sig = '    def save_run(self, run: "MinuteSimulation", force: bool = False) -> None:'
if old_sig in s:
    s = s.replace(old_sig, new_sig, 1)
elif new_sig not in s:
    raise RuntimeError("Could not locate save_run signature")

old_block = """        payload = json.dumps(json_clean(run.snapshot()), ensure_ascii=False, allow_nan=False)
        status = run.status
"""
new_block = """        snapshot = run.snapshot()
        payload = json.dumps(json_clean(snapshot), ensure_ascii=False, allow_nan=False)
        status = run.status
        incoming_history = snapshot.get("history") or []
        incoming_hedges = snapshot.get("hedge_events") or []
"""
if old_block not in s:
    raise RuntimeError("Could not locate save_run payload block")
s = s.replace(old_block, new_block, 1)

anchor = """            try:
                conn.execute(
"""
guard = """            try:
                # Prevent an older in-memory lane from erasing a richer historical replay.
                if not force:
                    existing = conn.execute(
                        \"\"\"
                        SELECT payload
                        FROM minute_lane_runs
                        WHERE trade_date=? AND expiry_view=? AND run_id=?
                        \"\"\",
                        (run.trade_date, view_mode, run.run_id),
                    ).fetchone()

                    if existing and existing["payload"]:
                        try:
                            old_snapshot = json.loads(existing["payload"])
                            old_history = old_snapshot.get("history") or []
                            old_hedges = old_snapshot.get("hedge_events") or []

                            def _latest_hist_ts(hist):
                                vals = []
                                for item in hist:
                                    if isinstance(item, dict):
                                        dt = parse_timestamp(item.get("timestamp"))
                                        if dt is not None:
                                            vals.append(dt)
                                return max(vals) if vals else None

                            old_latest = _latest_hist_ts(old_history)
                            new_latest = _latest_hist_ts(incoming_history)

                            if len(incoming_hedges) < len(old_hedges):
                                return

                            if (
                                old_latest is not None
                                and new_latest is not None
                                and old_latest > new_latest
                            ):
                                return
                        except Exception:
                            pass

                conn.execute(
"""
if anchor not in s:
    raise RuntimeError("Could not locate save_run transaction anchor")
s = s.replace(anchor, guard, 1)

SERVER.write_text(s)
print("[PATCHED] simulation_server_2.py")

# ================================
# replay_all_running_lanes.py
# ================================
backup(REPLAY, REPLAY_BAK)
r = REPLAY.read_text()

r = r.replace(
    "- Evaluates hedge/risk at HH:MM:59 so the production :59 gate is unchanged.",
    "- Evaluates hedge/risk at the authoritative Trading Terminal timestamp carried by each stored minute.",
)

if "rebuilt.started = sim.started" not in r:
    anchor = "    rebuilt.armed = sim.armed\n"
    if anchor not in r:
        raise RuntimeError("Could not find replay lifecycle anchor")
    r = r.replace(
        anchor,
        anchor + "    rebuilt.started = sim.started\n",
        1,
    )

if "rebuilt.replay_mode = True" not in r:
    anchor = "    rebuilt.armed = sim.armed\n    rebuilt.started = sim.started\n"
    if anchor not in r:
        raise RuntimeError("Could not find replay-mode anchor")
    r = r.replace(
        anchor,
        anchor + "    rebuilt.replay_mode = True\n",
        1,
    )

old = "        controller.persistence.save_run(rebuilt)\n        controller.runs_by_view[view][run_id] = rebuilt"
new = (
    "        rebuilt.replay_mode = False\n"
    "        controller.persistence.save_run(rebuilt, force=True)\n"
    "        controller.runs_by_view[view][run_id] = rebuilt"
)
if old not in r:
    raise RuntimeError("Could not locate replay final save block")
r = r.replace(old, new, 1)

if "[DB VERIFY]" not in r:
    anchor = "        controller.runs_by_view[view][run_id] = rebuilt\n"
    verify = """        # Verify that the final reconstructed state is exactly what reached SQLite.
        with controller.persistence.connect() as conn:
            stored = conn.execute(
                \"\"\"
                SELECT payload
                FROM minute_lane_runs
                WHERE trade_date=? AND expiry_view=? AND run_id=?
                \"\"\",
                (rebuilt.trade_date, view, run_id),
            ).fetchone()

        if not stored:
            raise RuntimeError(f"[DB VERIFY] missing {view}/{run_id}")

        persisted_snapshot = mod.json.loads(stored["payload"])
        persisted_history = persisted_snapshot.get("history") or []
        persisted_hedges = persisted_snapshot.get("hedge_events") or []

        if len(persisted_history) != len(rebuilt.history):
            raise RuntimeError(
                f"[DB VERIFY] history mismatch {view}/{run_id}: "
                f"memory={len(rebuilt.history)} db={len(persisted_history)}"
            )

        if len(persisted_hedges) != len(rebuilt.hedge_events):
            raise RuntimeError(
                f"[DB VERIFY] hedge mismatch {view}/{run_id}: "
                f"memory={len(rebuilt.hedge_events)} db={len(persisted_hedges)}"
            )

        print(
            f"[DB VERIFY] {view}/{run_id} "
            f"history={len(persisted_history)} hedges={len(persisted_hedges)} "
            f"latest={persisted_history[0].get('timestamp') if persisted_history else None}"
        )

"""
    if anchor not in r:
        raise RuntimeError("Could not find replay assignment anchor")
    r = r.replace(anchor, anchor + verify, 1)

REPLAY.write_text(r)
print("[PATCHED] replay_all_running_lanes.py")

import py_compile
py_compile.compile(str(SERVER), doraise=True)
py_compile.compile(str(REPLAY), doraise=True)

print()
print("[OK] Both files compile.")
print("[OK] EMA removed.")
print("[OK] Raw option_delta remains the displayed/authoritative delta.")
print("[OK] hedge_events are persisted and restored.")
print("[OK] Replay suppresses intermediate DB writes.")
print("[OK] Replay final commit is forced and read-back verified.")
print("[OK] Stale lower-quality live writes cannot erase richer replay state.")
print(f"[OK] Backups: {SERVER_BAK.name} / {REPLAY_BAK.name}")
