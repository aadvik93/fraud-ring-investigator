"""
app.py — FastAPI backend for the Fraud Ring Investigator.

Endpoints:
  GET  /                        → serves the SPA (index.html)
  GET  /api/alerts              → list open fraud alerts
  POST /api/investigate/{alert_id} → stream investigation steps (SSE)
  GET  /api/report/{alert_id}   → get the final investigation report
  GET  /api/graph               → get graph nodes/edges for visualization
  POST /api/reset               → clear and re-seed the graph
  GET  /api/health              → health check
"""

import os
import json
import asyncio
import logging
from pathlib import Path
from typing import AsyncGenerator

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from tools import get_alerts, get_graph_data
from agent import run_investigation

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="Fraud Ring Investigator",
    description="AI agent that investigates fraud rings using FalkorDB graph database",
    version="1.0.0"
)

# Store finished reports in memory (fine for demo purposes)
_reports: dict = {}

# ── Static files ──────────────────────────────────────────────────────────────
static_dir = Path(__file__).parent / "static"
static_dir.mkdir(exist_ok=True)


@app.get("/", response_class=HTMLResponse)
async def serve_index():
    """Serve the single-page application."""
    index = Path(__file__).parent / "static" / "index.html"
    if not index.exists():
        raise HTTPException(status_code=404, detail="index.html not found")
    return HTMLResponse(index.read_text())


# ── Health ────────────────────────────────────────────────────────────────────

@app.get("/api/health")
async def health():
    try:
        from tools import get_graph
        g = get_graph()
        res = g.query("RETURN 1 AS ok")
        return {"status": "ok", "falkordb": "connected"}
    except Exception as e:
        return JSONResponse(status_code=503, content={"status": "error", "detail": str(e)})


# ── Alerts ────────────────────────────────────────────────────────────────────

@app.get("/api/alerts")
async def list_alerts():
    """Return all open fraud alerts."""
    try:
        return {"alerts": get_alerts()}
    except Exception as e:
        logger.error(f"Failed to get alerts: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Investigation (SSE streaming) ─────────────────────────────────────────────

async def _stream_investigation(alert_id: str, mode: str) -> AsyncGenerator[str, None]:
    """Generator that yields SSE-formatted investigation steps."""
    steps = []

    def run_sync():
        """Run the synchronous investigation generator and collect steps."""
        for step in run_investigation(alert_id, mode):
            steps.append(step)

    # Run investigation in thread pool to not block the event loop
    loop = asyncio.get_event_loop()
    
    # We'll yield steps as they come using a queue approach
    import queue
    import threading

    q: queue.Queue = queue.Queue()

    def investigation_thread():
        try:
            for step in run_investigation(alert_id, mode):
                q.put(("step", step))
            q.put(("done", None))
        except Exception as e:
            q.put(("error", str(e)))

    thread = threading.Thread(target=investigation_thread, daemon=True)
    thread.start()

    while True:
        try:
            # Poll queue with small timeout so we can yield
            try:
                msg_type, payload = q.get(timeout=0.1)
            except queue.Empty:
                # Keep connection alive
                yield "data: {\"type\": \"ping\"}\n\n"
                await asyncio.sleep(0.1)
                continue

            if msg_type == "done":
                yield "data: {\"type\": \"done\"}\n\n"
                break
            elif msg_type == "error":
                yield f"data: {json.dumps({'type': 'error', 'message': payload})}\n\n"
                break
            elif msg_type == "step":
                step = payload
                # Save report when we see it
                if step.get("tool") == "report" or step.get("type") == "report":
                    _reports[alert_id] = step.get("result", step)
                yield f"data: {json.dumps(step, default=str)}\n\n"
                await asyncio.sleep(0.05)  # Small delay for animation effect

        except asyncio.CancelledError:
            break


@app.post("/api/investigate/{alert_id}")
async def investigate(alert_id: str, mode: str = "auto"):
    """
    Stream investigation steps as Server-Sent Events.
    
    Query params:
      mode: auto | nollm | claude | gemini
    """
    return StreamingResponse(
        _stream_investigation(alert_id, mode),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",  # Disable nginx buffering
        }
    )


# ── Report ────────────────────────────────────────────────────────────────────

@app.get("/api/report/{alert_id}")
async def get_report(alert_id: str):
    """Return the final investigation report for a completed investigation."""
    if alert_id not in _reports:
        raise HTTPException(status_code=404, detail="No report found for this alert. Run investigation first.")
    return _reports[alert_id]


# ── Graph data ────────────────────────────────────────────────────────────────

@app.get("/api/graph")
async def get_graph():
    """Return all graph nodes and edges for visualization."""
    try:
        return get_graph_data()
    except Exception as e:
        logger.error(f"Failed to get graph data: {e}")
        raise HTTPException(status_code=500, detail=str(e))


# ── Reset / re-seed ───────────────────────────────────────────────────────────

@app.post("/api/reset")
async def reset_graph():
    """Clear all data and re-seed the fraud graph. Clears reports too."""
    try:
        import subprocess, sys
        result = subprocess.run(
            [sys.executable, "seed.py"],
            capture_output=True, text=True,
            cwd=Path(__file__).parent
        )
        _reports.clear()
        if result.returncode == 0:
            return {"status": "ok", "message": "Graph reset and re-seeded", "output": result.stdout}
        else:
            raise HTTPException(status_code=500, detail=result.stderr)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ── Serve static files ────────────────────────────────────────────────────────
# Mount after routes so /api/* takes precedence
app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000, reload=False)
