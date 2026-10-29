import json
from pathlib import Path
import tempfile
import unittest
from standalone.inputs_refresh import prepare,current

class InputRefreshTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.base=Path(self.temp.name)
        self.resources=self.base/"resources";seed=self.resources/"seed"
        (seed/"shared").mkdir(parents=True)
        (seed/"shared/GreekTokens.csv").write_text("Token,Symbol,Expiry,Strike,OpType\n123,NIFTY,2040-12-31,0,FUT\n")
        (seed/"shared/BSEIndexTokens.csv").write_text("xx.Token,Symbol,Expiry,Strike,OpType,Type\n456,SENSEX,2040-12-31,0,,FUTURE\n")
        (self.resources/"GammaShort_Backtest_Final.py").write_text("# supplied model\n")
        (self.resources/"seed_manifest.json").write_text('{"built_at":"2026-10-29"}')
        self.data=self.base/"data"
    def tearDown(self):self.temp.cleanup()
    def test_seed_is_idempotent_and_dated(self):
        first,changed=prepare(self.data,self.resources,{})
        self.assertTrue(changed);self.assertFalse(first["refresh_enabled"])
        second,changed=prepare(self.data,self.resources,{})
        self.assertFalse(changed);self.assertEqual(first["root"],second["root"])
    def test_invalid_master_never_replaces_active_snapshot(self):
        first,_=prepare(self.data,self.resources,{})
        invalid=self.base/"invalid.csv";invalid.write_text("Token,Symbol,Expiry,Strike,OpType\n")
        with self.assertRaises(ValueError):
            prepare(self.data,self.resources,{"shared/GreekTokens.csv":str(invalid)})
        self.assertEqual(current(self.data)["root"],first["root"])
    def test_intraday_reference_update_is_deferred(self):
        daily=self.resources/"seed/data/gamma_inputs/GammaShortDailydata.csv"
        daily.parent.mkdir(parents=True);daily.write_text("Date,Expiry\n2026-10-29,2026-11-03\n")
        first,_=prepare(self.data,self.resources,{})
        import sqlite3
        db=self.data/"data/gamma_weekly_simulation.db";db.parent.mkdir(parents=True)
        from datetime import datetime
        from standalone.inputs_refresh import IST
        with sqlite3.connect(db) as conn:
            conn.execute("CREATE TABLE observations(day TEXT)")
            conn.execute("INSERT INTO observations VALUES(?)",(datetime.now(IST).date().isoformat(),))
        new=self.base/"daily.csv";new.write_text("Date,Expiry\n2026-10-30,2026-11-03\n")
        second,changed=prepare(self.data,self.resources,{"data/gamma_inputs/GammaShortDailydata.csv":str(new)})
        self.assertFalse(changed)
        self.assertEqual((Path(first["root"])/daily.relative_to(self.resources/"seed")).read_bytes(),
                         (Path(second["root"])/daily.relative_to(self.resources/"seed")).read_bytes())
        self.assertIn("data/gamma_inputs/GammaShortDailydata.csv",second["deferred_reference_updates"])
    def test_empty_binding_can_refresh_but_observed_day_cannot(self):
        from gamma_weekly_server import Store
        from standalone.inputs_refresh import release_empty_binding,IST
        from datetime import datetime
        day=datetime.now(IST).date().isoformat()
        db=self.data/"data/gamma_weekly_simulation.db"
        store=Store(db);store.bind_model("old",day);store.db.close()
        self.assertTrue(release_empty_binding(self.data,day))
        store=Store(db);store.bind_model("new",day)
        with store.db:
            store.db.execute("INSERT INTO observations VALUES(?,?,?,?,?,?)",(day,"2030-12-31","09:16",100,"CE","{}"))
        store.db.close()
        self.assertFalse(release_empty_binding(self.data,day))
        store=Store(db)
        with self.assertRaises(ValueError):store.bind_model("other",day)
        store.db.close()

    def test_pre_session_updates_do_not_freeze_empty_inputs(self):
        import standalone.inputs_refresh as refresh
        from datetime import datetime
        from unittest.mock import patch
        class Morning(datetime):
            @classmethod
            def now(cls,tz=None):return cls(2026,10,29,8,45,tzinfo=refresh.IST)
        with patch.object(refresh,"datetime",Morning):
            first,_=prepare(self.data,self.resources,{})
            daily=self.base/"daily.csv";daily.write_text("Date,Expiry\\n2026-10-29,2026-11-03\\n")
            second,changed=prepare(self.data,self.resources,{"data/gamma_inputs/GammaShortDailydata.csv":str(daily)})
            self.assertTrue(changed)
            self.assertNotEqual(first["root"],second["root"])
            self.assertFalse(second["deferred_reference_updates"])

    def test_unallowlisted_destination_refused(self):
        with self.assertRaises(ValueError):prepare(self.data,self.resources,{"../outside":"anything"})
if __name__=="__main__":unittest.main()
