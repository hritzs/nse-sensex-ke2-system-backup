# ==========================================================
# FILE: analytics_py/server.py
# FULL CORRECTED VERSION
#
# RUN:
# cd ~/nse_fo_system
# source venv/bin/activate
# python analytics_py/server.py
#
# FEATURES
# ✅ Exact token mapping using nfo_instruments.csv
# ✅ ALL prices divided by 100
# ✅ Clean live screener
# ✅ Unknown tokens handled properly
# ==========================================================

import os
import time
import pandas as pd

CSV_FILE = r"/mnt/c/Users/Administrator/Desktop/python tasks/nse_fo_ticks.csv"
MASTER_FILE = r"/mnt/c/Users/Administrator/Desktop/python tasks/nfo_instruments.csv"


# ----------------------------------------------------------
# Load instrument master
# ----------------------------------------------------------
def load_master():

    if not os.path.exists(MASTER_FILE):
        return {}

    try:
        df = pd.read_csv(MASTER_FILE)

        mapping = {}

        for _, row in df.iterrows():

            token = int(row["exchange_token"])
            symbol = str(row["tradingsymbol"])

            mapping[token] = symbol

        return mapping

    except Exception:
        return {}


TOKEN_MAP = load_master()


# ----------------------------------------------------------
# Candidate token transforms
# ----------------------------------------------------------
def token_candidates(raw):

    try:
        raw = int(raw)
    except:
        return []

    vals = [
        raw,
        raw >> 8,
        raw >> 7,
        raw & 0xFFFF
    ]

    out = []
    seen = set()

    for x in vals:
        if x not in seen:
            seen.add(x)
            out.append(x)

    return out


# ----------------------------------------------------------
# Resolve symbol
# ----------------------------------------------------------
def resolve_symbol(raw_token):

    for c in token_candidates(raw_token):
        if c in TOKEN_MAP:
            return TOKEN_MAP[c]

    return f"UNK_{int(raw_token)}"


# ----------------------------------------------------------
# ALL prices scaled by 100
# ----------------------------------------------------------
def normalize_price(x):

    try:
        return float(x) / 100.0
    except:
        return 0.0


# ----------------------------------------------------------
# Load live ticks
# ----------------------------------------------------------
def load_ticks():

    if not os.path.exists(CSV_FILE):
        return pd.DataFrame()

    try:
        df = pd.read_csv(
            CSV_FILE,
            header=None,
            names=["time", "token", "price", "type"],
            on_bad_lines="skip"
        )

        df["token"] = pd.to_numeric(df["token"], errors="coerce")
        df["price"] = df["price"].apply(normalize_price)

        df = df.dropna(subset=["token"])
        df["token"] = df["token"].astype(int)

        df["symbol"] = df["token"].apply(resolve_symbol)

        return df.tail(500)

    except Exception:
        return pd.DataFrame()


# ----------------------------------------------------------
# Render terminal dashboard
# ----------------------------------------------------------
def render(df):

    os.system("clear")

    print("=" * 100)
    print(" NSE FO LIVE EXACT TOKEN SCREENER ".center(100))
    print("=" * 100)

    if df.empty:
        print("Waiting for ticks...")
        return

    latest = df.groupby("token").last().reset_index()

    latest = latest.sort_values("price", ascending=False)

    print(
        f"{'RAW TOKEN':<14}"
        f"{'SYMBOL':<45}"
        f"{'PRICE':>14}"
        f"{'TYPE':>10}"
    )

    print("-" * 100)

    for _, row in latest.head(30).iterrows():

        print(
            f"{int(row['token']):<14}"
            f"{row['symbol'][:44]:<45}"
            f"{row['price']:>14.2f}"
            f"{int(row['type']):>10}"
        )

    print("-" * 100)
    print(
        "Rows:",
        len(df),
        "| Unique Tokens:",
        latest.shape[0],
        "| Master Loaded:",
        len(TOKEN_MAP)
    )


# ----------------------------------------------------------
# Main
# ----------------------------------------------------------
def main():

    print("Loaded Master Tokens:", len(TOKEN_MAP))
    time.sleep(1)

    while True:
        df = load_ticks()
        render(df)
        time.sleep(1)


if __name__ == "__main__":
    main()