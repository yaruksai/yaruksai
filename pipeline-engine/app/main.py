# pipeline-engine/app/main.py
"""
YARUKSAİ Pipeline — FastAPI Wrapper + SSE
─────────────────────────────────────────
Security:
  - No external ports (Docker network only)
  - Path traversal protection on artifact endpoints
  - Concurrency limiter (asyncio.Semaphore)
  - Builder never executes commands (prompt-only)
  - Single Uvicorn worker (SSE + in-memory event bus)
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# ─── src/ klasörünü Python path'e ekle ─────────────────────────
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# ─── Config ────────────────────────────────────────────────────
ARTIFACT_ROOT = Path(os.getenv("ARTIFACT_ROOT", "/app/artifacts")).resolve()
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "2"))
SSE_PING_SECONDS = int(os.getenv("SSE_PING_SECONDS", "15"))

RUN_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

# In-memory event bus (workers=1 şart)
_SUBSCRIBERS: Dict[str, List[asyncio.Queue]] = {}
_SUB_LOCK = asyncio.Lock()


# ─── Helpers ───────────────────────────────────────────────────

def _safe_run_id() -> str:
    return f"run_{uuid.uuid4().hex}"


def _is_safe_relpath(p: str) -> bool:
    """Path traversal koruması."""
    if not p or p.startswith("/") or "\\" in p:
        return False
    norm = Path(p)
    if any(part in ("..", "") for part in norm.parts):
        return False
    return True


def _run_dir(run_id: str) -> Path:
    """Validate run_id format and return resolved path."""
    if not re.fullmatch(r"run_[0-9a-f]{32}", run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format")
    d = (ARTIFACT_ROOT / run_id).resolve()
    if ARTIFACT_ROOT not in d.parents and d != ARTIFACT_ROOT:
        raise HTTPException(status_code=400, detail="Invalid run_id path")
    return d


async def _publish(run_id: str, event: Dict[str, Any]) -> None:
    async with _SUB_LOCK:
        queues = list(_SUBSCRIBERS.get(run_id, []))
    for q in queues:
        try:
            q.put_nowait(event)
        except asyncio.QueueFull:
            pass


async def _subscribe(run_id: str) -> asyncio.Queue:
    q: asyncio.Queue = asyncio.Queue(maxsize=200)
    async with _SUB_LOCK:
        _SUBSCRIBERS.setdefault(run_id, []).append(q)
    return q


async def _unsubscribe(run_id: str, q: asyncio.Queue) -> None:
    async with _SUB_LOCK:
        arr = _SUBSCRIBERS.get(run_id, [])
        if q in arr:
            arr.remove(q)
        if not arr and run_id in _SUBSCRIBERS:
            del _SUBSCRIBERS[run_id]


def _write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def _list_files_recursive(base: Path) -> List[str]:
    out: List[str] = []
    if not base.exists():
        return out
    for p in base.rglob("*"):
        if p.is_file():
            rel = p.relative_to(base).as_posix()
            if _is_safe_relpath(rel):
                out.append(rel)
    out.sort()
    return out


# ─── API Models ────────────────────────────────────────────────

class CouncilVerdict(BaseModel):
    """TS engine'den gelen 7AI Council verdict'i."""
    sigma: float = Field(..., ge=0.0, le=1.0)
    votes: Optional[list] = None
    seal: Optional[str] = None


class PipelineContext(BaseModel):
    user_id: Optional[str] = None
    source: Optional[str] = "api"
    council_verdict: Optional[CouncilVerdict] = None


class RunRequest(BaseModel):
    goal: str = Field(..., min_length=3, max_length=20000)
    context: Optional[PipelineContext] = None


class RunResponse(BaseModel):
    run_id: str
    status: str
    stream_url: str


# ─── CrewAI Runner Adapter ─────────────────────────────────────

def _sync_event_bridge(run_id: str, loop: asyncio.AbstractEventLoop):
    """
    Döndürdüğü callback, sync orchestrator thread'inden
    async event bus'a event gönderir.
    """
    def callback(event: Dict[str, Any]) -> None:
        event["run_id"] = run_id
        event["ts"] = time.time()
        try:
            asyncio.run_coroutine_threadsafe(_publish(run_id, event), loop).result(timeout=2)
        except Exception:
            pass
    return callback


def _run_flow_sync(
    goal: str,
    context: Dict[str, Any],
    run_dir: Path,
    event_callback,
) -> Dict[str, Any]:
    """
    CrewAI orchestrator'ı çağırır. Blocking — thread pool'da çalıştırılacak.
    Builder komut çalıştırmaz: prompt-level kilitli + subprocess/os.system yok.
    """
    from flows.orchestrator import run_six_stage_flow

    arch, audit, mizan, builder, post, final = run_six_stage_flow(
        project_goal=goal,
        artifacts_dir=run_dir,
        context=context,
        event_callback=event_callback,
    )

    return {
        "architect": arch[:500] if isinstance(arch, str) else str(arch)[:500],
        "auditor": audit[:500] if isinstance(audit, str) else str(audit)[:500],
        "mizan_score": mizan.get("mizan_score") if isinstance(mizan, dict) else None,
        "builder_status": builder.get("status") if isinstance(builder, dict) else None,
        "final_decision": final.get("decision") if isinstance(final, dict) else None,
    }


async def _background_run(run_id: str, goal: str, context: Dict[str, Any]) -> None:
    """Arka planda pipeline çalıştırır, SSE event'leri yayınlar."""
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    _write_json(run_dir / "run_meta.json", {
        "run_id": run_id, "goal": goal, "context": context,
        "ts": time.time(), "status": "running",
    })

    await _publish(run_id, {"type": "stage_started", "stage": "pipeline", "run_id": run_id})

    loop = asyncio.get_running_loop()
    event_cb = _sync_event_bridge(run_id, loop)

    try:
        result = await loop.run_in_executor(None, _run_flow_sync, goal, context, run_dir, event_cb)

        # Artifacts index
        files = _list_files_recursive(run_dir)
        _write_json(run_dir / "artifacts_index.json", {"files": files, "ts": time.time()})

        # EU AI Act Compliance Report — otomatik üretim
        try:
            from compliance import generate_compliance_report
            compliance = generate_compliance_report(
                run_id=run_id,
                goal=goal,
                pipeline_summary=result,
                council_verdict=context.get("council_verdict") if context else None,
                artifacts_dir=run_dir,
            )
            result["compliance_status"] = compliance.get("compliance_summary", {}).get("status")
            result["compliance_score"] = compliance.get("compliance_summary", {}).get("overall_score")
        except Exception as ce:
            print(f"[YARUKSAİ] Compliance report error: {ce}")

        # Update artifacts index with compliance files
        files = _list_files_recursive(run_dir)
        _write_json(run_dir / "artifacts_index.json", {"files": files, "ts": time.time()})

        # Kolektif Hafıza — pipeline sonucunu kaydet
        try:
            from memory import store_memory
            council_v = context.get("council_verdict", {}) if context else {}
            store_memory(
                run_id=run_id,
                goal=goal,
                sigma=council_v.get("sigma", council_v.get("sigma_score", 0.0)),
                verdict=council_v.get("verdict", ""),
                compliance_score=result.get("compliance_score", 0),
                risk_level=result.get("compliance_status", ""),
                final_decision=str(result.get("final_decision", "")),
                summary=str(result)[:500],
            )
        except Exception as me:
            print(f"[YARUKSAİ] Memory store error: {me}")

        # Final status
        _write_json(run_dir / "run_meta.json", {
            "run_id": run_id, "goal": goal, "context": context,
            "ts": time.time(), "status": "completed", "summary": result,
        })

        await _publish(run_id, {
            "type": "completed", "run_id": run_id,
            "summary": result,
        })
    except Exception as e:
        _write_json(run_dir / "run_meta.json", {
            "run_id": run_id, "goal": goal, "context": context,
            "ts": time.time(), "status": "failed", "error": str(e),
        })
        await _publish(run_id, {"type": "failed", "run_id": run_id, "error": str(e)})


async def _sse_stream(run_id: str) -> AsyncIterator[bytes]:
    """SSE event stream generator."""
    q = await _subscribe(run_id)
    last_ping = time.time()
    try:
        while True:
            now = time.time()
            if now - last_ping >= SSE_PING_SECONDS:
                last_ping = now
                yield b": ping\n\n"

            try:
                ev = await asyncio.wait_for(q.get(), timeout=1.0)
            except asyncio.TimeoutError:
                continue

            etype = ev.get("type", "message")
            data = json.dumps(ev, ensure_ascii=False)
            payload = f"event: {etype}\ndata: {data}\n\n".encode("utf-8")
            yield payload

            if etype in ("completed", "failed"):
                return
    finally:
        await _unsubscribe(run_id, q)


# ─── FastAPI App ───────────────────────────────────────────────

app = FastAPI(
    title="YARUKSAİ Pipeline Engine",
    description="6-stage CrewAI pipeline with SSE streaming",
    version="0.1.0",
)


@app.get("/health")
def health() -> Dict[str, Any]:
    return {
        "ok": True,
        "service": "yaruksai-pipeline",
        "artifact_root": str(ARTIFACT_ROOT),
        "max_concurrent": MAX_CONCURRENT_RUNS,
    }


@app.post("/api/pipeline/run", response_model=RunResponse)
async def pipeline_run(req: RunRequest) -> RunResponse:
    """Pipeline'ı başlat. Eşzamanlılık limiti aşılırsa 429 döner."""
    if RUN_SEMAPHORE._value <= 0:  # noqa: SLF001
        raise HTTPException(
            status_code=429,
            detail=f"Pipeline busy ({MAX_CONCURRENT_RUNS} concurrent runs active). Try again shortly.",
        )

    run_id = _safe_run_id()
    run_dir = _run_dir(run_id)
    run_dir.mkdir(parents=True, exist_ok=True)

    context = req.context.model_dump() if req.context else {}

    async def _run_with_semaphore() -> None:
        async with RUN_SEMAPHORE:
            await _background_run(run_id, req.goal, context)

    asyncio.create_task(_run_with_semaphore())

    return RunResponse(
        run_id=run_id,
        status="queued",
        stream_url=f"/api/pipeline/stream/{run_id}",
    )


@app.get("/api/pipeline/stream/{run_id}")
async def pipeline_stream(run_id: str) -> StreamingResponse:
    """SSE stream — stage event'lerini real-time olarak gönderir."""
    _ = _run_dir(run_id)  # validate format
    return StreamingResponse(
        _sse_stream(run_id),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


@app.get("/api/pipeline/artifacts/{run_id}")
def pipeline_artifacts(run_id: str) -> JSONResponse:
    """Run'a ait artifact listesini döndürür."""
    run_dir = _run_dir(run_id)
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run_id not found")
    files = _list_files_recursive(run_dir)
    return JSONResponse({"run_id": run_id, "files": files})


@app.get("/api/pipeline/artifacts/{run_id}/download")
def pipeline_artifacts_zip(run_id: str):
    """
    Hakikat Paketi — tüm artifact'leri ZIP olarak indir.
    Dosya adı: yaruksai_{run_id}.zip
    """
    import zipfile
    import io

    run_dir = _run_dir(run_id)
    if not run_dir.exists():
        raise HTTPException(status_code=404, detail="run_id not found")

    files = _list_files_recursive(run_dir)
    if not files:
        raise HTTPException(status_code=404, detail="No artifacts found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            full = (run_dir / rel).resolve()
            if full.exists() and full.is_file():
                zf.write(str(full), arcname=rel)

    buf.seek(0)

    return StreamingResponse(
        buf,
        media_type="application/zip",
        headers={
            "Content-Disposition": f'attachment; filename="yaruksai_{run_id}.zip"',
        },
    )


@app.get("/api/pipeline/artifacts/{run_id}/{relpath:path}")
def pipeline_artifact_download(run_id: str, relpath: str):
    """Tek bir artifact dosyasını indir. Path traversal korumalı."""
    run_dir = _run_dir(run_id)
    if not _is_safe_relpath(relpath):
        raise HTTPException(status_code=400, detail="Invalid artifact path")

    target = (run_dir / relpath).resolve()
    if run_dir not in target.parents and target != run_dir:
        raise HTTPException(status_code=400, detail="Path traversal denied")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    return FileResponse(path=str(target), filename=Path(relpath).name)


@app.get("/api/pipeline/status/{run_id}")
def pipeline_status(run_id: str) -> JSONResponse:
    """Run durumunu döndürür (run_meta.json'dan)."""
    run_dir = _run_dir(run_id)
    meta_path = run_dir / "run_meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="run_id not found")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read run metadata")
    return JSONResponse(meta)


@app.get("/api/memory/stats")
def memory_stats() -> JSONResponse:
    """Kolektif hafıza istatistikleri."""
    try:
        from memory import get_memory_stats
        stats = get_memory_stats()
        return JSONResponse(stats)
    except Exception as e:
        return JSONResponse({"error": str(e), "total_memories": 0})


@app.get("/api/memory/search")
def memory_search(q: str = "", top_k: int = 5) -> JSONResponse:
    """Benzer geçmiş kararları ara."""
    if not q:
        return JSONResponse({"results": [], "query": ""})
    try:
        from memory import recall_similar
        results = recall_similar(q, top_k=top_k)
        return JSONResponse({"query": q, "results": results, "count": len(results)})
    except Exception as e:
        return JSONResponse({"error": str(e), "results": []})
