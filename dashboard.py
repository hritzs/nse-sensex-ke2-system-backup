from fastapi import FastAPI, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import HTMLResponse
from fastapi.templating import Jinja2Templates

import json
import os
import asyncio

app = FastAPI()
templates = Jinja2Templates(directory="templates")

DATA_FILE = "build/data/ticks.json"

clients = set()


@app.get("/", response_class=HTMLResponse)
async def home(request: Request):
    return templates.TemplateResponse(
        request=request,
        name="index.html",
        context={}
    )


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    clients.add(ws)

    try:
        while True:
            await asyncio.sleep(1)
    except WebSocketDisconnect:
        clients.remove(ws)


async def broadcaster():
    last_data = ""

    while True:
        try:
            if os.path.exists(DATA_FILE):
                with open(DATA_FILE, "r") as f:
                    data = f.read()

                if data != last_data:
                    last_data = data

                    dead = []

                    for ws in clients:
                        try:
                            await ws.send_text(data)
                        except:
                            dead.append(ws)

                    for d in dead:
                        clients.discard(d)

        except:
            pass

        await asyncio.sleep(0.05)


@app.on_event("startup")
async def startup():
    asyncio.create_task(broadcaster())