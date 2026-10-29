"""SPAN + Exposure margin straight from the exchange's daily SPAN risk file.

Port of SmartGreek's SpanRiskFileCalculator / AelExposureTable (METHOD.md):

  per underlying (combined commodity, cc)
    scan risk     = Σ over scan tiers of max(0, worst of the 16 scenarios of Σ net units × ra[s])
    spread charge = calendar spreads (<dSpread>, in file order): opposite-signed net deltas of legs A and B,
                    paired = min(|A|, |B|), charge += rate × paired, paired taken off both legs pro rata
    SOM           = short-option minimum: rate × (short calls + short puts)  (or max of the two)
    SPAN req      = max(scan + spread, SOM)
    NOV           = Σ options net units × price  (long premium a credit, short a debit)
    SPAN margin   = SPAN req − NOV  (floored at 0 for a net-long option book, which pays premium instead)
    Exposure      = |net futures units over all months| × underlying × 2 %
                    + Σ short option legs |units| × underlying × ELM % (AEL file, else 2 / 3 / 5 % tiers)
    Margin        = SPAN margin + Exposure

The risk file is SPAN XML 4.00 (NSE nsccl.YYYYMMDD.*.spn, BSE BSERISK*.spn); <ra> holds 16 values per unit.
Not modelled (as in the original): spot-month charge, inter-commodity spread credits, futures calendar relief.
"""
from __future__ import annotations

import csv
import io
import logging
import os
import re
import time
import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass, field
from datetime import date, datetime

log = logging.getLogger("span")

SCENARIOS = 16
# symbol -> (clearing house, cc, option product code, future product code)
CODES = {
    "NIFTY": ("NSCCL", "NIFTY", "NIFTY", "NIFTY"),
    "BANKNIFTY": ("NSCCL", "BANKNIFTY", "BANKNIFTY", "BANKNIFTY"),
    "FINNIFTY": ("NSCCL", "FINNIFTY", "FINNIFTY", "FINNIFTY"),
    "MIDCPNIFTY": ("NSCCL", "MIDCPNIFTY", "MIDCPNIFTY", "MIDCPNIFTY"),
    "SENSEX": ("ICCL", "BSX", "BSXOPT", "BSX"),
    "BANKEX": ("ICCL", "BKX", "BKXOPT", "BKX"),
    "SENSEX50": ("ICCL", "SX50", "SX50OPT", "SX50"),
}
DEFAULT_TIERS = {"near": 2.0, "otm": 3.0, "far": 5.0, "far_months": 9, "otm_call": 1.10, "otm_put": 0.90}


def exchange_of(sym):
    c = CODES.get(sym)
    return c[0] if c else None


# ---------------------------------------------------------------- risk file
@dataclass
class CcParams:
    som_rate: float = 0.0
    som_meth: str = "GROSS"
    spot_meth: str = ""
    scan_tiers: list = field(default_factory=list)      # [(sPe, ePe)]
    intra_tiers: dict = field(default_factory=dict)     # tn -> (sPe, ePe)
    spreads: list = field(default_factory=list)         # [(rate, [(kind 'p'|'t', pe|tn, side)])]


@dataclass
class RiskFile:
    path: str
    ec: str
    contracts: dict = field(default_factory=dict)       # (sym, exp_iso, strike|None, 'FUT'|'CE'|'PE') -> (price, delta, ra)
    cc: dict = field(default_factory=dict)               # sym -> CcParams
    business_date: str = ""
    loaded_at: str = ""
    parse_s: float = 0.0


def _txt(el, path, default=""):
    x = el.find(path)
    return x.text.strip() if (x is not None and x.text) else default


def _f(s, default=0.0):
    try:
        return float(s)
    except (TypeError, ValueError):
        return default


def _iso(pe):
    pe = (pe or "").strip()
    return f"{pe[:4]}-{pe[4:6]}-{pe[6:8]}" if len(pe) >= 8 and pe[:8].isdigit() else ""


def _ra(el):
    """(16 scenario values, composite delta inside <ra> or None)"""
    ra = el.find("ra")
    if ra is None:
        return None, None
    vals = [_f(a.text) for a in ra.findall("a")]
    d = ra.find("d")
    return (vals[:SCENARIOS] if len(vals) >= SCENARIOS else None), (_f(d.text) if d is not None and d.text else None)


def _open(path):
    if path.lower().endswith(".zip"):
        z = zipfile.ZipFile(path)
        name = next((n for n in z.namelist() if n.lower().endswith((".spn", ".xml"))), None)
        if not name:
            raise ValueError("no .spn inside " + path)
        return z.open(name)
    return open(path, "rb")


def parse_risk_file(path, symbols) -> RiskFile:
    """Keeps every contract of the given symbols (so any position can be margined without re-reading)."""
    t0 = time.perf_counter()
    syms = [s for s in symbols if s in CODES]
    ec = CODES[syms[0]][0] if syms else ""
    fut_pf = {CODES[s][3]: s for s in syms}
    opt_pf = {CODES[s][2]: s for s in syms}
    by_cc = {CODES[s][1]: s for s in syms}
    rf = RiskFile(path=path, ec=ec)
    pf_type = pf_sym = None
    pf_code_seen = False
    depth_pf = 0
    with _open(path) as fh:
        for event, el in ET.iterparse(fh, events=("start", "end")):
            tag = el.tag
            if event == "start":
                if tag in ("futPf", "oopPf", "phyPf", "oofPf"):
                    pf_type, pf_sym, pf_code_seen = tag, None, False
                continue
            # ---- end events
            if tag == "pfCode" and pf_type and not pf_code_seen:
                pf_code_seen = True
                code = (el.text or "").strip()
                pf_sym = fut_pf.get(code) if pf_type == "futPf" else opt_pf.get(code) if pf_type in ("oopPf", "oofPf") else None
            elif tag == "fut" and pf_type == "futPf":
                if pf_sym:
                    ra, rd = _ra(el)
                    exp = _iso(_txt(el, "pe"))
                    if ra and exp:
                        d = el.find("d")
                        delta = _f(d.text, 1.0) if d is not None and d.text else (rd if rd is not None else 1.0)
                        rf.contracts[(pf_sym, exp, None, "FUT")] = (_f(_txt(el, "p")), delta, tuple(ra))
                el.clear()
            elif tag == "series" and pf_type in ("oopPf", "oofPf"):
                if pf_sym:
                    exp = _iso(_txt(el, "pe"))
                    for o in el.findall("opt"):
                        ra, rd = _ra(o)
                        if not (ra and exp):
                            continue
                        right = _txt(o, "o").upper()[:1]
                        k = _f(_txt(o, "k"), None)
                        if k is None or right not in ("C", "P"):
                            continue
                        d = o.find("d")
                        delta = _f(d.text) if d is not None and d.text else (rd if rd is not None else 0.0)
                        rf.contracts[(pf_sym, exp, float(k), "CE" if right == "C" else "PE")] = (_f(_txt(o, "p")), delta, tuple(ra))
                el.clear()
            elif tag in ("futPf", "oopPf", "phyPf", "oofPf"):
                pf_type = pf_sym = None
                el.clear()
            elif tag == "ccDef":
                sym = by_cc.get(_txt(el, "cc"))
                if sym:
                    rf.cc[sym] = _cc_params(el)
                el.clear()
            elif tag == "date" and not rf.business_date:
                rf.business_date = (el.text or "").strip()
    rf.parse_s = time.perf_counter() - t0
    rf.loaded_at = datetime.now().isoformat(timespec="seconds")
    return rf


def _tiers(node):
    out = {}
    if node is None:
        return out
    for t in node.findall("tier"):
        s, e = _txt(t, "sPe"), _txt(t, "ePe")
        if s and e:
            out[_txt(t, "tn") or str(len(out) + 1)] = (s, e)
    return out


def _cc_params(el) -> CcParams:
    p = CcParams(som_meth=_txt(el, "somMeth", "GROSS"), spot_meth=_txt(el, "spotMeth"))
    p.scan_tiers = list(_tiers(el.find("scanTiers")).values())
    p.intra_tiers = _tiers(el.find("intraTiers"))
    som = el.find("somTiers")
    if som is not None:
        v = som.find(".//val")
        p.som_rate = _f(v.text) if v is not None else 0.0
    for ds in el.findall("dSpread"):
        rate = _f(_txt(ds, "rate/val"))
        legs = []
        for lg in ds.findall("pLeg"):
            if _txt(lg, "pe"):
                legs.append(("p", _txt(lg, "pe"), (_txt(lg, "rs") or "A").upper()))
        for lg in ds.findall("tLeg"):
            if _txt(lg, "tn"):
                legs.append(("t", _txt(lg, "tn"), (_txt(lg, "rs") or "A").upper()))
        if legs:
            p.spreads.append((rate, legs))
    return p


# ---------------------------------------------------------------- AEL (exact ELM % per option contract)
def load_ael(path, symbols) -> dict:
    """F_AEL_OPT_CONTRACTS_ddMMyyyy.csv: InsType, Symbol, ExpDate(dd-MMM-yyyy), Strike, CE/PE, CALevel, ELM%."""
    out = {}
    with open(path, newline="", encoding="utf-8-sig", errors="replace") as fh:
        for r in csv.reader(fh):
            if len(r) < 7:
                continue
            sym = r[1].strip().upper()
            if sym not in symbols:
                continue
            try:
                exp = datetime.strptime(r[2].strip(), "%d-%b-%Y").date().isoformat()
                k, pct = float(r[3]), float(r[6])
            except ValueError:
                continue
            out[(sym, exp, k, r[4].strip().upper())] = pct
    return out


# ---------------------------------------------------------------- margin
def _pe(exp_iso):
    return exp_iso.replace("-", "")


def compute(sym, positions, rf: RiskFile | None, und_px, today: date, ael=None, tiers=None, floor_span=True):
    """positions: {(sym, exp_iso, strike|None, kind): net units}. Returns the breakdown for one underlying."""
    tiers = {**DEFAULT_TIERS, **(tiers or {})}
    held = [(k, u) for k, u in positions.items() if k[0] == sym and u]
    res = {"sym": sym, "contracts": len(held), "missing": [], "scan": 0.0, "spread": 0.0, "som": 0.0,
           "span_req": 0.0, "nov": 0.0, "span": 0.0, "exposure": 0.0, "margin": 0.0, "und": und_px}
    if not held:
        return res
    if rf is None:
        res["error"] = "no risk file"
        return res
    use = []
    for k, u in held:
        c = rf.contracts.get(k)
        if c is None:
            res["missing"].append(k)
        else:
            use.append((k, u, c))
    cp = rf.cc.get(sym) or CcParams()
    # scan risk per scan tier
    def tier_of(pe):
        for i, (s, e) in enumerate(cp.scan_tiers):
            if s <= pe <= e:
                return i
        return len(cp.scan_tiers)
    groups = {}
    for k, u, c in use:
        groups.setdefault(tier_of(_pe(k[1])), []).append((u, c[2]))
    scan = 0.0
    for g in groups.values():
        worst = max(sum(u * ra[s] for u, ra in g) for s in range(SCENARIOS))
        scan += max(worst, 0.0)
    # calendar spread charge on net delta per expiry
    rem = {}
    for k, u, c in use:
        rem[_pe(k[1])] = rem.get(_pe(k[1]), 0.0) + u * c[1]
    def periods(leg):
        kind, ref, _ = leg
        if kind == "p":
            return [ref]
        s, e = cp.intra_tiers.get(ref, (None, None))
        return [pe for pe in rem if s and s <= pe <= e]
    spread = 0.0
    for rate, legs in cp.spreads:
        a = [pe for lg in legs if lg[2] == "A" for pe in periods(lg)]
        b = [pe for lg in legs if lg[2] == "B" for pe in periods(lg)]
        da, db = sum(rem.get(p, 0.0) for p in a), sum(rem.get(p, 0.0) for p in b)
        if not a or not b or da == 0 or db == 0 or (da > 0) == (db > 0):
            continue
        paired = min(abs(da), abs(db))
        spread += rate * paired
        for side in (a, b):
            tot = sum(abs(rem.get(p, 0.0)) for p in side)
            for p in side:
                d = rem.get(p, 0.0)
                if tot:
                    rem[p] = d - (1 if d > 0 else -1) * paired * abs(d) / tot
    # short option minimum
    sc = sum(-u for k, u, c in use if k[3] == "CE" and u < 0)
    sp = sum(-u for k, u, c in use if k[3] == "PE" and u < 0)
    som = cp.som_rate * (max(sc, sp) if cp.som_meth.upper() == "MAX" else sc + sp) if cp.som_rate > 0 else 0.0
    span_req = max(scan + spread, som)
    nov = sum(u * c[0] for k, u, c in use if k[3] != "FUT")
    span = max(span_req - nov, 0.0) if floor_span else span_req - nov
    # exposure on the gross short position
    px = und_px or max((c[0] for k, u, c in use if k[3] == "FUT"), default=0.0)
    exposure, legs_out = 0.0, []
    if px:
        net_fut = sum(u for k, u, c in use if k[3] == "FUT")
        exposure += abs(net_fut) * px * tiers["near"] / 100
        for k, u, c in use:
            if k[3] == "FUT" or u >= 0:
                continue
            pct = (ael or {}).get(k)
            src = "AEL"
            if pct is None:
                src = "tier"
                exp = date.fromisoformat(k[1])
                months = (exp.year - today.year) * 12 + exp.month - today.month
                if months >= tiers["far_months"]:
                    pct = tiers["far"]
                elif (k[3] == "CE" and k[2] >= px * tiers["otm_call"]) or (k[3] == "PE" and k[2] <= px * tiers["otm_put"]):
                    pct = tiers["otm"]
                else:
                    pct = tiers["near"]
            exposure += -u * px * pct / 100
            legs_out.append((src, pct))
    else:
        res["error"] = "no underlying price for exposure"
    res.update(scan=scan, spread=spread, som=som, span_req=span_req, nov=nov, span=span, exposure=exposure,
               margin=span + exposure, und=px, ael_legs=sum(1 for s, _ in legs_out if s == "AEL"),
               tier_legs=sum(1 for s, _ in legs_out if s == "tier"))
    res["missing"] = [f"{k[1]} {'FUT' if k[3] == 'FUT' else f'{k[2]:g} {k[3]}'}" for k in res["missing"]]
    return res


# ---------------------------------------------------------------- files: find the newest, download
_SFX_RANK = {"s": 9, "i5": 6, "i4": 5, "i3": 4, "i2": 3, "i1": 2, "i01": 2, "i02": 3, "i03": 4, "i04": 5, "i05": 6}


def _nse_rank(name):
    m = re.match(r"nsccl\.(\d{8})\.([a-z0-9]+)\.(spn|zip)$", name, re.I)
    return (m.group(1), _SFX_RANK.get(m.group(2).lower(), 1)) if m else None


def newest_files(folder):
    """-> {'NSCCL': path, 'ICCL': path, 'AEL': path} of the newest local files."""
    out = {}
    try:
        names = os.listdir(folder)
    except OSError:
        return out
    nse = [(r, n) for n in names if (r := _nse_rank(n)) and n.lower().endswith(".spn")]
    if nse:
        out["NSCCL"] = os.path.join(folder, max(nse)[1])
    bse = [n for n in names if n.upper().startswith("BSERISK") and n.lower().endswith(".spn")]
    if bse:
        def key(n):
            d = re.search(r"(\d{8})", n)
            return (d.group(1) if d else "", n)
        out["ICCL"] = os.path.join(folder, max(bse, key=key))
    ael = [n for n in names if n.upper().startswith("F_AEL_OPT_CONTRACTS_") and n.lower().endswith(".csv")]
    if ael:
        def akey(n):
            m = re.search(r"(\d{2})(\d{2})(\d{4})", n)
            return (m.group(3) + m.group(2) + m.group(1)) if m else ""
        out["AEL"] = os.path.join(folder, max(ael, key=akey))
    return out


UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36",
      "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9", "Referer": "https://www.nseindia.com/"}


def _get(url, timeout=20):
    import urllib.request
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def download(folder, templates, d: date, suffixes=None, kind="NSCCL"):
    """Try each URL template for date d (newest suffix first). Saves into folder; returns (saved path, url, tried)."""
    os.makedirs(folder, exist_ok=True)
    tried = []
    for tpl in templates:
        for sfx in (suffixes or [""]):
            url = tpl.format(d=d, sfx=sfx)
            name = url.rsplit("/", 1)[-1].split("?")[0]
            target = os.path.join(folder, name)
            final = target[:-4] + ".spn" if name.lower().endswith(".zip") else target
            if os.path.exists(final) or (name.lower().endswith(".zip") and _extracted(folder, name)):
                return None, url, tried       # already have this one (and it is the newest available)
            try:
                data = _get(url)
            except Exception as exc:
                tried.append(f"{url}: {getattr(exc, 'code', '') or type(exc).__name__}")
                continue
            if name.lower().endswith(".zip"):
                with zipfile.ZipFile(io.BytesIO(data)) as z:
                    inner = [n for n in z.namelist() if n.lower().endswith((".spn", ".xml", ".csv"))]
                    if not inner:
                        tried.append(f"{url}: zip without .spn")
                        continue
                    for n in inner:
                        with open(os.path.join(folder, os.path.basename(n)), "wb") as fh:
                            fh.write(z.read(n))
                    open(os.path.join(folder, name + ".done"), "w").close()
                    return os.path.join(folder, os.path.basename(inner[0])), url, tried
            if len(data) < 200 or data[:15].lower().startswith((b"<!doctype", b"<html")):
                tried.append(f"{url}: not a data file")
                continue
            with open(target, "wb") as fh:
                fh.write(data)
            return target, url, tried
    return None, None, tried


def _extracted(folder, zipname):
    return os.path.exists(os.path.join(folder, zipname + ".done"))
