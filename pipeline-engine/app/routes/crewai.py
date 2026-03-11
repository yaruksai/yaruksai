"""
app/routes/crewai.py — CrewAI 6-Stage Pipeline API
═══════════════════════════════════════════════════
Endpoints for running, monitoring, and approving the
YARUKSAI CrewAI multi-agent pipeline.
"""

import sys
import json
import asyncio
import threading
from pathlib import Path
from datetime import datetime
from typing import Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

import app.shared as _shared

router = APIRouter(tags=["crewai"])

# ─── Path setup ─────────────────────────────────────────────
_pipeline_root = Path(__file__).resolve().parent.parent.parent
_crew_engine = _pipeline_root / "crew_engine"
if str(_pipeline_root) not in sys.path:
    sys.path.insert(0, str(_pipeline_root))
if str(_crew_engine) not in sys.path:
    sys.path.insert(0, str(_crew_engine))

ARTIFACTS_DIR = _pipeline_root / "artifacts"
ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)

# ─── Global State ───────────────────────────────────────────
pipeline_state = {
    "running": False,
    "stages": [],
    "error": None,
    "started_at": None,
    "finished_at": None,
    "goal": None,
}

_sse_queues: list[asyncio.Queue] = []


def _broadcast_sse(event_data: dict):
    """Broadcast to all connected SSE clients."""
    for q in _sse_queues:
        try:
            q.put_nowait(event_data)
        except asyncio.QueueFull:
            pass


def _status_callback(msg: dict):
    """Capture status updates from the orchestrator."""
    pipeline_state["stages"].append(msg)
    _broadcast_sse({"type": "stage", **msg})


# ─── Pipeline Runner ────────────────────────────────────────
def _run_pipeline(goal: str):
    """Run the 6-stage pipeline in a background thread."""
    try:
        from crew_engine.orchestrator import (
            run_six_stage_flow,
            load_docs_context_for_goal,
            set_status_callback,
        )

        set_status_callback(_status_callback)

        docs_context = load_docs_context_for_goal()
        full_goal = (goal if goal.strip() else
            "Build a YARUKSAI-governed multi-agent MVP that produces structured artifacts, "
            "audit outputs, and a final governance decision."
        ) + docs_context

        pipeline_state["goal"] = goal or "(default goal)"
        pipeline_state["started_at"] = datetime.now().isoformat()
        pipeline_state["running"] = True
        pipeline_state["stages"] = []
        pipeline_state["error"] = None
        pipeline_state["finished_at"] = None

        _broadcast_sse({"type": "started", "goal": pipeline_state["goal"]})

        result = run_six_stage_flow(full_goal)
        final_gate = result[5] if len(result) > 5 else {}

        pipeline_state["running"] = False
        pipeline_state["finished_at"] = datetime.now().isoformat()

        _broadcast_sse({
            "type": "completed",
            "decision": final_gate.get("decision", "unknown"),
            "reason": final_gate.get("reason", ""),
        })

    except Exception as e:
        pipeline_state["running"] = False
        pipeline_state["error"] = str(e)
        pipeline_state["finished_at"] = datetime.now().isoformat()
        _broadcast_sse({"type": "error", "message": str(e)})


# ─── API Endpoints ──────────────────────────────────────────

@router.get("/api/crewai/config")
async def get_crewai_config():
    """Return current LLM and system configuration."""
    try:
        from crew_engine.config import LLM_PROVIDER, LLM_MODEL, OLLAMA_BASE_URL
        return {
            "llm_provider": LLM_PROVIDER,
            "llm_model": LLM_MODEL,
            "ollama_base_url": OLLAMA_BASE_URL if LLM_PROVIDER == "ollama" else None,
            "version": "1.0.0",
            "engine": "crew_engine",
        }
    except Exception as e:
        return {"error": str(e), "version": "1.0.0"}


@router.post("/api/crewai/run")
async def run_crewai_pipeline(request: Request):
    """Start the 6-stage CrewAI pipeline."""
    if _shared.EMERGENCY_STOPPED:
        raise HTTPException(503, "System in emergency stop mode")

    if pipeline_state["running"]:
        raise HTTPException(409, "Pipeline is already running")

    try:
        body = await request.json()
    except Exception:
        body = {}

    goal = body.get("goal", "")

    thread = threading.Thread(target=_run_pipeline, args=(goal,), daemon=True)
    thread.start()

    return JSONResponse({
        "status": "started",
        "goal": goal or "(default goal)",
        "message": "6-stage CrewAI pipeline started in background",
    })


@router.get("/api/crewai/status")
async def get_crewai_status():
    """Return current pipeline status."""
    return JSONResponse(pipeline_state)


@router.get("/api/crewai/stream")
async def crewai_sse_stream():
    """SSE stream for live pipeline tracking."""
    queue: asyncio.Queue = asyncio.Queue(maxsize=100)
    _sse_queues.append(queue)

    async def event_generator():
        try:
            yield f"data: {json.dumps({'type': 'init', **pipeline_state}, default=str)}\n\n"
            while True:
                try:
                    data = await asyncio.wait_for(queue.get(), timeout=30.0)
                    yield f"data: {json.dumps(data, default=str)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'type': 'heartbeat'})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if queue in _sse_queues:
                _sse_queues.remove(queue)

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.post("/api/crewai/approve")
async def approve_crewai_build(request: Request):
    """Record human approval decision."""
    try:
        body = await request.json()
    except Exception:
        body = {}

    decision = body.get("decision", "").upper()
    if decision not in ("ALLOW", "DENY"):
        raise HTTPException(400, "decision must be 'ALLOW' or 'DENY'")

    decision_data = {
        "decision": decision,
        "reason": body.get("reason", ""),
        "reviewer": body.get("reviewer", "api_user"),
        "timestamp": datetime.now().isoformat(),
    }

    approval_file = ARTIFACTS_DIR / "approval_decision.json"
    approval_file.write_text(
        json.dumps(decision_data, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    _broadcast_sse({"type": "approval", **decision_data})
    return JSONResponse({"status": "recorded", **decision_data})


@router.get("/api/crewai/artifacts")
async def list_crewai_artifacts():
    """List all CrewAI pipeline artifacts."""
    ARTIFACTS_DIR.mkdir(parents=True, exist_ok=True)
    files = []
    for p in sorted(ARTIFACTS_DIR.iterdir()):
        if p.is_file() and p.suffix in (".json", ".txt"):
            files.append({
                "name": p.name,
                "size": p.stat().st_size,
                "modified": datetime.fromtimestamp(p.stat().st_mtime).isoformat(),
            })
    return JSONResponse({"artifacts": files})


@router.get("/api/crewai/artifacts/{name}")
async def get_crewai_artifact(name: str):
    """Return a single CrewAI artifact's contents."""
    p = ARTIFACTS_DIR / name
    if not p.exists() or not p.is_file():
        raise HTTPException(404, f"Artifact not found: {name}")

    content = p.read_text(encoding="utf-8", errors="replace")

    if p.suffix == ".json":
        try:
            return JSONResponse(content=json.loads(content))
        except json.JSONDecodeError:
            pass

    return JSONResponse({"name": name, "content": content})
"""
End of crewai route module.
"""
