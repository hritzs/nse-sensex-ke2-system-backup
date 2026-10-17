import os
import asyncio
import httpx
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
from dotenv import load_dotenv

load_dotenv()

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

GEMINI_API_KEY = os.getenv("GEMINI_API_KEY")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

if not GEMINI_API_KEY:
    raise RuntimeError("GEMINI_API_KEY is not set")

class NLQueryRequest(BaseModel):
    query: str

# Cache for models
_AVAILABLE_MODELS_CACHE = None

@app.on_event("startup")
async def startup_event():
    global _AVAILABLE_MODELS_CACHE
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    async with httpx.AsyncClient() as client:
        try:
            resp = await client.get(url, params={"key": GEMINI_API_KEY}, timeout=20)
            if resp.status_code == 200:
                data = resp.json()
                _AVAILABLE_MODELS_CACHE = [
                    {
                        "name": m.get("name"),
                        "displayName": m.get("displayName"),
                        "supportedGenerationMethods": m.get("supportedGenerationMethods", [])
                    }
                    for m in data.get("models", [])
                    if "generateContent" in m.get("supportedGenerationMethods", [])
                ]
        except Exception as e:
            print(f"Failed to cache models on startup: {e}")

@app.get("/models")
async def list_available_models():
    if _AVAILABLE_MODELS_CACHE is not None:
        return {"models": _AVAILABLE_MODELS_CACHE}
        
    url = "https://generativelanguage.googleapis.com/v1beta/models"
    async with httpx.AsyncClient() as client:
        resp = await client.get(url, params={"key": GEMINI_API_KEY}, timeout=20)
        if not resp.is_success:
            raise HTTPException(status_code=500, detail=f"Gemini API Error: {resp.text}")

        data = resp.json()
        usable = [
            {
                "name": m.get("name"),
                "displayName": m.get("displayName"),
                "supportedGenerationMethods": m.get("supportedGenerationMethods", [])
            }
            for m in data.get("models", [])
            if "generateContent" in m.get("supportedGenerationMethods", [])
        ]
        return {"models": usable}

@app.post("/translate-q")
async def translate_nl_to_q(req: NLQueryRequest):
    system_prompt = """You are an expert KDB+/q data analyst. Translate the user's request into a single valid 'q' query.
Table: greeks_chain
Columns: time, sym, underlying, expiry, strike, ltp, bid, ask, spot, future, iv, delta, gamma, theta, vega, chg1d, chg5, chg15, chg30, chg60, max_iv, min_iv, oi, volume, lpp_high, lpp_low, prev_oi, chg_future, atm_iv
Rules:
- Return ONLY the raw q code string. No markdown, no backticks, no explanations.
- Example 'top 5 highest iv': 5#select sym, strike, iv from `iv xdesc greeks_chain"""

    url = f"https://generativelanguage.googleapis.com/v1beta/models/{GEMINI_MODEL}:generateContent"

    payload = {
        "system_instruction": {
            "parts": [{"text": system_prompt}]
        },
        "contents": [
            {
                "role": "user",
                "parts": [{"text": req.query}]
            }
        ],
        "generationConfig": {
            "temperature": 0
        }
    }

    async with httpx.AsyncClient() as client:
        retries = 3
        for attempt in range(retries):
            resp = await client.post(url, params={"key": GEMINI_API_KEY}, json=payload, timeout=30)
            
            if resp.status_code == 503:
                if attempt < retries - 1:
                    await asyncio.sleep(1) # wait before retrying
                    continue
                else:
                    raise HTTPException(status_code=503, detail="Gemini API Error: 503 Service Unavailable after retries.")
            elif not resp.is_success:
                raise HTTPException(status_code=500, detail=f"Gemini API Error ({GEMINI_MODEL}): {resp.text}")
            else:
                break

    data = resp.json()
    try:
        q_code = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        q_code = q_code.replace("```q", "").replace("```", "").strip()
        return {"model": GEMINI_MODEL, "q_query": q_code}
    except (KeyError, IndexError):
        raise HTTPException(status_code=500, detail=f"Failed to parse Gemini response: {data}")