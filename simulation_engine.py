import os
import csv
import json
from pathlib import Path

LUT_DIR = Path("/home/administrator/nse_v2_microservices/live_lut")

class SimulationEngine:
    def __init__(self):
        self.luts = {}
        self.load_luts()

    def load_luts(self):
        lut_files = {
            "0916": "LUT_0916_Custom.csv",
            "0917": "LUT_0917_Custom.csv",
            "0918": "LUT_0918_Custom.csv",
            "0919": "LUT_0919_Custom.csv",
            "0920_onwards": "LUT_0920_Onwards_Custom.csv"
        }
        
        for stage, filename in lut_files.items():
            path = LUT_DIR / filename
            if path.exists():
                rules = {}
                with open(path, mode='r', encoding='utf-8') as f:
                    reader = csv.DictReader(f)
                    for row in reader:
                        # Create composite key for fast O(1) lookup
                        key = (
                            str(row.get('DTE', '')).strip(),
                            str(row.get('IV_Ratio', '')).strip(),
                            str(row.get('Straddle_Ratio', '')).strip(),
                            str(row.get('Build_IV', '')).strip(),
                            str(row.get('Norm_OG_Gap', '')).strip(),
                            str(row.get('Adj_IV_Chg', '')).strip()
                        )
                        rules[key] = row.get('Trade', 'NO')
                self.luts[stage] = rules
                print(f"[SIMULATION] Loaded {len(rules)} lookup rules for stage {stage}")

    def match_bucket(self, val, category):
        # Helper to match continuous values into LUT discrete buckets
        try:
            v = float(val)
        except:
            return str(val)

        if category == 'DTE':
            return str(int(round(v)))
        elif category == 'IV_Ratio':
            if v <= 0.65: return '<=0.65'
            elif v <= 0.80: return '0.66-0.80'
            elif v <= 0.90: return '0.81-0.90'
            elif v <= 0.95: return '0.91-0.95'
            elif v <= 1.00: return '0.96-1.00'
            elif v <= 1.05: return '1.01-1.05'
            elif v <= 1.10: return '1.06-1.10'
            elif v <= 1.20: return '1.11-1.20'
            elif v <= 1.30: return '1.21-1.30'
            else: return '>1.30'
        elif category == 'Straddle_Ratio':
            if v <= 0.95: return '<=0.95'
            elif v <= 1.00: return '0.96-1.00'
            elif v <= 1.05: return '1.01-1.05'
            elif v <= 1.10: return '1.06-1.10'
            elif v <= 1.15: return '1.11-1.15'
            elif v <= 1.20: return '1.16-1.20'
            elif v <= 1.30: return '1.21-1.30'
            elif v <= 1.50: return '1.31-1.50'
            elif v <= 1.75: return '1.51-1.75'
            elif v <= 2.00: return '1.76-2.00'
            else: return '>2.00'
        elif category == 'Build_IV':
            if v < 0.08: return '<0.08'
            elif v <= 0.12: return '0.08-0.12'
            elif v <= 0.16: return '0.12-0.16'
            elif v <= 0.20: return '0.16-0.20'
            else: return '>=0.20'
        elif category == 'Norm_OG_Gap':
            if v < -0.0075: return '<-0.75%'
            elif v > 0.0075: return '>0.75%'
            else: return 'Between -0.75% and 0.75%'
        elif category == 'Adj_IV_Chg':
            if v < -0.0075: return '<-0.0075'
            elif v > 0.0050: return '>0.0050'
            else: return '-0.0075 to 0.0050'
        return str(val)

    def evaluate_all_stages(self, dte, iv_ratio, straddle_ratio, build_iv, norm_og_gap, adj_iv_chg):
        b_dte = self.match_bucket(dte, 'DTE')
        b_ivr = self.match_bucket(iv_ratio, 'IV_Ratio')
        b_str = self.match_bucket(straddle_ratio, 'Straddle_Ratio')
        b_biv = self.match_bucket(build_iv, 'Build_IV')
        b_gap = self.match_bucket(norm_og_gap, 'Norm_OG_Gap')
        b_chg = self.match_bucket(adj_iv_chg, 'Adj_IV_Chg')

        results = {}
        for stage, rules in self.luts.items():
            key = (b_dte, b_ivr, b_str, b_biv, b_gap, b_chg)
            results[stage] = rules.get(key, 'NO')
        return results

if __name__ == "__main__":
    engine = SimulationEngine()
    # Test sample evaluation vector
    sample_res = engine.evaluate_all_stages(dte=1, iv_ratio=0.60, straddle_ratio=0.90, build_iv=0.06, norm_og_gap=-0.01, adj_iv_chg=-0.01)
    print("\nSample 5-Stage Evaluation Result:")
    print(json.dumps(sample_res, indent=2))