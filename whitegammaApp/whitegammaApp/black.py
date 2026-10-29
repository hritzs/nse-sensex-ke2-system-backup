"""Black-Scholes with r = 0 and q = 0, priced on the forward.

This mirrors the jrvarma.xla functions the workbook calls with r=0:
  BScall / BSPUT, BSCallImplied / BSPutImplied,
  BSCALLDelta / BSPutDelta, BSCALLGamma, BSCALLVega, BSCALLTheta.

Units follow the workbook:
  vega  -> per 1.00 of volatility (the sheet divides by 100 for "per vol point")
  theta -> per year (the sheet divides by 365 for "per day")
"""
from __future__ import annotations

import math

SQRT2PI = math.sqrt(2.0 * math.pi)
IV_CAP = 7.0  # the sheet caps IV with MIN(..., 7)


def _pdf(x: float) -> float:
    return math.exp(-0.5 * x * x) / SQRT2PI


def _cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def _d1d2(f: float, k: float, vol: float, t: float):
    sv = vol * math.sqrt(t)
    d1 = (math.log(f / k) + 0.5 * vol * vol * t) / sv
    return d1, d1 - sv


def price(f: float, k: float, vol: float, t: float, cp: str) -> float:
    """cp = 'CE' or 'PE'."""
    if t <= 0 or vol <= 0:
        return max(0.0, f - k) if cp == "CE" else max(0.0, k - f)
    d1, d2 = _d1d2(f, k, vol, t)
    if cp == "CE":
        return f * _cdf(d1) - k * _cdf(d2)
    return k * _cdf(-d2) - f * _cdf(-d1)


def implied_vol(f: float, k: float, prem: float, t: float, cp: str) -> float | None:
    """Implied vol, or None when no volatility can produce this premium."""
    if not (f and k and t) or f <= 0 or k <= 0 or t <= 0 or prem is None or prem <= 0:
        return None
    intrinsic = max(0.0, f - k) if cp == "CE" else max(0.0, k - f)
    upper = f if cp == "CE" else k
    if prem <= intrinsic + 1e-12 or prem >= upper:
        return None
    lo, hi = 1e-6, 20.0
    vol = 0.2
    for _ in range(100):
        p = price(f, k, vol, t, cp)
        diff = p - prem
        if abs(diff) < 1e-9:
            return vol
        if diff > 0:
            hi = vol
        else:
            lo = vol
        d1, _ = _d1d2(f, k, vol, t)
        vega = f * _pdf(d1) * math.sqrt(t)
        nxt = vol - diff / vega if vega > 1e-12 else 0.0
        vol = nxt if lo < nxt < hi else 0.5 * (lo + hi)
    return vol


def greeks(f: float, k: float, vol: float, t: float):
    """Return dict with call/put delta, gamma, vega (per 1.00), theta (per year)."""
    if not vol or vol <= 0 or t <= 0 or f <= 0 or k <= 0:
        cd = 1.0 if f > k else 0.0
        return {"call_delta": cd, "put_delta": cd - 1.0, "gamma": 0.0, "vega": 0.0, "theta": 0.0}
    d1, _ = _d1d2(f, k, vol, t)
    sq = math.sqrt(t)
    n1 = _pdf(d1)
    cd = _cdf(d1)
    return {
        "call_delta": cd,
        "put_delta": cd - 1.0,
        "gamma": n1 / (f * vol * sq),
        "vega": f * n1 * sq,
        "theta": -f * n1 * vol / (2.0 * sq),
    }


def otm_iv(f: float, k: float, ce: float | None, pe: float | None, t: float) -> float | None:
    """Workbook rule: use the put IV below the forward, the call IV above, capped at 7."""
    iv = implied_vol(f, k, pe, t, "PE") if k < f else implied_vol(f, k, ce, t, "CE")
    if iv is None:
        return None
    return min(iv, IV_CAP)
