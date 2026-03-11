"""
app/routes/pipeline.py — Pipeline Management, Memory, Weights, Admin
═════════════════════════════════════════════════════════════════════

Extracted from main.py (L803-1224, ~420 lines).
"""

import asyncio
import json
import os
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.shared import (
    ARTIFACT_ROOT, ADMIN_LEDGER,
    check_admin, log_admin_action, load_weights, save_weights,
    run_dir, write_json, list_files_recursive,
    DEFAULT_WEIGHTS, MINIMUM_WEIGHTS,
)

router = APIRouter(tags=["pipeline-management"])

# Self-Termination ihlal sayacı
_WEIGHT_VIOLATIONS: Dict[str, int] = {}


# ── Memory Endpoints ──────────────────────────────────────────

@router.get("/api/memory/stats")
def memory_stats() -> JSONResponse:
    """Kolektif hafıza istatistikleri."""
    try:
        from memory import get_memory_stats
        stats = get_memory_stats()
        return JSONResponse(stats)
    except Exception as e:
        return JSONResponse({"error": str(e), "total_memories": 0})


@router.get("/api/memory/search")
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


@router.get("/api/memory/list")
def memory_list(q: str = "", page: int = 1, per_page: int = 20) -> JSONResponse:
    """Kolektif hafıza kayıtlarını listele, ARA, sayfalandır."""
    try:
        from memory import get_db_path
        import sqlite3
        db = get_db_path()
        if not Path(db).exists():
            return JSONResponse({"records": [], "total": 0, "page": page})

        conn = sqlite3.connect(db)
        conn.row_factory = sqlite3.Row

        if q:
            rows = conn.execute(
                "SELECT * FROM memories WHERE goal LIKE ? OR verdict LIKE ? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (f"%{q}%", f"%{q}%", per_page, (page - 1) * per_page)
            ).fetchall()
            total = conn.execute(
                "SELECT COUNT(*) FROM memories WHERE goal LIKE ? OR verdict LIKE ?",
                (f"%{q}%", f"%{q}%")
            ).fetchone()[0]
        else:
            rows = conn.execute(
                "SELECT * FROM memories ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (per_page, (page - 1) * per_page)
            ).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM memories").fetchone()[0]

        conn.close()
        records = [dict(r) for r in rows]
        return JSONResponse({"records": records, "total": total, "page": page, "per_page": per_page})
    except Exception as e:
        return JSONResponse({"records": [], "total": 0, "error": str(e)})


@router.post("/api/memory/seed-case-study")
async def seed_case_study(request: Request) -> JSONResponse:
    """Tarihsel vaka çalışmalarını Kolektif Hafıza'ya 'Değişmez Referans' olarak yükle."""
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz erişim"}, status_code=403)

    body = await request.json()
    case_file = body.get("case_file", "amazon_hiring_bias.json")

    # Case study dosyasını bul
    case_dir = Path(__file__).resolve().parent.parent.parent / "data" / "case_studies"
    case_path = case_dir / case_file

    if not case_path.exists():
        available = [f.name for f in case_dir.glob("*.json")] if case_dir.exists() else []
        return JSONResponse({
            "error": f"Case study '{case_file}' bulunamadı",
            "available": available,
        }, status_code=404)

    try:
        case_data = json.loads(case_path.read_text(encoding="utf-8"))

        from memory import store_memory
        sigma_calc = case_data.get("sigma_calculation", {})
        subject = case_data.get("subject", {})

        memory_id = store_memory(
            run_id=f"case-study-{case_data.get('case_study_id', 'unknown')}",
            goal=case_data.get("yaruksai_pipeline_input", {}).get("goal", "Historical Case Study"),
            sigma=sigma_calc.get("weighted_sigma", 0.0),
            verdict=sigma_calc.get("verdict", "UNKNOWN"),
            compliance_score=0.0,
            risk_level=sigma_calc.get("risk_classification", "UNKNOWN"),
            final_decision=sigma_calc.get("verdict", "UNKNOWN"),
            summary=(
                f"[HISTORICAL CASE] {subject.get('company', 'Unknown')} — "
                f"{subject.get('system_name', 'Unknown System')} ({subject.get('period', '')}). "
                f"σ={sigma_calc.get('weighted_sigma', 0):.4f}, "
                f"EU AI Act: {sigma_calc.get('eu_ai_act_mapping', 'N/A')}. "
                f"Root cause: {case_data.get('root_cause_analysis', {}).get('primary', 'N/A')}"
            ),
        )

        log_admin_action("CASE_STUDY_SEEDED", {
            "case_id": case_data.get("case_study_id"),
            "memory_id": memory_id,
            "sigma": sigma_calc.get("weighted_sigma"),
            "verdict": sigma_calc.get("verdict"),
            "type": "IMMUTABLE_REFERENCE",
        })

        return JSONResponse({
            "status": "seeded",
            "memory_id": memory_id,
            "case_id": case_data.get("case_study_id"),
            "sigma": sigma_calc.get("weighted_sigma"),
            "verdict": sigma_calc.get("verdict"),
            "message": f"'{subject.get('company')}' vakası Kolektif Hafıza'ya değişmez referans olarak kaydedildi.",
        })

    except Exception as e:
        return JSONResponse({"error": str(e)}, status_code=500)


# ── Pipeline Run List ─────────────────────────────────────────

@router.get("/api/pipeline/runs")
def pipeline_runs() -> JSONResponse:
    """Tüm pipeline run'larını listele (admin dashboard için)."""
    runs = []
    artifacts = ARTIFACT_ROOT
    if not artifacts.exists():
        return JSONResponse({"runs": [], "total": 0})

    for run_d in sorted(artifacts.iterdir(), reverse=True):
        if not run_d.is_dir() or not run_d.name.startswith("run_"):
            continue
        meta_path = run_d / "run_meta.json"
        if not meta_path.exists():
            continue
        try:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            ctx = meta.get("context", {}) or {}
            council = ctx.get("council_verdict", {}) or {}
            summary = meta.get("summary", {}) or {}
            runs.append({
                "run_id": meta.get("run_id", run_d.name),
                "goal": meta.get("goal", ""),
                "sigma": council.get("sigma", council.get("sigma_score", 0)),
                "verdict": council.get("verdict", ""),
                "status": meta.get("status", "unknown"),
                "compliance_score": summary.get("compliance_score"),
                "ts": meta.get("ts", 0),
            })
        except Exception:
            continue

    return JSONResponse({"runs": runs[:50], "total": len(runs)})


# ── Agent Weight Controller ───────────────────────────────────

@router.get("/api/config/weights")
def get_weights() -> JSONResponse:
    """7 prensibin mevcut ağırlıklarını döndür."""
    return JSONResponse(load_weights())


@router.post("/api/config/weights")
async def set_weights(request: Request) -> JSONResponse:
    """7 prensibin ağırlıklarını güncelle. DEMİR NİZAM — minimum eşikler korunur."""
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz erişim"}, status_code=403)

    body = await request.json()
    current = load_weights()

    # 🛡️ Self-Termination kontrolü — minimum eşik ihlali
    violations = []
    for key in current:
        if key in body:
            val = body[key]
            raw_w = float(val) if isinstance(val, (int, float)) else float(val.get("w", 0)) if isinstance(val, dict) else 0
            min_w = MINIMUM_WEIGHTS.get(key, 0.0)
            if raw_w < min_w:
                violations.append({
                    "principle": key,
                    "requested": round(raw_w, 4),
                    "minimum": min_w,
                    "label": current[key].get("label", key),
                })

    if violations:
        source_ip = request.client.host if request.client else "unknown"
        log_admin_action("WEIGHT_VIOLATION_ATTEMPT", {
            "violations": violations,
            "source_ip": source_ip,
            "severity": "CRITICAL",
        })

        _WEIGHT_VIOLATIONS[source_ip] = _WEIGHT_VIOLATIONS.get(source_ip, 0) + 1
        vcount = _WEIGHT_VIOLATIONS[source_ip]

        if vcount >= 3:
            log_admin_action("SELF_TERMINATION_TRIGGERED", {
                "source_ip": source_ip,
                "total_violations": vcount,
                "message": "3 ardışık minimum eşik ihlal girişimi — oturum sonlandırıldı",
            })
            return JSONResponse({
                "error": "SELF_TERMINATION",
                "message": "3 ardışık Mizan ihlal girişimi tespit edildi. Oturum güvenlik gerekçesiyle sonlandırıldı.",
                "violations": violations,
                "ledger_seal": "recorded",
            }, status_code=403)

        return JSONResponse({
            "error": "DEMIR_NIZAM_VIOLATION",
            "message": f"Aşağıdaki prensipler minimum eşiğin altına çekilemez. İhlal girişimi Ledger'a kaydedildi. ({vcount}/3 uyarı)",
            "violations": violations,
            "warning": f"{3 - vcount} deneme sonra oturum otomatik sonlandırılacak.",
        }, status_code=400)

    # Normal güncelleme
    for key in current:
        if key in body:
            val = body[key]
            if isinstance(val, (int, float)):
                current[key]["w"] = max(MINIMUM_WEIGHTS.get(key, 0.0), min(1.0, float(val)))
            elif isinstance(val, dict) and "w" in val:
                current[key]["w"] = max(MINIMUM_WEIGHTS.get(key, 0.0), min(1.0, float(val["w"])))

    # Normalize to sum=1
    total = sum(v["w"] for v in current.values())
    if total > 0:
        for v in current.values():
            v["w"] = round(v["w"] / total, 4)

    # Post-normalization check
    for key, minw in MINIMUM_WEIGHTS.items():
        if key in current and current[key]["w"] < minw:
            current[key]["w"] = minw

    save_weights(current)
    log_admin_action("WEIGHT_UPDATE", {
        "weights": {k: v["w"] for k, v in current.items()},
        "source": "admin_panel"
    })

    return JSONResponse({"status": "updated", "weights": current})


# ── Docker Log Stream (SSE) ──────────────────────────────────

@router.get("/api/logs/stream")
async def log_stream(request: Request) -> StreamingResponse:
    """Docker container loglarını canlı SSE stream olarak gönderir."""

    async def generate():
        proc = subprocess.Popen(
            ["docker", "logs", "-f", "--tail", "50", "yaruksai-pipeline"],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, bufsize=1
        )
        try:
            yield f"data: {json.dumps({'type':'connected','message':'Log stream bağlandı'})}\n\n"
            while True:
                if await request.is_disconnected():
                    break
                line = proc.stdout.readline()
                if line:
                    yield f"data: {json.dumps({'type':'log','line':line.strip()})}\n\n"
                else:
                    await asyncio.sleep(0.5)
        finally:
            proc.terminate()

    return StreamingResponse(generate(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",
    })


# ── Kurucu Müdahalesi (Founder Override) ──────────────────────

@router.post("/api/admin/override")
async def admin_override(request: Request) -> JSONResponse:
    """
    Kurucu yetki kullanımı: REJECT edilmiş kararı APPROVE'a çevirir.
    Ledger'a 'FOUNDER_OVERRIDE' olarak işlenir — şeffaf.
    """
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz erişim"}, status_code=403)

    body = await request.json()
    run_id_val = body.get("run_id")
    reason = body.get("reason", "Kurucu kararı")

    if not run_id_val:
        return JSONResponse({"error": "run_id gerekli"}, status_code=400)

    rd = run_dir(run_id_val)
    if not rd:
        return JSONResponse({"error": "Run bulunamadı"}, status_code=404)

    meta_path = rd / "run_meta.json"
    original_status = "unknown"
    if meta_path.exists():
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        original_status = meta.get("status", "unknown")
        meta["status"] = "approved_override"
        meta["founder_override"] = {
            "reason": reason,
            "original_status": original_status,
            "timestamp": time.time(),
        }
        write_json(meta_path, meta)

    log_admin_action("FOUNDER_OVERRIDE", {
        "run_id": run_id_val,
        "reason": reason,
        "original_status": original_status,
    })

    return JSONResponse({
        "status": "override_applied",
        "run_id": run_id_val,
        "message": f"Kurucu müdahalesi uygulandı: {reason}"
    })


# ── Admin Action Logger View ─────────────────────────────────

@router.get("/api/admin/ledger")
def get_admin_ledger(limit: int = 50) -> JSONResponse:
    """Admin eylem geçmişi — Shāhid Ledger."""
    if not ADMIN_LEDGER.exists():
        return JSONResponse({"entries": [], "total": 0})
    lines = ADMIN_LEDGER.read_text(encoding="utf-8").strip().split("\n")
    entries = []
    for line in reversed(lines[-limit:]):
        try:
            entries.append(json.loads(line))
        except Exception:
            continue
    return JSONResponse({"entries": entries, "total": len(lines)})
