"""
app/routes/emergency.py — Emergency Kill-Switch
═════════════════════════════════════════════════

Extracted from main.py (L1991-2072, ~80 lines).
"""

import time
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.shared import check_admin, log_admin_action, get_emergency_state, set_emergency_state

router = APIRouter(tags=["emergency"])


@router.post("/api/admin/emergency-stop")
async def emergency_stop(request: Request):
    """
    Acil Durdurma — tüm aktif pipeline'ları durdurur.
    Shahid Ledger'a EMERGENCY_STOP kaydı işler.
    Yeniden başlatmak için founder onayı gerekir.
    """
    check_admin(request)

    info = {
        "stopped_at": time.time(),
        "stopped_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "stopped_by": request.headers.get("X-Founder-Id", "founder"),
        "reason": "EMERGENCY_STOP triggered by admin",
    }
    set_emergency_state(True, info)

    # Log to Shahid Ledger
    log_admin_action("EMERGENCY_STOP", {
        "founder_id": info["stopped_by"],
        "timestamp": info["stopped_at_iso"],
        "severity": "CRITICAL",
    })

    return JSONResponse({
        "status": "EMERGENCY_STOPPED",
        "message": "Tüm pipeline'lar durduruldu. Yeniden başlatmak için /api/admin/emergency-resume kullanın.",
        "info": info,
    })


@router.post("/api/admin/emergency-resume")
async def emergency_resume(request: Request):
    """
    Acil durdurma sonrası sistemi yeniden başlat.
    Sadece founder onayı ile çalışır.
    """
    check_admin(request)

    stopped, stop_info = get_emergency_state()
    if not stopped:
        return JSONResponse({"status": "NOT_STOPPED", "message": "Sistem zaten çalışıyor."})

    resume_info = {
        "resumed_at": time.time(),
        "resumed_at_iso": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "resumed_by": request.headers.get("X-Founder-Id", "founder"),
        "was_stopped_at": stop_info.get("stopped_at_iso"),
    }

    log_admin_action("EMERGENCY_RESUME", {
        "founder_id": resume_info["resumed_by"],
        "timestamp": resume_info["resumed_at_iso"],
        "downtime_seconds": round(resume_info["resumed_at"] - stop_info.get("stopped_at", 0), 1),
    })

    set_emergency_state(False)

    return JSONResponse({
        "status": "RESUMED",
        "message": "Sistem yeniden başlatıldı.",
        "info": resume_info,
    })


@router.get("/api/admin/emergency-status")
async def emergency_status():
    """Acil durdurma durumu."""
    stopped, info = get_emergency_state()
    return JSONResponse({
        "emergency_stopped": stopped,
        "info": info if stopped else None,
    })
