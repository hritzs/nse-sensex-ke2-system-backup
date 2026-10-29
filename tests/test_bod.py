import json
from datetime import datetime
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from gamma_weekly_server import Store
from scripts import bod_manager as bod
from scripts.managed_stack import listed_expiries

DAY="2026-10-28"

class BodTests(unittest.TestCase):
    def test_all_listed_expiries_include_long_dated_without_inventing_dates(self):
        rows=[]
        for expiry,symbol,exchange,side in [
                ("2026-10-27","NIFTY","NSE","CE"),
                ("2026-11-03","NIFTY","NSE","CE"),
                ("2031-06-24","NIFTY","NSE","PE"),
                ("2027-03-30","NIFTY","NSE","CE"),
                ("2026-11-26","SENSEX","BSE","CE"),
                ("2031-06-24","NIFTY","NSE",None)]:
            rows.append(SimpleNamespace(expiry=expiry,symbol=symbol,exchange=exchange,option_type=side))
        master=SimpleNamespace(by_id=dict(enumerate(rows)))
        self.assertEqual(listed_expiries(master,DAY),
                         ["2026-11-03","2027-03-30","2031-06-24"])

    def test_empty_day_binding_can_roll_without_touching_prior_day_results(self):
        with tempfile.TemporaryDirectory() as temp:
            path=Path(temp)/"sim.db";store=Store(path)
            try:
                store.bind_model("old",DAY)
                store.save("2026-10-27","2026-11-03","old",{"0916":{"status":"CLOSED","pnl":7}})
                store.save(DAY,"2026-11-03","old",{"0916":{"status":"WAITING_QUOTES"}})
                self.assertEqual(bod.db_state(path,DAY),("old",False))
                bod.clear_empty_binding(path,DAY,"old")
                self.assertEqual(bod.db_state(path,DAY),(None,False))
                self.assertEqual(store.restored("2026-10-27","2026-11-03","old")["0916"]["pnl"],7)
            finally:store.db.close()

    def test_real_observations_and_now_requests_prevent_reference_rebind(self):
        for kind in ("observation","now","closed"):
            with self.subTest(kind=kind),tempfile.TemporaryDirectory() as temp:
                path=Path(temp)/"sim.db";store=Store(path)
                try:
                    store.bind_model("old",DAY)
                    if kind=="observation":
                        store.append(DAY,"2026-11-03",[{"Time_dt":DAY+" 09:16:00","Strike":25000,"Type":"CE"}])
                    elif kind=="now":store.now(DAY,"2026-11-03",1001)
                    else:store.save(DAY,"2026-11-03","old",{"0916":{"status":"CLOSED","pnl":3}})
                    self.assertEqual(bod.db_state(path,DAY),("old",True))
                    with self.assertRaises(RuntimeError):bod.clear_empty_binding(path,DAY,"old")
                    self.assertEqual(bod.db_state(path,DAY)[0],"old")
                finally:store.db.close()

    def test_completed_day_does_not_refresh_again_and_pause_is_respected(self):
        class Clock:
            @staticmethod
            def now(tz):return datetime(2026,10,28,10,0,tzinfo=tz)
        with tempfile.TemporaryDirectory() as temp:
            root=Path(temp);(root/"receipts").mkdir()
            (root/"receipts"/(DAY+".json")).write_text(json.dumps(
                {"all_nifty_expiries":["2031-06-24"],"references_deferred_to_next_bod":True}))
            with patch.object(bod,"BOD",root),patch.object(bod,"datetime",Clock), \
                 patch.object(bod,"service_active",return_value=True), \
                 patch.object(bod,"prepare_and_publish") as refresh:
                self.assertEqual(bod.tick()["state"],"READY")
                refresh.assert_not_called()
                (root/"paused").write_text("operator stop")
                self.assertEqual(bod.tick()["state"],"PAUSED")
                refresh.assert_not_called()

    def test_before_bod_time_waits_without_contacting_share(self):
        class Clock:
            @staticmethod
            def now(tz):return datetime(2026,10,28,8,40,tzinfo=tz)
        with tempfile.TemporaryDirectory() as temp,patch.object(bod,"BOD",Path(temp)), \
             patch.object(bod,"datetime",Clock),patch.object(bod,"prepare_and_publish") as refresh:
            self.assertEqual(bod.tick()["state"],"WAITING_BOD")
            refresh.assert_not_called()

if __name__=="__main__":unittest.main()
