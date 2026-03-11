"""
app/routes/user_auth.py — User Auth, Messaging, Project Management
═══════════════════════════════════════════════════════════════════

Extracted from main.py (L1541-1988, ~450 lines).
"""

import asyncio
import hashlib as _hashlib
import json
import random
import secrets
import sqlite3
import time
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, StreamingResponse

from app.shared import admin_db, check_admin, log_admin_action

router = APIRouter(tags=["user-auth"])


# ─── Auth DB Tables ───────────────────────────────────────────

def _init_auth_tables(conn: sqlite3.Connection):
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            display_name TEXT NOT NULL DEFAULT '',
            password_hash TEXT NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            avatar_color TEXT DEFAULT '#4ade80',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            token TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL,
            created_at REAL NOT NULL,
            expires_at REAL NOT NULL,
            FOREIGN KEY(user_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            sender_id INTEGER NOT NULL,
            recipient_id INTEGER DEFAULT NULL,
            channel TEXT NOT NULL DEFAULT 'general',
            body TEXT NOT NULL DEFAULT '',
            created_at REAL NOT NULL,
            FOREIGN KEY(sender_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS projects (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'active',
            owner_id INTEGER,
            created_at REAL NOT NULL,
            FOREIGN KEY(owner_id) REFERENCES users(id)
        );
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project_id INTEGER NOT NULL,
            title TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT NOT NULL DEFAULT 'todo',
            priority TEXT NOT NULL DEFAULT 'medium',
            assigned_to INTEGER DEFAULT NULL,
            due_date TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL,
            FOREIGN KEY(project_id) REFERENCES projects(id),
            FOREIGN KEY(assigned_to) REFERENCES users(id)
        );
    """)
    conn.commit()


def _auth_db() -> sqlite3.Connection:
    conn = admin_db()
    _init_auth_tables(conn)
    return conn


def _hash_pw(pw: str) -> str:
    return _hashlib.sha256(pw.encode()).hexdigest()


def _get_user_from_token(token: str) -> Optional[dict]:
    if not token:
        return None
    conn = _auth_db()
    try:
        sess = conn.execute(
            "SELECT user_id FROM sessions WHERE token=? AND expires_at>?",
            (token, time.time())).fetchone()
        if not sess:
            return None
        user = conn.execute("SELECT * FROM users WHERE id=? AND is_active=1", (sess["user_id"],)).fetchone()
        return dict(user) if user else None
    finally:
        conn.close()


# Ensure founder account exists
def _ensure_founder():
    conn = _auth_db()
    try:
        existing = conn.execute("SELECT id FROM users WHERE username='kurucu'").fetchone()
        if not existing:
            conn.execute(
                "INSERT INTO users (username, display_name, password_hash, role, avatar_color, created_at) VALUES (?,?,?,?,?,?)",
                ("kurucu", "Kurucu", _hash_pw("yaruksai2026"), "founder", "#4ade80", time.time()))
            conn.commit()
    finally:
        conn.close()

_founder_ensured = False

def _maybe_ensure_founder():
    global _founder_ensured
    if not _founder_ensured:
        try:
            _ensure_founder()
            _founder_ensured = True
        except Exception:
            pass  # DB not available in this environment


# ── Auth Endpoints ────────────────────────────────────────────

@router.post("/api/auth/login")
async def auth_login(request: Request) -> JSONResponse:
    _maybe_ensure_founder()
    body = await request.json()
    username = body.get("username", "").strip().lower()
    password = body.get("password", "")
    conn = _auth_db()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE username=? AND is_active=1",
            (username,)).fetchone()
        if not user or user["password_hash"] != _hash_pw(password):
            return JSONResponse({"error": "Geçersiz kullanıcı adı veya şifre"}, status_code=401)

        # Issue JWT token (SaaS upgrade)
        try:
            from app.auth_jwt import create_access_token
            jwt_token = create_access_token(
                user_id=user["id"],
                username=user["username"],
                role=user["role"],
                display_name=user["display_name"],
            )
        except ImportError:
            jwt_token = None

        # Legacy session (backward compat)
        session_token = secrets.token_hex(32)
        expires = time.time() + 86400 * 7  # 7 gün
        conn.execute(
            "INSERT INTO sessions (token, user_id, created_at, expires_at) VALUES (?,?,?,?)",
            (session_token, user["id"], time.time(), expires))
        conn.commit()
        log_admin_action("USER_LOGIN", {"username": username, "user_id": user["id"]})
        return JSONResponse({
            "token": jwt_token or session_token,
            "session_token": session_token,
            "token_type": "jwt" if jwt_token else "session",
            "user": {"id": user["id"], "username": user["username"],
                     "display_name": user["display_name"], "role": user["role"],
                     "avatar_color": user["avatar_color"]}
        })
    finally:
        conn.close()


@router.post("/api/auth/register")
async def auth_register(request: Request) -> JSONResponse:
    body = await request.json()
    username = body.get("username", "").strip().lower()
    display_name = body.get("display_name", username)
    password = body.get("password", "")
    if len(username) < 3 or len(password) < 4:
        return JSONResponse({"error": "Kullanıcı adı min 3, şifre min 4 karakter"}, status_code=400)
    colors = ["#4ade80", "#60a5fa", "#a78bfa", "#fbbf24", "#f87171", "#fb923c", "#34d399"]
    conn = _auth_db()
    try:
        existing = conn.execute("SELECT id FROM users WHERE username=?", (username,)).fetchone()
        if existing:
            return JSONResponse({"error": "Bu kullanıcı adı zaten alınmış"}, status_code=409)
        conn.execute(
            "INSERT INTO users (username, display_name, password_hash, role, avatar_color, created_at) VALUES (?,?,?,?,?,?)",
            (username, display_name, _hash_pw(password), "member", random.choice(colors), time.time()))
        conn.commit()
        log_admin_action("USER_REGISTERED", {"username": username})
        return JSONResponse({"status": "created", "username": username})
    finally:
        conn.close()


@router.get("/api/auth/me")
async def auth_me(request: Request) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user = _get_user_from_token(token)
    if not user:
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    return JSONResponse({"user": {
        "id": user["id"], "username": user["username"],
        "display_name": user["display_name"], "role": user["role"],
        "avatar_color": user["avatar_color"]
    }})


@router.get("/api/auth/users")
async def auth_list_users(request: Request) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user = _get_user_from_token(token)
    if not user:
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    conn = _auth_db()
    try:
        rows = conn.execute("SELECT id, username, display_name, role, avatar_color FROM users WHERE is_active=1 ORDER BY created_at").fetchall()
        return JSONResponse({"users": [dict(r) for r in rows]})
    finally:
        conn.close()


# ── Messaging Endpoints ──────────────────────────────────────

_chat_subscribers: List[asyncio.Queue] = []

@router.post("/api/chat/send")
async def chat_send(request: Request) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user = _get_user_from_token(token)
    if not user:
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    body = await request.json()
    text = body.get("body", "").strip()
    channel = body.get("channel", "general")
    recipient_id = body.get("recipient_id")
    if not text:
        return JSONResponse({"error": "Boş mesaj gönderilemez"}, status_code=400)
    conn = _auth_db()
    try:
        conn.execute(
            "INSERT INTO chat_messages (sender_id, recipient_id, channel, body, created_at) VALUES (?,?,?,?,?)",
            (user["id"], recipient_id, channel, text, time.time()))
        conn.commit()
    finally:
        conn.close()
    msg_obj = {
        "sender_id": user["id"], "sender_name": user["display_name"],
        "avatar_color": user["avatar_color"], "body": text,
        "channel": channel, "ts": time.time()
    }
    for q in _chat_subscribers:
        try:
            q.put_nowait(msg_obj)
        except asyncio.QueueFull:
            pass
    return JSONResponse({"status": "sent"})


@router.get("/api/chat/history")
async def chat_history(request: Request, channel: str = "general", limit: int = 50) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not _get_user_from_token(token):
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    conn = _auth_db()
    try:
        rows = conn.execute("""
            SELECT cm.*, u.display_name as sender_name, u.avatar_color
            FROM chat_messages cm JOIN users u ON cm.sender_id=u.id
            WHERE cm.channel=? ORDER BY cm.created_at DESC LIMIT ?
        """, (channel, limit)).fetchall()
        messages = [dict(r) for r in reversed(rows)]
        return JSONResponse({"messages": messages, "channel": channel})
    finally:
        conn.close()


@router.get("/api/chat/stream")
async def chat_stream(request: Request) -> StreamingResponse:
    token = request.query_params.get("token", "")
    if not _get_user_from_token(token):
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)

    q: asyncio.Queue = asyncio.Queue(maxsize=100)
    _chat_subscribers.append(q)

    async def event_gen() -> AsyncIterator[str]:
        try:
            while True:
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=20)
                    yield f"data: {json.dumps(msg, ensure_ascii=False)}\n\n"
                except asyncio.TimeoutError:
                    yield f"data: {json.dumps({'ping': True})}\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            if q in _chat_subscribers:
                _chat_subscribers.remove(q)

    return StreamingResponse(event_gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ── Project Management Endpoints ─────────────────────────────

@router.get("/api/projects")
async def project_list(request: Request) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not _get_user_from_token(token):
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    conn = _auth_db()
    try:
        rows = conn.execute("""
            SELECT p.*, u.display_name as owner_name,
                   (SELECT COUNT(*) FROM tasks WHERE project_id=p.id) as task_count,
                   (SELECT COUNT(*) FROM tasks WHERE project_id=p.id AND status='done') as done_count
            FROM projects p LEFT JOIN users u ON p.owner_id=u.id
            ORDER BY p.created_at DESC
        """).fetchall()
        return JSONResponse({"projects": [dict(r) for r in rows]})
    finally:
        conn.close()


@router.post("/api/projects")
async def project_create(request: Request) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user = _get_user_from_token(token)
    if not user:
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    body = await request.json()
    conn = _auth_db()
    try:
        conn.execute(
            "INSERT INTO projects (name, description, status, owner_id, created_at) VALUES (?,?,?,?,?)",
            (body.get("name", ""), body.get("description", ""), "active", user["id"], time.time()))
        conn.commit()
        log_admin_action("PROJECT_CREATED", {"name": body.get("name", ""), "by": user["username"]})
        return JSONResponse({"status": "created"})
    finally:
        conn.close()


@router.put("/api/projects/{project_id}")
async def project_update(project_id: int, request: Request) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user = _get_user_from_token(token)
    if not user:
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    body = await request.json()
    conn = _auth_db()
    try:
        fields, vals = [], []
        for k in ("name", "description", "status"):
            if k in body:
                fields.append(f"{k}=?")
                vals.append(body[k])
        if fields:
            vals.append(project_id)
            conn.execute(f"UPDATE projects SET {','.join(fields)} WHERE id=?", vals)
            conn.commit()
        log_admin_action("PROJECT_UPDATED", {"project_id": project_id, "by": user["username"]})
        return JSONResponse({"status": "updated"})
    finally:
        conn.close()


@router.delete("/api/projects/{project_id}")
async def project_delete(project_id: int, request: Request) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user = _get_user_from_token(token)
    if not user:
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    conn = _auth_db()
    try:
        conn.execute("DELETE FROM tasks WHERE project_id=?", (project_id,))
        conn.execute("DELETE FROM projects WHERE id=?", (project_id,))
        conn.commit()
        log_admin_action("PROJECT_DELETED", {"project_id": project_id, "by": user["username"]})
        return JSONResponse({"status": "deleted"})
    finally:
        conn.close()


# Tasks
@router.get("/api/projects/{project_id}/tasks")
async def task_list(project_id: int, request: Request) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    if not _get_user_from_token(token):
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    conn = _auth_db()
    try:
        rows = conn.execute("""
            SELECT t.*, u.display_name as assignee_name, u.avatar_color as assignee_color
            FROM tasks t LEFT JOIN users u ON t.assigned_to=u.id
            WHERE t.project_id=? ORDER BY
                CASE t.priority WHEN 'critical' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END,
                t.created_at DESC
        """, (project_id,)).fetchall()
        return JSONResponse({"tasks": [dict(r) for r in rows], "project_id": project_id})
    finally:
        conn.close()


@router.post("/api/projects/{project_id}/tasks")
async def task_create(project_id: int, request: Request) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user = _get_user_from_token(token)
    if not user:
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    body = await request.json()
    now = time.time()
    conn = _auth_db()
    try:
        conn.execute(
            "INSERT INTO tasks (project_id, title, description, status, priority, assigned_to, due_date, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
            (project_id, body.get("title", ""), body.get("description", ""),
             "todo", body.get("priority", "medium"),
             body.get("assigned_to"), body.get("due_date", ""), now, now))
        conn.commit()
        log_admin_action("TASK_CREATED", {"title": body.get("title", ""), "project_id": project_id, "by": user["username"]})
        return JSONResponse({"status": "created"})
    finally:
        conn.close()


@router.put("/api/tasks/{task_id}")
async def task_update(task_id: int, request: Request) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user = _get_user_from_token(token)
    if not user:
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    body = await request.json()
    conn = _auth_db()
    try:
        fields, vals = [], []
        for k in ("title", "description", "status", "priority", "assigned_to", "due_date"):
            if k in body:
                fields.append(f"{k}=?")
                vals.append(body[k])
        if fields:
            fields.append("updated_at=?")
            vals.append(time.time())
            vals.append(task_id)
            conn.execute(f"UPDATE tasks SET {','.join(fields)} WHERE id=?", vals)
            conn.commit()
        log_admin_action("TASK_UPDATED", {"task_id": task_id, "status": body.get("status", ""), "by": user["username"]})
        return JSONResponse({"status": "updated"})
    finally:
        conn.close()


@router.delete("/api/tasks/{task_id}")
async def task_delete(task_id: int, request: Request) -> JSONResponse:
    token = request.headers.get("Authorization", "").replace("Bearer ", "")
    user = _get_user_from_token(token)
    if not user:
        return JSONResponse({"error": "Oturum geçersiz"}, status_code=401)
    conn = _auth_db()
    try:
        conn.execute("DELETE FROM tasks WHERE id=?", (task_id,))
        conn.commit()
        log_admin_action("TASK_DELETED", {"task_id": task_id, "by": user["username"]})
        return JSONResponse({"status": "deleted"})
    finally:
        conn.close()
