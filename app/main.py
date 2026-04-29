import asyncio
import json
import time
import uuid

from fastapi import FastAPI
from fastapi.requests import Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from starlette.responses import StreamingResponse

from .scraper import scrape

app = FastAPI()

# Limit concurrent scrapes (each Playwright ~400MB)
semaphore = asyncio.Semaphore(2)

# In-memory sessions: session_id -> { queue, result, created_at }
sessions: dict[str, dict] = {}

SESSION_TTL = 1800  # 30 minutes


def cleanup_sessions():
    now = time.time()
    expired = [sid for sid, s in sessions.items() if now - s["created_at"] > SESSION_TTL]
    for sid in expired:
        del sessions[sid]


@app.post("/api/scrape")
async def start_scrape(request: Request):
    cleanup_sessions()

    body = await request.json()
    username = body.get("username", "").strip()
    password = body.get("password", "").strip()
    mode = body.get("mode", "keyword")
    totp = body.get("totp", "").strip()

    if not username or not password:
        return JSONResponse({"error": "請輸入帳號和密碼"}, status_code=400)

    session_id = uuid.uuid4().hex[:12]
    queue: asyncio.Queue[str | None] = asyncio.Queue()
    sessions[session_id] = {
        "queue": queue,
        "result": None,
        "error": None,
        "created_at": time.time(),
    }

    async def run():
        async with semaphore:
            try:
                async def on_progress(msg: str):
                    await queue.put(json.dumps({"type": "progress", "message": msg}))

                result = await scrape(username, password, mode, on_progress, totp=totp)
                sessions[session_id]["result"] = result
                await queue.put(json.dumps({"type": "done", "data": result}))
            except Exception as e:
                sessions[session_id]["error"] = str(e)
                await queue.put(json.dumps({"type": "error", "message": str(e)}))
            finally:
                await queue.put(None)  # sentinel

    asyncio.create_task(run())
    return {"session_id": session_id}


@app.get("/api/stream/{session_id}")
async def stream(session_id: str):
    session = sessions.get(session_id)
    if not session:
        return JSONResponse({"error": "Session not found"}, status_code=404)

    async def event_generator():
        queue = session["queue"]
        while True:
            msg = await queue.get()
            if msg is None:
                break
            yield f"data: {msg}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# Serve static files and index.html
app.mount("/static", StaticFiles(directory="static"), name="static")


@app.get("/")
async def index():
    with open("static/index.html", "r", encoding="utf-8") as f:
        return HTMLResponse(f.read())
