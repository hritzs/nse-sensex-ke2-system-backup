import json
import os
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from gamma_weekly_engine import load_engine, ReferenceData, evaluate, LANES
from gamma_weekly_server import Store, eligible_expiries

ROOT = Path(__file__).resolve().parents[1]


class GammaTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = load_engine()

    def frame(self, day="2026-10-16", expiry="2026-10-20", seconds=346, moving=False):
        e = self.engine
        times = e.pd.date_range(day + " 09:15:00", periods=seconds, freq="1s")
        rows = []
        for index, stamp in enumerate(times):
            u = 25000.0 + (60.0 * __import__("math").sin(index/50.0) if moving else 0.0)
            for k in (24950.0, 25000.0, 25050.0):
                for side in ("CE", "PE"):
                    price = e._bs_price_kernel(side == "CE", u, k, 2.5 / 365, 0, .2)
                    rows.append(dict(Time_dt=stamp, Strike=k, Type=side,
                        LTP=price, Bid=price-.05, Ask=price+.05, DTE=2.5,
                        Expiry=expiry, UnderlyingLTP=u, UnderlyingBid=u-.1, UnderlyingAsk=u+.1))
        return e.pd.DataFrame(rows)

    def refs(self, events=None, tables=None):
        return SimpleNamespace(blockers=lambda *_: [], events=events or set(),
            tables=tables if tables is not None else [None]*5, fingerprint="fixture",
            inputs={("2026-10-16", "2026-10-20"): dict(prev_straddle=200,
                adj_iv_chg=0, yesterday_iv=.2, prev_future=25000,
                prev_pure=.2, prev_with_prev=.2)})

    def test_fixed_entries_do_not_require_lut_on(self):
        e = self.engine
        refs = self.refs(tables=[e.np.zeros(e.DIMS, dtype=bool)]*5)
        result = evaluate(e, refs, self.frame(), "2026-10-16", "2026-10-20")
        self.assertEqual(set(result["lanes"]), set(LANES))
        for lane in LANES[:-1]:
            row = result["lanes"][lane]
            self.assertEqual(row["signal"], "LUT_OFF")
            self.assertIn(row["status"], ("OPEN_PROVISIONAL", "CLOSED"))
            self.assertFalse(row["filters_gate_entry"])
            self.assertTrue(e.np.isfinite(row["net_pnl"]))
        self.assertEqual(result["lanes"]["NOW"]["status"], "READY")

    def test_missing_lut_is_unknown_and_event_still_sizes_fixed_lane(self):
        e = self.engine
        frame = self.frame()
        regular = evaluate(e, self.refs(), frame, "2026-10-16", "2026-10-20")
        events = {e.pd.Timestamp("2026-10-16").date()}
        event = evaluate(e, self.refs(events=events), frame, "2026-10-16", "2026-10-20")
        self.assertEqual(regular["lanes"]["0918"]["signal"], "LUT_UNAVAILABLE")
        self.assertEqual(event["lanes"]["0916"]["signal"], "EVENT_PRE_0918")
        for lane in LANES[:-1]:
            a, b = regular["lanes"][lane], event["lanes"][lane]
            self.assertAlmostEqual(b["gross_pnl"], a["gross_pnl"]/3)
            self.assertEqual(b["entry_cost"], a["entry_cost"])

    def test_bad_lut_and_absent_reference_blockers(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            (p/"LUT_0916.csv").write_text("Trade\nYES\n")
            refs = ReferenceData(self.engine, {"GS_LUT_DIR": temp})
            self.assertTrue(any("52920" in x for x in refs.errors))
            self.assertEqual(refs.tables, [None]*5)
            self.assertEqual(set(refs.blockers("2026-10-16", "2026-10-20")),
                             {"EVENT_CALENDAR_UNAVAILABLE"})

    def test_contexts_remain_expiry_specific(self):
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp)
            header = "Date,Expiry,prev_straddle,adj_iv_chg,yesterday_iv,prev_future,prev_pure,prev_with_prev\n"
            (p/"refs.csv").write_text(header+"2026-10-16,2026-10-20,200,0,0.2,25000,0.2,0.2\n")
            (p/"events.csv").write_text("Date\n")
            refs = ReferenceData(self.engine, {"GS_EXPIRY_INPUTS": str(p/"refs.csv"),
                                              "GS_EVENT_PATH": str(p/"events.csv")})
            self.assertFalse(refs.blockers("2026-10-16", "2026-10-20"))
            self.assertFalse(refs.blockers("2026-10-16", "2026-10-27"))
            self.assertNotIn(("2026-10-16", "2026-10-27"),refs.inputs)

    def test_store_preserves_now_and_model_version(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp)/"state.db"
            store = Store(path)
            store.bind_model("one")
            self.assertEqual(store.now("2026-10-16", "2026-10-20", 1000), 1000)
            self.assertEqual(store.now("2026-10-16", "2026-10-20", 1100), 1000)
            store.save("2026-10-16", "2026-10-20", "one", {"NOW": {"status": "CLOSED"}})
            store.db.close()
            restored = Store(path)
            self.assertEqual(restored.restored("2026-10-16", "2026-10-20", "one")["NOW"]["status"], "CLOSED")
            with self.assertRaises(ValueError):
                restored.bind_model("two")
            restored.db.close()

    def test_expiry_discovery_excludes_expired_and_far_quarterlies(self):
        from datetime import date
        instruments = [SimpleNamespace(exchange="NSE", symbol="NIFTY", option_type="CE", expiry=e)
                       for e in ("2026-10-06", "2026-10-20", "2026-10-27", "2031-12-30")]
        master = SimpleNamespace(by_id=dict(enumerate(instruments)))
        self.assertEqual(eligible_expiries(master, date(2026,10,16)),
                         ["2026-10-20", "2026-10-27"])
        with self.assertRaises(ValueError):
            eligible_expiries(master, date(2026,10,16), explicit=["2026-10-21"])

    def test_no_historical_backward_fill_and_no_thread_environment_mutation(self):
        import inspect
        before = os.environ.get("NUMBA_NUM_THREADS")
        engine = load_engine()
        self.assertEqual(os.environ.get("NUMBA_NUM_THREADS"), before)
        frame = self.frame(seconds=2)
        # Strike data begin on the second observed second. Earlier IV stays NaN.
        grid = engine.pd.DataFrame(dict(Second=[x.time() for x in engine.pd.to_datetime(frame.Time_dt).unique()],
                                        Underlying=[25000.,25000.], DTE=[2.5,2.5]))
        data = frame[frame.Time_dt > frame.Time_dt.min()].copy()
        data["_K"] = data.Strike
        data["Second"] = data.Time_dt.dt.time
        arrays = engine.strike_arrays(25000., grid, data)
        self.assertTrue(engine.np.isnan(arrays[0][0]))
        self.assertTrue(engine.np.isnan(arrays[2][0]))

    def test_two_expiry_quote_capture_to_engine_and_http(self):
        from datetime import datetime, timedelta
        import threading
        from http.server import ThreadingHTTPServer
        import urllib.request
        from gamma_weekly_server import Simulator, make_handler, IST
        import shutil
        e = self.engine
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            (root/"shared").mkdir()
            shutil.copy2(ROOT/"GammaShort_Backtest_Final.py", root/"GammaShort_Backtest_Final.py")
            day = datetime.now(IST).date().isoformat()
            expiries = [(datetime.now(IST).date()+timedelta(days=x)).isoformat() for x in (4,11)]
            master = "Token,Symbol,Expiry,Strike,OpType\n"
            tokens = {}
            n = 1000
            for expiry, u in zip(expiries, (25000,26000)):
                for k in (u-50,u,u+50):
                    for side in ("CE","PE"):
                        n += 1
                        tokens[expiry,k,side] = n
                        master += f"{n},NIFTY,{expiry},{k},{side}\n"
            (root/"shared/GreekTokens.csv").write_text(master)
            (root/"shared/BSEIndexTokens.csv").write_text("xx.Token,Symbol,Expiry,Strike,OpType\n")
            header = "Date,Expiry,prev_straddle,adj_iv_chg,yesterday_iv,prev_future,prev_pure,prev_with_prev\n"
            (root/"refs.csv").write_text(header+"".join(f"{day},{ex},200,0,0.2,{u},0.2,0.2\n"
                                                      for ex,u in zip(expiries,(25000,26000))))
            (root/"events.csv").write_text("Date\n")
            sim = Simulator(root, dict(GS_SIM_DB=str(root/"sim.db"),
                GS_EXPIRY_INPUTS=str(root/"refs.csv"),GS_EVENT_PATH=str(root/"events.csv")))
            stamp = datetime.fromisoformat(day+"T09:15:00").replace(tzinfo=IST)
            for second in range(346):
                at = stamp + timedelta(seconds=second)
                for expiry, u in zip(expiries,(25000,26000)):
                    for k in (u-50,u,u+50):
                        for side in ("CE","PE"):
                            close = datetime.fromisoformat(expiry+"T15:30:00").replace(tzinfo=IST)
                            t = (close-at).total_seconds()/(86400*365)
                            price = e._bs_price_kernel(side=="CE",u,k,t,0,.2)
                            sim.ingest(dict(type="market_quote",exchange="NSE",
                                token=tokens[expiry,k,side],ltp=price,bid=price-.05,ask=price+.05),at)
                sim.sample(at)
            sim.calculate()
            state = sim.state()
            for expiry,u in zip(expiries,(25000,26000)):
                self.assertEqual(set(state["results"][expiry]["lanes"]),set(LANES))
                self.assertEqual(state["results"][expiry]["lanes"]["0916"]["strike"],u)
            http = ThreadingHTTPServer(("127.0.0.1",0),make_handler(sim))
            thread = threading.Thread(target=http.serve_forever,daemon=True);thread.start()
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{http.server_port}/api/state") as response:
                    payload = json.load(response)
                self.assertEqual(len(payload["results"]),2)
                self.assertFalse(payload["basis"]["backtest_tick_parity_verified"])
            finally:
                http.shutdown();http.server_close();thread.join()
                sim.store.db.close()

    def test_missing_references_preserve_full_trade_calculations(self):
        e = self.engine
        frame = self.frame()
        complete = evaluate(e, self.refs(), frame, "2026-10-16", "2026-10-20")
        missing = self.refs()
        missing.inputs = {}
        without = evaluate(e, missing, frame, "2026-10-16", "2026-10-20")
        self.assertFalse(without["daily_reference_available"])
        for lane in LANES[:-1]:
            a, b = complete["lanes"][lane], without["lanes"][lane]
            self.assertEqual(b["signal"], "REFERENCE_UNAVAILABLE")
            for field in ("gross_pnl","net_pnl","entry_cost","hedge_cost","hedge_notional","strike","sl_bps","tp_bps"):
                self.assertAlmostEqual(a[field],b[field],places=9,msg=field)

    def test_missing_calendar_still_blocks(self):
        references = self.refs()
        references.blockers = lambda *_: ["EVENT_CALENDAR_UNAVAILABLE"]
        result = evaluate(self.engine,references,self.frame(),"2026-10-16","2026-10-20")
        self.assertEqual(result["status"],"WAITING_INPUTS")

    def test_new_trade_date_accepts_new_daily_reference_version(self):
        with tempfile.TemporaryDirectory() as temp:
            store = Store(Path(temp)/"dated.db")
            try:
                store.bind_model("day-one", "2026-10-16")
                store.bind_model("day-two", "2026-10-19")
                with self.assertRaises(ValueError):
                    store.bind_model("edited-same-day", "2026-10-19")
            finally:
                store.db.close()

    def test_each_second_is_one_consistent_snapshot(self):
        with tempfile.TemporaryDirectory() as temp:
            store=Store(Path(temp)/"snap.db")
            try:
                row={"Time_dt":"2026-10-16 09:16:00","Strike":25000,"Type":"CE"}
                store.append("2026-10-16","2026-10-20",[row])
                moved=dict(row,Strike=25050)
                store.append("2026-10-16","2026-10-20",[moved])
                frame=store.frame(self.engine,"2026-10-16","2026-10-20")
                self.assertEqual(frame.Strike.tolist(),[25050])
            finally:
                store.db.close()

    def test_moving_market_reference_fallback_keeps_hedging_and_event_costs(self):
        e=self.engine
        frame=self.frame(seconds=901,moving=True)
        events={e.pd.Timestamp("2026-10-16").date()}
        complete=self.refs(events=events)
        baseline=evaluate(e,complete,frame,"2026-10-16","2026-10-20")
        missing=self.refs(events=events);missing.inputs={}
        fallback=evaluate(e,missing,frame,"2026-10-16","2026-10-20")
        for lane in LANES[:-1]:
            a,b=baseline["lanes"][lane],fallback["lanes"][lane]
            self.assertEqual(b["signal"],"REFERENCE_UNAVAILABLE")
            self.assertEqual(b["size"],1/3)
            self.assertGreater(b["hedge_notional"],0)
            for field in ("gross_pnl","net_pnl","entry_cost","hedge_cost","hedge_notional","strike","sl_bps","tp_bps"):
                self.assertAlmostEqual(a[field],b[field],places=8,msg=lane+" "+field)

    def test_incomplete_daily_values_use_full_market_calculations(self):
        references=self.refs()
        references.inputs["2026-10-16","2026-10-20"]["prev_future"]=float("nan")
        result=evaluate(self.engine,references,self.frame(),"2026-10-16","2026-10-20")
        self.assertFalse(result["daily_reference_available"])
        self.assertEqual(result["lanes"]["0916"]["signal"],"REFERENCE_UNAVAILABLE")
        self.assertTrue(self.engine.np.isfinite(result["lanes"]["0916"]["net_pnl"]))


if __name__ == "__main__":
    unittest.main()
