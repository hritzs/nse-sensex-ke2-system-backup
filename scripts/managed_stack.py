#!/usr/bin/env python3
"""Run the supervised stack using the immutable BOD input snapshot."""
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]

def listed_expiries(master, today):
    return sorted({i.expiry for i in master.by_id.values()
                   if i.exchange=="NSE" and i.symbol=="NIFTY"
                   and i.option_type and i.expiry >= today})

def main():
    active = json.loads((ROOT/"data/bod/active.json").read_text())
    from datetime import datetime
    from zoneinfo import ZoneInfo
    if active["trade_date"] != datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat():
        raise SystemExit("BOD snapshot is from another day; waiting for daily refresh")
    env = dict(os.environ)
    env.update(active["environment"])
    # All actual listed, unexpired options, including longer-dated contracts.
    # Existing simulator calculations and database binding are unchanged.
    sys.path.insert(0, str(ROOT))
    from datetime import datetime
    from zoneinfo import ZoneInfo
    from ke2_feed_server import Master
    today = datetime.now(ZoneInfo("Asia/Kolkata")).date().isoformat()
    dates = listed_expiries(Master(ROOT),today)
    if not dates:
        raise SystemExit("No unexpired NIFTY option expiries in loaded master")
    env["GS_WEEKLY_EXPIRIES"] = ",".join(dates)
    os.chdir(ROOT)
    os.execve("/bin/bash", ["/bin/bash", str(ROOT/"start_system.sh"),
        "--restart", "--gamma-weekly", "--with-api", "--lan-all"], env)

if __name__ == "__main__":
    main()
