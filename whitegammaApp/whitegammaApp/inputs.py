"""Input files that the workbook used to paste in with macros.

  Bhavcopy sheet     <- NSE / BSE F&O bhavcopy CSV       (load_bhavcopy)
  PrevQty sheet      <- C982_PnLDetail_<ddmmyyyy>.CSV    (load_prev_positions)
  HPTrades/HD/TN     <- <MMDD>AUTOTRD.txt trade files    (load_trades)

Every loader keeps only the configured index symbols and returns plain dicts keyed by
(symbol, expiry 'YYYY-MM-DD', strike float|None, kind 'CE'|'PE'|'FUT').
"""
from __future__ import annotations

import csv
import glob
import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

log = logging.getLogger("inputs")

_DATE_FORMATS = ("%d-%b-%Y", "%d-%b-%y", "%d%b%Y", "%d %b %Y", "%d/%m/%y", "%d/%m/%Y",
                 "%Y-%m-%d", "%d-%m-%Y", "%Y%m%d", "%d %B %Y", "%b %d %Y")


def parse_date(text) -> str | None:
    if text is None:
        return None
    if isinstance(text, (date, datetime)):
        return (text.date() if isinstance(text, datetime) else text).isoformat()
    s = str(text).strip().strip('"')
    if not s:
        return None
    s = re.sub(r"\s+", " ", s)
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(s.title() if "%b" in fmt or "%B" in fmt else s, fmt).date().isoformat()
        except ValueError:
            pass
    # "27 OCT 2026 00:00:00" or ISO with time
    head = s.split(" ")
    if len(head) > 3:
        return parse_date(" ".join(head[:3]))
    if "T" in s:
        return parse_date(s.split("T")[0])
    return None


def norm_kind(opt) -> str:
    o = (opt or "").strip().upper()
    if o in ("CE", "PE"):
        return o
    if o in ("C", "CALL"):
        return "CE"
    if o in ("P", "PUT"):
        return "PE"
    return "FUT"  # '', 'XX', 'FF', 'FUT'


def _num(text, default=None):
    if text is None:
        return default
    s = str(text).strip().replace(",", "")
    if not s:
        return default
    try:
        return float(s)
    except ValueError:
        return default


def make_key(symbol, expiry, strike, kind):
    kind = norm_kind(kind)
    return (symbol.strip().upper(), expiry, None if kind == "FUT" else float(strike), kind)


# ---------------------------------------------------------------- path helpers

def prev_trading_day(d: date, holidays=()) -> date:
    d -= timedelta(days=1)
    while d.weekday() >= 5 or d.isoformat() in holidays:
        d -= timedelta(days=1)
    return d


def read_prev_date_file(path) -> date | None:
    """bhavdat.csv as used by the bhavdat macro: the previous trading date is cell A2."""
    try:
        with open(path, newline="", encoding="utf-8-sig") as fh:
            rows = list(csv.reader(fh))
        iso = parse_date(rows[1][0]) if len(rows) > 1 and rows[1] else None
        return date.fromisoformat(iso) if iso else None
    except OSError:
        return None


def resolve_path(template: str, today: date, prev: date, allow_fallback=True):
    """Fill {today:...} / {prev:...} strftime tokens ('!' upper-cases, e.g. {prev:%d%b%Y!}).
    Returns (path or None, expected_path). When the exact file is missing and fallback is
    allowed, the newest file matching the same pattern is used (and reported as a fallback)."""
    if not template:
        return None, None

    def fill(match):
        d = today if match.group(1) == "today" else prev
        out = d.strftime(match.group(2))
        return out.upper() if match.group(3) == "!" else out

    pattern = re.compile(r"\{(today|prev):([^}!]+)(!?)\}")
    expected = pattern.sub(fill, template)
    if os.path.exists(expected):
        return expected, expected
    if allow_fallback and pattern.search(template):
        candidates = glob.glob(pattern.sub("*", template))
        if candidates:
            return max(candidates, key=os.path.getmtime), expected
    return None, expected


# ---------------------------------------------------------------- bhavcopy

def load_bhavcopy(paths, symbols) -> dict:
    """CLOSE price per contract. Accepts the old NSE layout (SYMBOL, EXPIRY_DT, ...),
    the 2024 UDiFF layout (TckrSymb, XpryDt, ...) and the workbook's BSE copy."""
    out = {}
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        with open(path, newline="", encoding="utf-8-sig") as fh:
            reader = csv.DictReader(fh)
            cols = {c.strip().upper(): c for c in (reader.fieldnames or [])}

            def pick(*names):
                for n in names:
                    if n in cols:
                        return cols[n]
                return None

            c_sym = pick("SYMBOL", "TCKRSYMB")
            c_exp = pick("EXPIRY_DT", "XPRYDT", "EXPIRY")
            c_str = pick("STRIKE_PR", "STRKPRIC", "STRIKE")
            c_opt = pick("OPTION_TYP", "OPTNTP", "OPTION_TYPE")
            c_cls = pick("CLOSE", "CLSPRIC", "CLOSE_PRICE")
            if not all((c_sym, c_exp, c_cls)):
                log.warning("Unrecognised bhavcopy header in %s", path)
                continue
            n = 0
            for row in reader:
                sym = (row.get(c_sym) or "").strip().upper()
                if sym not in symbols:
                    continue
                exp = parse_date(row.get(c_exp))
                close = _num(row.get(c_cls))
                if not exp or close is None:
                    continue
                kind = norm_kind(row.get(c_opt) if c_opt else "")
                strike = _num(row.get(c_str), 0.0) if c_str else 0.0
                out[make_key(sym, exp, strike, kind)] = close
                n += 1
            log.info("Bhavcopy %s: %d index rows", path, n)
    return out


# ---------------------------------------------------------------- previous positions

# Fixed-width columns of C982_PnLDetail (136-char lines), derived from the workbook's
# Cleaner sheet: inst, symbol, expiry, strike, type, buy qty/avg/val, sell qty/avg/val,
# net qty, close, net value, pnl.
_PNL_COLS = [(0, 9), (9, 24), (24, 32), (32, 40), (40, 43), (43, 51), (51, 59), (59, 69),
             (69, 77), (77, 85), (85, 95), (95, 105), (105, 114), (114, 125), (125, 136)]


@dataclass
class PrevPosition:
    symbol: str
    expiry: str
    strike: float | None
    kind: str
    net_qty: float
    close: float | None


def load_prev_positions(path, symbols) -> dict:
    """Net carried-forward quantity per contract (PrevQty column L)."""
    out: dict = {}
    if not path or not os.path.exists(path):
        return out
    with open(path, encoding="utf-8-sig", errors="replace") as fh:
        text = fh.read()
    lines = text.splitlines()
    first = lines[0] if lines else ""
    if "," in first and re.search(r"symbol", first, re.I):
        # Simple CSV alternative: symbol,expiry,strike,option_type,net_qty[,close]
        rows = csv.DictReader(lines)
        for r in rows:
            r = {k.strip().lower(): v for k, v in r.items() if k}
            sym = (r.get("symbol") or "").strip().upper()
            exp = parse_date(r.get("expiry"))
            if sym not in symbols or not exp:
                continue
            key = make_key(sym, exp, _num(r.get("strike"), 0.0), r.get("option_type") or r.get("type"))
            _add_prev(out, key, _num(r.get("net_qty"), 0.0), _num(r.get("close")))
        return out
    for line in lines:
        if len(line.rstrip()) < 100:
            continue
        f = [line[a:b].strip() for a, b in _PNL_COLS]
        sym = f[1].upper()
        if sym not in symbols:
            continue
        exp = parse_date(f[2])
        if not exp:
            continue
        net = _num(f[11], 0.0)
        key = make_key(sym, exp, _num(f[3], 0.0), f[4])
        _add_prev(out, key, net, _num(f[12]))
    log.info("Prev positions %s: %d index lines", path, len(out))
    return out


def _add_prev(out, key, net, close):
    cur = out.get(key)
    if cur is None:
        out[key] = PrevPosition(key[0], key[1], key[2], key[3], net, close)
    else:
        cur.net_qty += net
        if close is not None:
            cur.close = close


# ---------------------------------------------------------------- today's trades

@dataclass
class TradeAgg:
    buy_qty: float = 0.0
    buy_value: float = 0.0
    sell_qty: float = 0.0
    sell_value: float = 0.0
    count: int = 0

    @property
    def net(self):
        return self.buy_qty - self.sell_qty

    @property
    def buy_avg(self):
        return self.buy_value / self.buy_qty if self.buy_qty else 0.0

    @property
    def sell_avg(self):
        return self.sell_value / self.sell_qty if self.sell_qty else 0.0


# Column positions of the NEAT-style AUTOTRD.txt trade file as used by the HPTrades sheet:
# D symbol, E expiry, F strike, G option type, N buy(1)/sell(2), O qty, P price.
DEFAULT_TRADE_COLUMNS = {"symbol": 3, "expiry": 4, "strike": 5, "option_type": 6,
                         "side": 13, "qty": 14, "price": 15}


_NSE_EPOCH = datetime(1980, 1, 1)


def parse_expiry(text) -> str | None:
    """Expiry as written in a trade file: any of the usual date texts, NSE seconds-since-1980, or an Excel serial."""
    d = parse_date(text)
    if d:
        return d
    s = str(text or "").strip().strip('"')
    for fmt in ("%m/%d/%Y", "%m/%d/%y", "%d/%b/%Y", "%d/%b/%y", "%d%b%y", "%d-%m-%y", "%d.%m.%Y", "%d-%b-%Y %H:%M:%S", "%d %b %Y %H:%M:%S"):
        try:
            return datetime.strptime(s.title() if "%b" in fmt else s, fmt).date().isoformat()
        except ValueError:
            pass
    n = _num(s)
    if n is not None:
        if 20000 < n < 80000:                      # Excel serial date
            return (datetime(1899, 12, 30) + timedelta(days=int(n))).date().isoformat()
        if 1e8 < n < 4e9:                          # NSE: seconds since 1-Jan-1980
            return (_NSE_EPOCH + timedelta(seconds=int(n))).date().isoformat()
    return None


def _read_lines(path):
    raw = open(path, "rb").read()
    if raw[:2] in (b"\xff\xfe", b"\xfe\xff"):
        text = raw.decode("utf-16", errors="replace")
    elif raw[:3] == b"\xef\xbb\xbf":
        text = raw[3:].decode("utf-8", errors="replace")
    else:
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            text = raw.decode("cp1252", errors="replace")
    return text.replace("\x00", "").splitlines()


def _split(line, sep=None):
    if sep:
        return next(csv.reader([line], delimiter=sep))
    for sep in (",", "|", "\t", ";"):
        if sep in line:
            return next(csv.reader([line], delimiter=sep))
    return [line]


def _today_prefixes(today):
    """Same check as merge_trades_watcher.py: the trade-date column starts with today's date."""
    if not today:
        return None
    return (today.strftime("%d-%m-%Y"), today.strftime("%d/%m/%Y"), today.strftime("%d %b %Y").upper())


def load_trades(path, symbols, columns=None, today=None) -> tuple[dict, int, dict]:
    """Today's trades of one AUTOTRD file -> {key: TradeAgg}, number of trades used, and a check of what was skipped."""
    cols = {**DEFAULT_TRADE_COLUMNS, **(columns or {})}
    date_col, prefixes = cols.pop("date", 21), _today_prefixes(today)
    out: dict = {}
    diag = {"lines": 0, "index_lines": 0, "skipped": {}, "first_line": "", "first_skipped": ""}
    if not path or not os.path.exists(path):
        return out, 0, diag
    rows = 0

    def skip(reason, line):
        diag["skipped"][reason] = diag["skipped"].get(reason, 0) + 1
        if not diag["first_skipped"]:
            diag["first_skipped"] = f"{reason}: {line[:240]}"

    for line in _read_lines(path):
        if not line.strip():
            continue
        diag["lines"] += 1
        if not diag["first_line"]:
            diag["first_line"] = line[:240]
        rec = [x.strip().strip('"') for x in _split(line, ",")]
        if len(rec) <= max(cols.values()):
            skip(f"fewer than {max(cols.values()) + 1} columns", line)
            continue
        if prefixes and len(rec) > date_col and not rec[date_col].upper().startswith(prefixes):
            skip("not today's date", line)
            continue
        sym = rec[cols["symbol"]].upper()
        if sym not in symbols:
            continue                                 # stock / other contracts: not an error
        diag["index_lines"] += 1
        exp = parse_expiry(rec[cols["expiry"]])
        qty = _num(rec[cols["qty"]])
        px = _num(rec[cols["price"]])
        side = rec[cols["side"]].upper()
        if not exp:
            skip("expiry not understood", line)
            continue
        if not qty:
            skip("no quantity", line)
            continue
        if px is None:
            skip("no price", line)
            continue
        strike = _num(rec[cols["strike"]], 0.0)
        key = make_key(sym, exp, int(strike) if strike else 0.0, rec[cols["option_type"]])
        agg = out.setdefault(key, TradeAgg())
        if side in ("1", "B", "BUY"):
            agg.buy_qty += qty
            agg.buy_value += qty * px
        elif side in ("2", "S", "SELL"):
            agg.sell_qty += qty
            agg.sell_value += qty * px
        else:
            skip("buy/sell flag not 1/2/B/S", line)
            continue
        agg.count += 1
        rows += 1
    return out, rows, diag


# ---------------------------------------------------------------- BSE (SENSEX) trade files
# The workbook pastes these under row 500001 of HPTrades/HDTrades/TNTrades and splits the
# trading symbol with a LET formula: F trading symbol (e.g. SENSEX26N0582000CE weekly,
# SENSEX26OCT82000CE monthly, SENSEX26OCTFUT), G B/S, H qty, I price, M trade date.
# The files are pipe-delimited, <MMDD>AUTOTRD.txt in ...\\BSE\\FO (see merge_trades_watcher.py).
DEFAULT_BSE_COLUMNS = {"tsym": 5, "side": 6, "qty": 7, "price": 8, "date": 12}
_MON = "JANFEBMARAPRMAYJUNJULAUGSEPOCTNOVDEC"


def parse_trading_symbol(text):
    """'SENSEX26N0582000CE' -> ('SENSEX', '2026-11-05', 82000.0, 'CE'); same rules as the HPTrades row-500001 formula."""
    s = (text or "").strip().upper()
    m = re.match(r"^([A-Z&-]+?)(\d.*)$", s)
    if not m:
        return None
    sym, r = m.groups()
    fut = r.endswith("FUT")
    opt = r[-2:] in ("CE", "PE") and not fut
    if not (fut or opt) or len(r) < 5:
        return None
    b = r[2:len(r) - (3 if fut else 2)]
    try:
        y = 2000 + int(r[:2])
        if len(b) >= 3 and b[1].isdigit():                       # weekly: month code + 2-digit day
            mc = b[0]
            mon = int(mc) if mc.isdigit() else "OND".index(mc) + 10
            exp = date(y, mon, int(b[1:3]))
            rest = b[3:]
        else:                                                     # monthly: last Thursday of the month
            mon = _MON.index(b[:3]) // 3 + 1
            nxt = date(y + (mon == 12), mon % 12 + 1, 1)
            last = nxt - timedelta(days=1)
            exp = last - timedelta(days=(last.weekday() - 3) % 7)
            rest = b[3:]
    except (ValueError, IndexError):
        return None
    strike = None if fut else _num(rest)
    if opt and strike is None:
        return None
    return sym, exp.isoformat(), strike, ("FUT" if fut else r[-2:])


def load_bse_trades(path, symbols, columns=None, today=None) -> tuple[dict, int, dict]:
    """A BSE trade file (one trading symbol per line) -> same shape as load_trades()."""
    cols = {**DEFAULT_BSE_COLUMNS, **(columns or {})}
    date_col, prefixes = cols.pop("date", 12), _today_prefixes(today)
    out: dict = {}
    diag = {"lines": 0, "index_lines": 0, "skipped": {}, "first_line": "", "first_skipped": ""}
    if not path or not os.path.exists(path):
        return out, 0, diag
    rows = 0

    def skip(reason, line):
        diag["skipped"][reason] = diag["skipped"].get(reason, 0) + 1
        if not diag["first_skipped"]:
            diag["first_skipped"] = f"{reason}: {line[:240]}"

    for line in _read_lines(path):
        if not line.strip():
            continue
        diag["lines"] += 1
        if not diag["first_line"]:
            diag["first_line"] = line[:240]
        rec = [x.strip().strip('"') for x in _split(line, "|")]
        if len(rec) <= max(cols.values()):
            skip(f"fewer than {max(cols.values()) + 1} columns", line)
            continue
        if prefixes and len(rec) > date_col and not rec[date_col].upper().startswith(prefixes):
            skip("not today's date", line)
            continue
        c = parse_trading_symbol(rec[cols["tsym"]])
        if not c:
            if any(rec[cols["tsym"]].upper().startswith(x) for x in symbols):
                skip("trading symbol not understood", line)
            continue
        sym, exp, strike, kind = c
        if sym not in symbols:
            continue
        diag["index_lines"] += 1
        qty, px, side = _num(rec[cols["qty"]]), _num(rec[cols["price"]]), rec[cols["side"]].upper()
        if not qty:
            skip("no quantity", line)
            continue
        if px is None:
            skip("no price", line)
            continue
        key = make_key(sym, exp, int(strike) if strike else 0.0, "" if kind == "FUT" else kind)
        agg = out.setdefault(key, TradeAgg())
        if side in ("B", "BUY", "1"):
            agg.buy_qty += qty
            agg.buy_value += qty * px
        elif side in ("S", "SELL", "2"):
            agg.sell_qty += qty
            agg.sell_value += qty * px
        else:
            skip("buy/sell flag not B/S", line)
            continue
        agg.count += 1
        rows += 1
    return out, rows, diag


# ---------------------------------------------------------------- token lists (GreekTokens sheet)
# FB!B1 / C1: \\172.16.1.85\Shared\IndexTokens.csv and BSEIndexTokens.csv, refreshed daily. The Greekticker
# macro copies column A (token) and D:G (symbol, expiry, strike, option type); files with a header row
# (Token, Symbol, Expiry, Strike, OpType, ...) are read by the header names instead.
_TOKEN_DEFAULT = {"token": 0, "symbol": 3, "expiry": 4, "strike": 5, "opt": 6}


def _token_columns(header):
    h = [x.strip().lower().lstrip("x.") for x in header]
    def find(*names):
        exact = next((i for i, x in enumerate(h) if x in names), None)
        return exact if exact is not None else next((i for i, x in enumerate(h) if any(n in x for n in names)), None)
    cols = {"token": find("token"), "symbol": find("symbol"), "expiry": find("expiry", "exp"),
            "strike": find("strike"), "opt": find("optype", "option", "opt")}
    return cols if cols["symbol"] is not None and cols["expiry"] is not None else None


def load_tokens(path, symbols) -> tuple[dict, int, dict]:
    """-> {(sym, exp, strike|None, kind): token}, rows used, and a check of what was skipped."""
    out: dict = {}
    diag = {"lines": 0, "skipped": {}, "first_line": ""}
    if not path or not os.path.exists(path):
        return out, 0, diag
    lines = _read_lines(path)
    cols = None
    for n, line in enumerate(lines):
        if not line.strip():
            continue
        rec = [x.strip().strip('"') for x in _split(line)]
        if n == 0:
            diag["first_line"] = line[:240]
            cols = _token_columns(rec)
            if cols:
                continue                      # header row
            cols = dict(_TOKEN_DEFAULT)
        diag["lines"] += 1
        try:
            sym = rec[cols["symbol"]].upper()
        except (IndexError, TypeError):
            diag["skipped"]["short line"] = diag["skipped"].get("short line", 0) + 1
            continue
        if sym not in symbols:
            continue
        exp = parse_expiry(rec[cols["expiry"]]) if cols["expiry"] is not None and cols["expiry"] < len(rec) else None
        if not exp:
            diag["skipped"]["expiry not understood"] = diag["skipped"].get("expiry not understood", 0) + 1
            continue
        opt = (rec[cols["opt"]].upper() if cols["opt"] is not None and cols["opt"] < len(rec) else "")
        kind = opt if opt in ("CE", "PE") else "FUT"
        strike = _num(rec[cols["strike"]]) if (kind != "FUT" and cols["strike"] is not None and cols["strike"] < len(rec)) else None
        if kind != "FUT" and not strike:
            diag["skipped"]["no strike"] = diag["skipped"].get("no strike", 0) + 1
            continue
        tok = rec[cols["token"]] if cols["token"] is not None and cols["token"] < len(rec) else ""
        out[(sym, exp, float(strike) if strike else None, kind)] = tok
    # strikes in paise (raw exchange contract files): an index strike is never above 10 lakh
    for sym in {k[0] for k in out}:
        ks = [k[2] for k in out if k[0] == sym and k[2]]
        if ks and min(ks) > 1_000_000:
            for k in [k for k in out if k[0] == sym and k[2]]:
                out[(k[0], k[1], k[2] / 100, k[3])] = out.pop(k)
    return out, len(out), diag


def trade_summary(part, diag=None) -> dict:
    """Buy / sell quantity of one trade file, in total and per index, plus what the reader skipped."""
    out = {"buy": 0.0, "sell": 0.0, "trades": 0, "by_sym": {}, "check": diag or {}}
    for (sym, _exp, _k, _kind), agg in part.items():
        d = out["by_sym"].setdefault(sym, {"buy": 0.0, "sell": 0.0})
        d["buy"] += agg.buy_qty
        d["sell"] += agg.sell_qty
        out["buy"] += agg.buy_qty
        out["sell"] += agg.sell_qty
        out["trades"] += agg.count
    return out


def merge_trades(parts) -> dict:
    out: dict = {}
    for part in parts:
        for key, agg in part.items():
            tot = out.setdefault(key, TradeAgg())
            tot.buy_qty += agg.buy_qty
            tot.buy_value += agg.buy_value
            tot.sell_qty += agg.sell_qty
            tot.sell_value += agg.sell_value
            tot.count += agg.count
    return out


# ---------------------------------------------------------------- watcher

_IST = timezone(timedelta(hours=5, minutes=30))   # file times shown in IST, like the clock


@dataclass
class WatchedFile:
    name: str
    template: str
    path: str | None = None
    mtime: float | None = None
    rows: int = 0
    expected: str | None = None
    error: str | None = None
    loaded_at: str | None = None
    extra: dict = field(default_factory=dict)

    def status(self):
        return {"name": self.name, "path": self.path, "template": self.template,
                "mtime": datetime.fromtimestamp(self.mtime, _IST).replace(tzinfo=None).isoformat(timespec="seconds") if self.mtime else None,
                "rows": self.rows, "error": self.error,
                "fallback": bool(self.path and self.expected and self.path != self.expected),
                "expected": self.expected, "loaded_at": self.loaded_at, **self.extra}
