#!/usr/bin/env python3
"""Read-only Gamma configuration readiness; does not open the simulator DB."""
import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import sys
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from gamma_weekly_engine import ROOT, load_engine, ReferenceData
from gamma_weekly_server import eligible_expiries, configured_environment
from ke2_feed_server import Master

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--strict",action="store_true")
    args=parser.parse_args()
    engine=load_engine()
    refs=ReferenceData(engine,configured_environment(ROOT,os.environ))
    today=datetime.now(ZoneInfo("Asia/Kolkata")).date()
    explicit=[x.strip() for x in os.environ.get("GS_WEEKLY_EXPIRIES","").split(",") if x.strip()]
    expiries=eligible_expiries(Master(ROOT),today,int(os.environ.get("GS_WEEKLY_HORIZON_DAYS","42")),explicit)
    blockers={expiry:refs.blockers(today.isoformat(),expiry) for expiry in expiries}
    ready=bool(expiries) and not any(blockers.values())
    print(json.dumps({"ready_for_fixed_lanes":ready,"trade_date":str(today),"expiries":expiries,
                      "blockers":blockers,"diagnostics":refs.errors,
                      "missing_daily_references":[e for e in expiries if (today.isoformat(),e) not in refs.inputs],
                      "lut_required_to_gate_entry":False},indent=2))
    return 1 if args.strict and not ready else 0

if __name__=="__main__":
    raise SystemExit(main())
