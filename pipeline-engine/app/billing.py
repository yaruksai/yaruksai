# pipeline-engine/app/billing.py
"""
YARUKSAİ — Billing & Usage Metering Skeleton
═════════════════════════════════════════════
Credit-based system for SaaS monetization.
"""

import os
import time
import sqlite3
from pathlib import Path
from typing import Optional, Dict, Any
from datetime import datetime, timedelta

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from app.auth_jwt import get_current_user

router = APIRouter(prefix="/api/billing", tags=["billing"])

ADMIN_DB = Path(os.getenv("ADMIN_DB", "/app/data/admin.db"))

# Plan limits (credits per month)
PLAN_LIMITS = {
    "starter":    1_000,
    "business":  50_000,
    "enterprise": 999_999_999,  # effectively unlimited
}

# Credit cost per action
CREDIT_COSTS = {
    "pipeline_run": 10,
    "certificate_pdf": 2,
    "api_call": 1,
}


def _billing_db() -> sqlite3.Connection:
    """Get DB connection with billing tables initialized."""
    ADMIN_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ADMIN_DB))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS credits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id INTEGER UNIQUE NOT NULL,
            balance INTEGER NOT NULL DEFAULT 1000,
            plan_limit INTEGER NOT NULL DEFAULT 1000,
            period_start REAL NOT NULL,
            period_end REAL NOT NULL,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS usage_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id INTEGER NOT NULL,
            action TEXT NOT NULL,
            credits_used INTEGER NOT NULL DEFAULT 0,
            run_id TEXT DEFAULT '',
            metadata TEXT DEFAULT '{}',
            created_at REAL NOT NULL
        );
    """)
    conn.commit()
    return conn


def ensure_credits(org_id: int, plan: str = "starter") -> Dict[str, Any]:
    """Ensure credit record exists for org. Creates if missing."""
    conn = _billing_db()
    try:
        row = conn.execute("SELECT * FROM credits WHERE org_id=?", (org_id,)).fetchone()
        if row:
            return dict(row)

        now = time.time()
        limit = PLAN_LIMITS.get(plan, 1000)
        # Period: 30 days from creation
        period_end = now + (30 * 24 * 3600)
        conn.execute(
            "INSERT INTO credits (org_id, balance, plan_limit, period_start, period_end, created_at) VALUES (?,?,?,?,?,?)",
            (org_id, limit, limit, now, period_end, now))
        conn.commit()
        return {
            "org_id": org_id, "balance": limit, "plan_limit": limit,
            "period_start": now, "period_end": period_end
        }
    finally:
        conn.close()


def deduct_credit(org_id: int, action: str, run_id: str = "") -> Dict[str, Any]:
    """
    Deduct credits for an action. Returns balance info.
    Raises HTTPException(402) if insufficient credits.
    """
    cost = CREDIT_COSTS.get(action, 1)
    conn = _billing_db()
    try:
        row = conn.execute("SELECT * FROM credits WHERE org_id=?", (org_id,)).fetchone()
        if not row:
            # Auto-create for starter
            ensure_credits(org_id, "starter")
            row = conn.execute("SELECT * FROM credits WHERE org_id=?", (org_id,)).fetchone()

        if row["balance"] < cost:
            raise HTTPException(
                status_code=402,
                detail=f"Yetersiz kredi. Gerekli: {cost}, Mevcut: {row['balance']}"
            )

        new_balance = row["balance"] - cost
        conn.execute("UPDATE credits SET balance=? WHERE org_id=?", (new_balance, org_id))
        conn.execute(
            "INSERT INTO usage_log (org_id, action, credits_used, run_id, created_at) VALUES (?,?,?,?,?)",
            (org_id, action, cost, run_id, time.time()))
        conn.commit()

        return {"balance": new_balance, "deducted": cost, "action": action}
    finally:
        conn.close()


# ─── API Endpoints ─────────────────────────────────────────────

@router.get("/usage")
async def get_usage(request: Request, org_id: Optional[int] = None, days: int = 30) -> JSONResponse:
    """Get usage analytics for an organization."""
    user = get_current_user(request)
    target_org = org_id or user.get("org_id")
    if not target_org:
        return JSONResponse({"error": "org_id gerekli"}, status_code=400)

    conn = _billing_db()
    try:
        cutoff = time.time() - (days * 24 * 3600)

        # Credit balance
        credits_row = conn.execute("SELECT * FROM credits WHERE org_id=?", (target_org,)).fetchone()
        credits_info = dict(credits_row) if credits_row else {"balance": 0, "plan_limit": 0}

        # Usage breakdown by action
        usage_rows = conn.execute("""
            SELECT action, SUM(credits_used) as total_credits, COUNT(*) as count
            FROM usage_log WHERE org_id=? AND created_at>=?
            GROUP BY action ORDER BY total_credits DESC
        """, (target_org, cutoff)).fetchall()

        # Daily usage for chart
        daily_rows = conn.execute("""
            SELECT date(created_at, 'unixepoch') as day, SUM(credits_used) as daily_total
            FROM usage_log WHERE org_id=? AND created_at>=?
            GROUP BY day ORDER BY day ASC
        """, (target_org, cutoff)).fetchall()

        # Total usage this period
        total_used = conn.execute(
            "SELECT COALESCE(SUM(credits_used), 0) FROM usage_log WHERE org_id=? AND created_at>=?",
            (target_org, cutoff)).fetchone()[0]

        return JSONResponse({
            "org_id": target_org,
            "credits": credits_info,
            "total_used": total_used,
            "usage_by_action": [dict(r) for r in usage_rows],
            "daily_usage": [dict(r) for r in daily_rows],
            "period_days": days,
        })
    finally:
        conn.close()


@router.get("/balance")
async def get_balance(request: Request, org_id: Optional[int] = None) -> JSONResponse:
    """Get current credit balance."""
    user = get_current_user(request)
    target_org = org_id or user.get("org_id")
    if not target_org:
        return JSONResponse({"error": "org_id gerekli"}, status_code=400)

    info = ensure_credits(target_org)
    return JSONResponse({"balance": info["balance"], "plan_limit": info["plan_limit"]})
