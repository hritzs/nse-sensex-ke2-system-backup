from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import pandas as pd
import os

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

CSV_FILE = r"/mnt/c/Users/Administrator/Desktop/python tasks/nse_fo_ticks.csv"
MAP_FILE = r"/mnt/c/Users/Administrator/Desktop/python tasks/nfo_instruments.csv"


# -------------------------------------------------
# token map
# -------------------------------------------------
TOKENMAP = {}

if os.path.exists(MAP_FILE):
    try:
        x = pd.read_csv(MAP_FILE)

        for _, r in x.iterrows():
            TOKENMAP[int(r["exchange_token"])] = r["tradingsymbol"]

    except:
        pass


def sym(tok):
    return TOKENMAP.get(int(tok), str(tok))


def load():

    if not os.path.exists(CSV_FILE):
        return pd.DataFrame()

    try:
        df = pd.read_csv(
            CSV_FILE,
            header=None,
            names=["time","token","price","type"],
            on_bad_lines="skip"
        )

        df["token"] = pd.to_numeric(df["token"], errors="coerce")
        df["price"] = pd.to_numeric(df["price"], errors="coerce") / 100.0

        df = df.dropna()

        df["token"] = df["token"].astype(int)
        df["symbol"] = df["token"].apply(sym)

        return df.tail(2000)

    except:
        return pd.DataFrame()


@app.get("/ticks")
def ticks():

    df = load()

    if df.empty:
        return []

    z = df.groupby("token").last().reset_index()

    z = z.sort_values("price", ascending=False)

    return z[["symbol","price","type","time"]].to_dict("records")


@app.get("/movers")
def movers():

    df = load()

    rows = []

    for tok,g in df.groupby("token"):

        if len(g) < 2:
            continue

        a = float(g.iloc[0]["price"])
        b = float(g.iloc[-1]["price"])

        rows.append({
            "symbol": sym(tok),
            "first": round(a,2),
            "last": round(b,2),
            "change": round(b-a,2)
        })

    rows.sort(key=lambda x: abs(x["change"]), reverse=True)

    return rows[:30]


@app.get("/active")
def active():

    df = load()

    c = df["token"].value_counts()

    rows = []

    for tok,n in c.items():

        rows.append({
            "symbol": sym(tok),
            "ticks": int(n)
        })

    return rows[:30]