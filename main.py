import os
import pandas as pd
from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel
import google.generativeai as genai
from qpython import qconnection
import uvicorn

# Initialize FastAPI
app = FastAPI(title="KDB+ AI Query Assistant")

# Allow the React frontend to communicate with this API
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"], # Change to your frontend URL in production (e.g., http://localhost:3000)
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Set up Gemini AI 
api_key = os.getenv("GEMINI_API_KEY")
if api_key:
    genai.configure(api_key=api_key)
    model = genai.GenerativeModel('gemini-1.5-flash')
else:
    print("WARNING: GEMINI_API_KEY environment variable not set. Generation will fail.")

# Data Models
class GenerateRequest(BaseModel):
    text: str

class ExecuteRequest(BaseModel):
    query: str

# The Prompt Template injecting your Database Schema
SCHEMA_PROMPT = """
You are an expert KDB+ / q database programmer.
I have a table named `greeks_chain` running on port 5000 with the following schema:
- sym: symbol (e.g., `NIFTY26MAY24500CE)
- underlying: symbol (e.g., `NIFTY)
- expiry: date
- strike: float
- type: symbol (`CE or `PE)
- ltp: float
- iv: float (implied volatility in percentage)
- delta: float
- theta: float
- gamma: float
- vega: float
- oi: long (open interest)

Translate the user's natural language request into a valid, optimized q-SQL query.
- Use 'asc' or 'desc' for sorting.
- Use '#' for limiting rows (e.g., `5#select...` for top 5).
- Use 'like' for string matching if needed.

Return ONLY the raw q query string. Do NOT include markdown formatting (like ```q). Do NOT explain yourself.

User Request: "{text}"
"""

@app.post("/api/generate-q")
async def generate_q_query(req: GenerateRequest):
    if not api_key:
        raise HTTPException(status_code=500, detail="GEMINI_API_KEY is missing on the server.")
    
    try:
        prompt = SCHEMA_PROMPT.format(text=req.text)
        response = model.generate_content(prompt)
        
        raw_query = response.text.strip()
        if raw_query.startswith("```"):
            raw_query = "\n".join(raw_query.split("\n")[1:-1])
            
        return {"query": raw_query.strip()}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"LLM Generation Error: {str(e)}")

@app.post("/api/execute-q")
async def execute_q_query(req: ExecuteRequest):
    try:
        # Connect to local KDB+ instance; pandas=True auto-converts to DataFrames
        with qconnection.QConnection(host='localhost', port=5000, pandas=True) as q:
            result = q(req.query)
            
            if isinstance(result, pd.DataFrame):
                for col in result.select_dtypes([object]).columns:
                    result[col] = result[col].apply(lambda x: x.decode('utf-8') if isinstance(x, bytes) else x)
                result = result.replace([pd.NA, float('inf'), float('-inf')], None)
                return {"data": result.to_dict(orient='records')}
            else:
                val = result.decode('utf-8') if isinstance(result, bytes) else str(result)
                return {"data": [{"result": val}]}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"KDB+ Execution Error: {str(e)}")

# Start the server automatically when you run the script
if __name__ == "__main__":
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)