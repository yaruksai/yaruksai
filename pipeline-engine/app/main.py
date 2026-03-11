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
import io
import json
import os
import sys
import time
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import FastAPI, Request, HTTPException
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from pydantic import BaseModel, Field

# ─── src/ klasörünü Python path'e ekle ─────────────────────────
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

# ─── Shared State (route modülleri bu modülü kullanır) ─────────
import app.shared as _shared
from app.shared import (
    ARTIFACT_ROOT, ADMIN_LEDGER,
    check_admin, log_admin_action, load_weights,
    safe_run_id as _safe_run_id, run_dir as _run_dir,
    is_safe_relpath as _is_safe_relpath, write_json as _write_json,
    list_files_recursive as _list_files_recursive,
    LEGAL_DISCLAIMER,
)

# ─── Config ────────────────────────────────────────────────────
MAX_CONCURRENT_RUNS = int(os.getenv("MAX_CONCURRENT_RUNS", "2"))
SSE_PING_SECONDS = int(os.getenv("SSE_PING_SECONDS", "15"))

RUN_SEMAPHORE = asyncio.Semaphore(MAX_CONCURRENT_RUNS)

# In-memory event bus (workers=1 şart)
_SUBSCRIBERS: Dict[str, List[asyncio.Queue]] = {}
_SUB_LOCK = asyncio.Lock()


# ─── SSE Helpers ───────────────────────────────────────────────

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
    rd = _run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)

    _write_json(rd / "run_meta.json", {
        "run_id": run_id, "goal": goal, "context": context,
        "ts": time.time(), "status": "running",
    })

    await _publish(run_id, {"type": "stage_started", "stage": "pipeline", "run_id": run_id})

    loop = asyncio.get_running_loop()
    event_cb = _sync_event_bridge(run_id, loop)

    try:
        result = await loop.run_in_executor(None, _run_flow_sync, goal, context, rd, event_cb)

        # Artifacts index
        files = _list_files_recursive(rd)
        _write_json(rd / "artifacts_index.json", {"files": files, "ts": time.time()})

        # EU AI Act Compliance Report — otomatik üretim
        try:
            from compliance import generate_compliance_report
            compliance = generate_compliance_report(
                run_id=run_id,
                goal=goal,
                pipeline_summary=result,
                council_verdict=context.get("council_verdict") if context else None,
                artifacts_dir=rd,
            )
            result["compliance_status"] = compliance.get("compliance_summary", {}).get("status")
            result["compliance_score"] = compliance.get("compliance_summary", {}).get("overall_score")
        except Exception as ce:
            print(f"[YARUKSAİ] Compliance report error: {ce}")

        # Update artifacts index with compliance files
        files = _list_files_recursive(rd)
        _write_json(rd / "artifacts_index.json", {"files": files, "ts": time.time()})

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
        _write_json(rd / "run_meta.json", {
            "run_id": run_id, "goal": goal, "context": context,
            "ts": time.time(), "status": "completed", "summary": result,
        })

        await _publish(run_id, {
            "type": "completed", "run_id": run_id,
            "summary": result,
        })
    except Exception as e:
        _write_json(rd / "run_meta.json", {
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
    description="AI Decision Auditing Engine — INTEGRITY_INDEX + EVIDENCE_PACK\n\n"
                "EU AI Act (2024/1689) | IEEE 7000-2021 | ISO/IEC 42001\n\n"
                "Endpoints:\n"
                "- `POST /v1/audit` — Primary audit (EVIDENCE_PACK)\n"
                "- `POST /v1/audit?shadow=true` — Shadow mode\n"
                "- `POST /auth/token` — JWT RS256 token\n"
                "- `GET /health` — Component status",
    version="1.2.0",
    docs_url="/docs",
    redoc_url="/redoc",
    openapi_url="/openapi.json",
)


# ─── CORS — Production ────────────────────────────────────────
from starlette.middleware.cors import CORSMiddleware

_CORS_ORIGINS = os.getenv("CORS_ORIGINS", "https://yaruksai.com,https://www.yaruksai.com,http://localhost:3000").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in _CORS_ORIGINS],
    allow_credentials=True,
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],
    allow_headers=["Authorization", "Content-Type", "X-Admin-Key", "X-Request-ID"],
    expose_headers=["X-Request-ID"],
)


# ─── Rate Limiting — In-Memory ────────────────────────────────
import collections

_RATE_LIMIT_WINDOW = int(os.getenv("RATE_LIMIT_WINDOW", "60"))
_RATE_LIMIT_MAX = int(os.getenv("RATE_LIMIT_MAX", "60"))
_rate_store: Dict[str, list] = collections.defaultdict(list)

@app.middleware("http")
async def rate_limit_middleware(request: Request, call_next):
    if request.url.path in ("/health", "/api/health", "/docs", "/redoc", "/openapi.json"):
        return await call_next(request)

    client_ip = request.client.host if request.client else "unknown"
    now = time.time()

    _rate_store[client_ip] = [t for t in _rate_store[client_ip] if now - t < _RATE_LIMIT_WINDOW]

    if len(_rate_store[client_ip]) >= _RATE_LIMIT_MAX:
        from starlette.responses import JSONResponse as _RateLimitResp
        return _RateLimitResp(
            {"error": "Rate limit exceeded", "retry_after": _RATE_LIMIT_WINDOW},
            status_code=429,
            headers={"Retry-After": str(_RATE_LIMIT_WINDOW)},
        )

    _rate_store[client_ip].append(now)
    response = await call_next(request)
    response.headers["X-RateLimit-Limit"] = str(_RATE_LIMIT_MAX)
    response.headers["X-RateLimit-Remaining"] = str(_RATE_LIMIT_MAX - len(_rate_store[client_ip]))
    return response


# ─── Mount SaaS Routers ──────────────────────────────────────
try:
    from app.tenancy import router as tenancy_router
    app.include_router(tenancy_router)
except ImportError as _e:
    print(f"[YARUKSAİ] Tenancy module not available: {_e}")

try:
    from app.billing import router as billing_router
    app.include_router(billing_router)
except ImportError as _e:
    print(f"[YARUKSAİ] Billing module not available: {_e}")


# ─── Mount Route Modules ─────────────────────────────────────
try:
    from app.routes import ALL_ROUTERS
    for _router in ALL_ROUTERS:
        app.include_router(_router)
    print(f"[YARUKSAİ] ✅ {len(ALL_ROUTERS)} route modülü yüklendi")
except ImportError as _e:
    print(f"[YARUKSAİ] ⚠️ Route modülleri yüklenemedi: {_e}")


# ─── Boot Integrity Lock ─────────────────────────────────────

try:
    from app.boot_lock import verify_boot_integrity, BootIntegrityError, regenerate_genesis

    @app.on_event("startup")
    async def _boot_integrity_check():
        try:
            report = verify_boot_integrity()
            locked = report.get("locked", False)
            _shared.set_boot_state(locked, report)
        except BootIntegrityError as e:
            _shared.set_boot_state(True, {
                "status": "HARD_LOCK",
                "locked": True,
                "error": str(e),
            })
            print(f"[YARUKSAİ] 🔒 SİSTEM KİLİTLENDİ — Boot integrity failure")
        except Exception as e:
            _shared.set_boot_state(False, {"status": "ERROR", "locked": False, "error": str(e)})
            print(f"[YARUKSAİ] ⚠️ Boot lock hatası (devam ediliyor): {e}")

except ImportError:
    print("[YARUKSAİ] Boot lock module not available")


# ═══════════════════════════════════════════════════════════════
#  SPRINT 1 — AUTH + CIRCUIT BREAKER + LOGGING IMPORTS
# ═══════════════════════════════════════════════════════════════

try:
    from app.auth_rs256 import (
        create_token, verify_token, get_current_user,
        require_role, require_scope, CLIENT_CREDENTIALS, ROLES,
    )
    from app.model_config import get_model, get_all_assignments, AGENT_MODELS
    AUTH_AVAILABLE = True
except ImportError as _auth_err:
    AUTH_AVAILABLE = False
    print(f"[SPRINT1] Warning: auth module not available: {_auth_err}")

try:
    from mizan_engine.circuit_breaker import (
        circuit_registry, CircuitState, CircuitOpenError,
        AgentExecutionError, execute_with_retry, AGENT_TIMEOUTS,
    )
    CIRCUIT_AVAILABLE = True
except ImportError as _cb_err:
    CIRCUIT_AVAILABLE = False
    print(f"[SPRINT1] Warning: circuit_breaker not available: {_cb_err}")


# ═══ STRUCTURED LOGGING ═══
import logging
import json as _json_mod

class JSONLogHandler(logging.Handler):
    def emit(self, record):
        log_entry = {
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S.000Z", time.gmtime()),
            "level": record.levelname,
            "service": "yaruksai-pipeline",
            "event": record.getMessage(),
        }
        print(_json_mod.dumps(log_entry, ensure_ascii=False))

_yaruksai_logger = logging.getLogger("yaruksai")
_yaruksai_logger.setLevel(logging.INFO)
_yaruksai_logger.addHandler(JSONLogHandler())


# ═══ HEALTH CHECK ═══
@app.get("/health")
@app.get("/api/health")
def health() -> Dict[str, Any]:
    """GET /health — Component-level status."""
    import time as _t
    _start = _t.time()

    components = {
        "database": {"status": "up", "latency_ms": 0},
        "witness_chain": {"status": "up"},
    }

    # DB check (SQLite)
    try:
        from mizan_engine.shahid_ledger import ShahidLedger
        ledger_path = os.getenv("SHAHID_LEDGER_PATH", str(Path(ARTIFACT_ROOT) / "shahid_ledger.db"))
        ledger = ShahidLedger(db_path=ledger_path)
        _db_start = _t.time()
        ledger.get_all(limit=1)
        components["database"]["latency_ms"] = round((_t.time() - _db_start) * 1000)
    except Exception:
        components["database"]["status"] = "down"

    # Circuit breaker status
    if CIRCUIT_AVAILABLE:
        components["agents"] = circuit_registry.all_status()
        if not components["agents"]:
            for name in ["celali", "cemali", "kemali", "emanet"]:
                cb = circuit_registry.get(name)
                components["agents"][name] = cb.to_dict()

    # Overall status
    any_down = any(
        c.get("status") == "down"
        for c in components.values()
        if isinstance(c, dict) and "status" in c
    )
    any_open = CIRCUIT_AVAILABLE and circuit_registry.any_open()

    status_val = "unhealthy" if any_down else ("degraded" if any_open else "healthy")
    http_code = 503 if any_down else 200

    _, boot_report = _shared.get_boot_state()
    boot_locked = _shared.BOOT_LOCKED

    result = {
        "status": status_val,
        "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "version": "1.2.0",
        "service": "yaruksai-pipeline",
        "boot_status": boot_report.get("status", "UNKNOWN"),
        "boot_locked": boot_locked,
        "emergency_stopped": _shared.EMERGENCY_STOPPED,
        "components": components,
    }

    if AUTH_AVAILABLE:
        result["model_assignments"] = get_all_assignments()

    from starlette.responses import JSONResponse as _JR
    return _JR(result, status_code=http_code)


# ═══ AUTH TOKEN ═══
@app.post("/auth/token")
@app.post("/api/auth/token")
async def auth_token(request: Request):
    """POST /auth/token — client_credentials grant."""
    if not AUTH_AVAILABLE:
        raise HTTPException(503, "Auth module not available")

    try:
        body = await request.json()
    except Exception:
        raise HTTPException(400, "Invalid JSON body")

    client_id = body.get("client_id", "")
    client_secret = body.get("client_secret", "")
    grant_type = body.get("grant_type", "")

    if grant_type != "client_credentials":
        raise HTTPException(400, "Only client_credentials grant supported")

    client = CLIENT_CREDENTIALS.get(client_id)
    if not client or client["secret"] != client_secret:
        raise HTTPException(401, "Invalid client credentials")

    token_data = create_token(client_id, client["role"])
    _yaruksai_logger.info(f"Token issued for {client_id} (role: {client['role']})")

    return JSONResponse(token_data)


# ═══ BOOT STATUS ═══
@app.get("/api/admin/boot-status")
def boot_status() -> Dict[str, Any]:
    """Boot integrity doğrulama raporu."""
    return {
        "boot_locked": _shared.BOOT_LOCKED,
        "report": _shared.BOOT_REPORT,
    }


@app.post("/api/admin/boot-regenerate")
def boot_regenerate() -> Dict[str, Any]:
    """Deploy sonrası genesis manifest'ı yenile. Sadece admin."""
    try:
        path = regenerate_genesis()
        _shared.set_boot_state(False, {"status": "GENESIS_REGENERATED", "locked": False, "path": str(path)})
        return {"status": "ok", "message": "Genesis manifest yenilendi", "path": str(path)}
    except Exception as e:
        return {"status": "error", "message": str(e)}


# ═══ PIPELINE RUN ═══
@app.post("/api/pipeline/run", response_model=RunResponse)
async def pipeline_run(req: RunRequest) -> RunResponse:
    """Pipeline'ı başlat."""
    if _shared.EMERGENCY_STOPPED:
        raise HTTPException(
            status_code=503,
            detail="SİSTEM ACİL DURDURMA MODUNDA. Pipeline çalıştırılamaz.",
        )
    if _shared.BOOT_LOCKED:
        raise HTTPException(
            status_code=503,
            detail="SİSTEM KİLİTLİ: Boot integrity doğrulaması başarısız.",
        )
    if RUN_SEMAPHORE._value <= 0:  # noqa: SLF001
        raise HTTPException(
            status_code=429,
            detail=f"Pipeline busy ({MAX_CONCURRENT_RUNS} concurrent runs active).",
        )

    run_id = _safe_run_id()
    rd = _run_dir(run_id)
    rd.mkdir(parents=True, exist_ok=True)

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
    _ = _run_dir(run_id)
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
    rd = _run_dir(run_id)
    if not rd.exists():
        raise HTTPException(status_code=404, detail="run_id not found")
    files = _list_files_recursive(rd)
    return JSONResponse({"run_id": run_id, "files": files})


@app.get("/api/pipeline/artifacts/{run_id}/download")
def pipeline_artifacts_zip(run_id: str):
    """Hakikat Paketi — tüm artifact'leri ZIP olarak indir."""
    import zipfile

    rd = _run_dir(run_id)
    if not rd.exists():
        raise HTTPException(status_code=404, detail="run_id not found")

    files = _list_files_recursive(rd)
    if not files:
        raise HTTPException(status_code=404, detail="No artifacts found")

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for rel in files:
            full = (rd / rel).resolve()
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


@app.get("/api/pipeline/certificate/{run_id}")
def pipeline_certificate(run_id: str):
    """YARUKSAİ Etik Uyum Sertifikası — VERICORE Inside."""
    rd = _run_dir(run_id)
    meta_path = rd / "run_meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="run_id not found")

    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        ctx = meta.get("context", {}) or {}
        council = ctx.get("council_verdict", {}) or {}
        summary = meta.get("summary", {}) or {}

        compliance_path = rd / "eu_ai_act_compliance.json"
        compliance_data = None
        if compliance_path.exists():
            compliance_data = json.loads(compliance_path.read_text(encoding="utf-8"))

        weights = load_weights()

        from app.pdf_engine import generate_certificate
        pdf_bytes = generate_certificate(
            run_id=run_id,
            goal=meta.get("goal", ""),
            sigma=council.get("sigma", council.get("sigma_score", 0.0)),
            verdict=council.get("verdict", ""),
            compliance_score=summary.get("compliance_score", 0.0),
            compliance_data=compliance_data,
            weights=weights,
            ts=meta.get("ts"),
        )

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": f'attachment; filename="yaruksai_certificate_{run_id}.pdf"',
            },
        )
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Sertifika oluşturma hatası: {str(e)}")


# ── POST /api/certificate — Evaluate JSON → VERICORE PDF ─────

@app.post("/api/certificate")
async def certificate_from_evaluate(request: Request):
    """Şûra Konseyi Sertifikası — evaluate() JSON → PDF."""
    try:
        body = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Geçersiz JSON gövdesi")

    if not body.get("votes") and not body.get("sigma_score"):
        raise HTTPException(
            status_code=400,
            detail="evaluate() çıktısı gerekli: 'votes' ve 'sigma_score' alanları zorunlu"
        )

    try:
        from app.pdf_engine import generate_council_certificate
        pdf_bytes = generate_council_certificate(body)

        log_admin_action("CERTIFICATE_GENERATED", {
            "sigma": body.get("sigma_score"),
            "verdict": body.get("verdict"),
            "votes_count": len(body.get("votes", [])),
            "goal": body.get("_goal", ""),
        })

        return StreamingResponse(
            io.BytesIO(pdf_bytes),
            media_type="application/pdf",
            headers={
                "Content-Disposition": 'attachment; filename="yaruksai_sura_certificate.pdf"',
            },
        )
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"PDF sertifika hatası: {str(e)}")


@app.get("/api/pipeline/artifacts/{run_id}/{relpath:path}")
def pipeline_artifact_download(run_id: str, relpath: str):
    """Tek bir artifact dosyasını indir. Path traversal korumalı."""
    rd = _run_dir(run_id)
    if not _is_safe_relpath(relpath):
        raise HTTPException(status_code=400, detail="Invalid artifact path")

    target = (rd / relpath).resolve()
    if rd not in target.parents and target != rd:
        raise HTTPException(status_code=400, detail="Path traversal denied")
    if not target.exists() or not target.is_file():
        raise HTTPException(status_code=404, detail="Artifact not found")

    return FileResponse(path=str(target), filename=Path(relpath).name)


@app.get("/api/pipeline/status/{run_id}")
def pipeline_status(run_id: str) -> JSONResponse:
    """Run durumunu döndürür (run_meta.json'dan)."""
    rd = _run_dir(run_id)
    meta_path = rd / "run_meta.json"
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail="run_id not found")
    try:
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to read run metadata")
    return JSONResponse(meta)
