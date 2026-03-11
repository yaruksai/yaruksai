"""
app/routes/admin_os.py — Admin OS: CRM, Content Pool, Site CRUD
═══════════════════════════════════════════════════════════════════

Extracted from main.py (L1172-1486, ~315 lines).
"""

import json
import os
import time
import sqlite3
from pathlib import Path
from typing import Any, Dict

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from app.shared import check_admin, log_admin_action

router = APIRouter(tags=["admin-os"])

# ─── Admin DB ──────────────────────────────────────────────────
ADMIN_DB = Path(os.getenv("ADMIN_DB", "/app/data/admin.db"))


def _admin_db() -> sqlite3.Connection:
    """Admin OS veritabanı bağlantısı — tablolar yoksa oluşturur."""
    ADMIN_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ADMIN_DB))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS crm_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_name TEXT NOT NULL DEFAULT '',
            from_email TEXT NOT NULL DEFAULT '',
            subject TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            reply TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'unread',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            customer TEXT NOT NULL DEFAULT '',
            product TEXT NOT NULL DEFAULT '',
            amount REAL NOT NULL DEFAULT 0,
            status TEXT NOT NULL DEFAULT 'pending',
            notes TEXT DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS content_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL DEFAULT '',
            content_type TEXT NOT NULL DEFAULT 'article',
            body TEXT NOT NULL DEFAULT '',
            status TEXT NOT NULL DEFAULT 'pending',
            platform TEXT DEFAULT '',
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS site_content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'service',
            title TEXT NOT NULL DEFAULT '',
            body TEXT NOT NULL DEFAULT '',
            price REAL DEFAULT 0,
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
    """)
    conn.commit()
    return conn


# ── CRM: Messages ─────────────────────────────────────────────

@router.get("/api/crm/messages")
def crm_list_messages(status: str = "", page: int = 1, per_page: int = 20) -> JSONResponse:
    conn = _admin_db()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM crm_messages WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, per_page, (page - 1) * per_page)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM crm_messages WHERE status=?", (status,)).fetchone()[0]
        else:
            rows = conn.execute(
                "SELECT * FROM crm_messages ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (per_page, (page - 1) * per_page)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM crm_messages").fetchone()[0]
        return JSONResponse({"messages": [dict(r) for r in rows], "total": total, "page": page})
    finally:
        conn.close()


@router.post("/api/crm/messages")
async def crm_create_message(request: Request) -> JSONResponse:
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz"}, status_code=403)
    body = await request.json()
    conn = _admin_db()
    try:
        conn.execute(
            "INSERT INTO crm_messages (from_name, from_email, subject, body, status, created_at) VALUES (?,?,?,?,?,?)",
            (body.get("from_name", ""), body.get("from_email", ""), body.get("subject", ""),
             body.get("body", ""), "unread", time.time()))
        conn.commit()
        log_admin_action("CRM_MESSAGE_CREATED", {"subject": body.get("subject", "")})
        return JSONResponse({"status": "created"})
    finally:
        conn.close()


@router.post("/api/crm/messages/{msg_id}/reply")
async def crm_reply_message(msg_id: int, request: Request) -> JSONResponse:
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz"}, status_code=403)
    body = await request.json()
    reply_text = body.get("reply", "")
    conn = _admin_db()
    try:
        conn.execute("UPDATE crm_messages SET reply=?, status='replied' WHERE id=?", (reply_text, msg_id))
        conn.commit()
        log_admin_action("CRM_REPLY", {"message_id": msg_id, "reply_preview": reply_text[:100]})
        return JSONResponse({"status": "replied", "message_id": msg_id})
    finally:
        conn.close()


# ── CRM: Orders ───────────────────────────────────────────────

@router.get("/api/crm/orders")
def crm_list_orders(status: str = "", page: int = 1, per_page: int = 20) -> JSONResponse:
    conn = _admin_db()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM orders WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, per_page, (page - 1) * per_page)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM orders WHERE status=?", (status,)).fetchone()[0]
        else:
            rows = conn.execute(
                "SELECT * FROM orders ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (per_page, (page - 1) * per_page)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM orders").fetchone()[0]
        return JSONResponse({"orders": [dict(r) for r in rows], "total": total, "page": page})
    finally:
        conn.close()


@router.post("/api/crm/orders")
async def crm_create_order(request: Request) -> JSONResponse:
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz"}, status_code=403)
    body = await request.json()
    conn = _admin_db()
    try:
        conn.execute(
            "INSERT INTO orders (customer, product, amount, status, notes, created_at) VALUES (?,?,?,?,?,?)",
            (body.get("customer", ""), body.get("product", ""), body.get("amount", 0),
             "pending", body.get("notes", ""), time.time()))
        conn.commit()
        log_admin_action("ORDER_CREATED", {"customer": body.get("customer", ""), "product": body.get("product", "")})
        return JSONResponse({"status": "created"})
    finally:
        conn.close()


@router.put("/api/crm/orders/{order_id}")
async def crm_update_order(order_id: int, request: Request) -> JSONResponse:
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz"}, status_code=403)
    body = await request.json()
    new_status = body.get("status", "pending")
    if new_status not in ("pending", "approved", "cancelled"):
        return JSONResponse({"error": "Geçersiz durum"}, status_code=400)
    conn = _admin_db()
    try:
        conn.execute("UPDATE orders SET status=?, notes=? WHERE id=?",
                     (new_status, body.get("notes", ""), order_id))
        conn.commit()
        log_admin_action("ORDER_STATUS_CHANGE", {"order_id": order_id, "new_status": new_status})
        return JSONResponse({"status": "updated", "order_id": order_id})
    finally:
        conn.close()


# ── Content Pool ──────────────────────────────────────────────

@router.get("/api/content/pool")
def content_list(status: str = "", page: int = 1, per_page: int = 20) -> JSONResponse:
    conn = _admin_db()
    try:
        if status:
            rows = conn.execute(
                "SELECT * FROM content_pool WHERE status=? ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (status, per_page, (page - 1) * per_page)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM content_pool WHERE status=?", (status,)).fetchone()[0]
        else:
            rows = conn.execute(
                "SELECT * FROM content_pool ORDER BY created_at DESC LIMIT ? OFFSET ?",
                (per_page, (page - 1) * per_page)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM content_pool").fetchone()[0]
        return JSONResponse({"content": [dict(r) for r in rows], "total": total, "page": page})
    finally:
        conn.close()


@router.post("/api/content/pool")
async def content_create(request: Request) -> JSONResponse:
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz"}, status_code=403)
    body = await request.json()
    conn = _admin_db()
    try:
        conn.execute(
            "INSERT INTO content_pool (title, content_type, body, status, platform, created_at) VALUES (?,?,?,?,?,?)",
            (body.get("title", ""), body.get("content_type", "article"),
             body.get("body", ""), "pending", body.get("platform", ""), time.time()))
        conn.commit()
        log_admin_action("CONTENT_CREATED", {"title": body.get("title", "")})
        return JSONResponse({"status": "created"})
    finally:
        conn.close()


@router.put("/api/content/pool/{content_id}")
async def content_update(content_id: int, request: Request) -> JSONResponse:
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz"}, status_code=403)
    body = await request.json()
    new_status = body.get("status", "pending")
    conn = _admin_db()
    try:
        if "body" in body:
            conn.execute("UPDATE content_pool SET body=?, status=? WHERE id=?",
                         (body["body"], new_status, content_id))
        else:
            conn.execute("UPDATE content_pool SET status=? WHERE id=?",
                         (new_status, content_id))
        conn.commit()
        log_admin_action("CONTENT_STATUS_CHANGE", {"content_id": content_id, "new_status": new_status})
        return JSONResponse({"status": "updated", "content_id": content_id})
    finally:
        conn.close()


# ── Site CRUD ─────────────────────────────────────────────────

@router.get("/api/site/content")
def site_list(category: str = "", page: int = 1, per_page: int = 20) -> JSONResponse:
    conn = _admin_db()
    try:
        if category:
            rows = conn.execute(
                "SELECT * FROM site_content WHERE category=? ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (category, per_page, (page - 1) * per_page)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM site_content WHERE category=?", (category,)).fetchone()[0]
        else:
            rows = conn.execute(
                "SELECT * FROM site_content ORDER BY updated_at DESC LIMIT ? OFFSET ?",
                (per_page, (page - 1) * per_page)).fetchall()
            total = conn.execute("SELECT COUNT(*) FROM site_content").fetchone()[0]
        return JSONResponse({"items": [dict(r) for r in rows], "total": total, "page": page})
    finally:
        conn.close()


@router.post("/api/site/content")
async def site_create(request: Request) -> JSONResponse:
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz"}, status_code=403)
    body = await request.json()
    now = time.time()
    conn = _admin_db()
    try:
        conn.execute(
            "INSERT INTO site_content (category, title, body, price, is_active, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
            (body.get("category", "service"), body.get("title", ""), body.get("body", ""),
             body.get("price", 0), 1, now, now))
        conn.commit()
        log_admin_action("SITE_CONTENT_CREATED", {"title": body.get("title", ""), "category": body.get("category", "")})
        return JSONResponse({"status": "created"})
    finally:
        conn.close()


@router.put("/api/site/content/{item_id}")
async def site_update(item_id: int, request: Request) -> JSONResponse:
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz"}, status_code=403)
    body = await request.json()
    conn = _admin_db()
    try:
        fields, vals = [], []
        for k in ("title", "body", "price", "is_active", "category"):
            if k in body:
                fields.append(f"{k}=?")
                vals.append(body[k])
        if fields:
            fields.append("updated_at=?")
            vals.append(time.time())
            vals.append(item_id)
            conn.execute(f"UPDATE site_content SET {','.join(fields)} WHERE id=?", vals)
            conn.commit()
        log_admin_action("SITE_CONTENT_UPDATED", {"item_id": item_id, "fields": list(body.keys())})
        return JSONResponse({"status": "updated", "item_id": item_id})
    finally:
        conn.close()


@router.delete("/api/site/content/{item_id}")
async def site_delete(item_id: int, request: Request) -> JSONResponse:
    if not check_admin(request):
        return JSONResponse({"error": "Yetkisiz"}, status_code=403)
    conn = _admin_db()
    try:
        row = conn.execute("SELECT title, category FROM site_content WHERE id=?", (item_id,)).fetchone()
        conn.execute("DELETE FROM site_content WHERE id=?", (item_id,))
        conn.commit()
        log_admin_action("SITE_CONTENT_DELETED", {
            "item_id": item_id,
            "title": row["title"] if row else "unknown",
            "category": row["category"] if row else "unknown"
        })
        return JSONResponse({"status": "deleted", "item_id": item_id})
    finally:
        conn.close()
