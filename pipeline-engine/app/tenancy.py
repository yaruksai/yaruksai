# pipeline-engine/app/tenancy.py
"""
YARUKSAİ — Multi-Tenancy & API Key Management
═══════════════════════════════════════════════
Database-per-Tenant Architecture:
  - Ana DB (admin.db): organizations, org_memberships, api_keys (meta)
  - Tenant DB (tenants/{slug}/tenant.db): CRM, orders, content, pipeline runs
  - Fiziksel izolasyon — farklı .db dosyaları ile veri karışması İMKANSIZ
"""

import os
import time
import sqlite3
import secrets
import hashlib
import json
from pathlib import Path
from typing import Optional, Dict, Any, List
from datetime import datetime

from fastapi import APIRouter, Request, HTTPException
from fastapi.responses import JSONResponse

from app.auth_jwt import (
    get_current_user, require_role, extract_api_key,
    generate_api_key, verify_api_key_hash,
)

router = APIRouter(prefix="/api/orgs", tags=["tenancy"])

ADMIN_DB = Path(os.getenv("ADMIN_DB", "/app/data/admin.db"))
TENANT_DATA_ROOT = Path(os.getenv("TENANT_DATA_ROOT", "/app/data/tenants"))


# ─── Database Connections ─────────────────────────────────────

def _admin_db() -> sqlite3.Connection:
    """Central admin DB — only org metadata, memberships, and API keys."""
    ADMIN_DB.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ADMIN_DB))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS organizations (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL,
            slug TEXT UNIQUE NOT NULL,
            plan TEXT NOT NULL DEFAULT 'starter',
            logo_url TEXT DEFAULT '',
            contact_email TEXT DEFAULT '',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS org_memberships (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            org_id INTEGER NOT NULL,
            role TEXT NOT NULL DEFAULT 'member',
            created_at REAL NOT NULL,
            UNIQUE(user_id, org_id)
        );
        CREATE TABLE IF NOT EXISTS api_keys (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            org_id INTEGER NOT NULL,
            key_hash TEXT NOT NULL,
            key_prefix TEXT NOT NULL,
            label TEXT NOT NULL DEFAULT '',
            permissions TEXT NOT NULL DEFAULT 'read,audit',
            is_active INTEGER NOT NULL DEFAULT 1,
            created_at REAL NOT NULL,
            last_used_at REAL DEFAULT NULL,
            total_requests INTEGER NOT NULL DEFAULT 0,
            FOREIGN KEY(org_id) REFERENCES organizations(id)
        );
    """)
    conn.commit()
    return conn


def get_tenant_db(org_slug: str) -> sqlite3.Connection:
    """
    Tenant-specific DB — fiziksel izolasyon.
    Her müşterinin verisi kendi .db dosyasında.
    
    Path: data/tenants/{org_slug}/tenant.db
    """
    tenant_dir = TENANT_DATA_ROOT / org_slug
    tenant_dir.mkdir(parents=True, exist_ok=True)
    db_path = tenant_dir / "tenant.db"

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS crm_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            from_name TEXT,
            email TEXT,
            message TEXT,
            status TEXT DEFAULT 'new',
            reply TEXT DEFAULT '',
            created_at REAL NOT NULL,
            replied_at REAL DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            client_name TEXT NOT NULL,
            description TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            amount REAL DEFAULT 0,
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS content_pool (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            title TEXT NOT NULL,
            body TEXT DEFAULT '',
            category TEXT DEFAULT 'blog',
            status TEXT DEFAULT 'draft',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS site_content (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            category TEXT NOT NULL DEFAULT 'service',
            title TEXT NOT NULL,
            price REAL DEFAULT 0,
            body TEXT DEFAULT '',
            created_at REAL NOT NULL,
            updated_at REAL NOT NULL
        );
        CREATE TABLE IF NOT EXISTS pipeline_runs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id TEXT UNIQUE NOT NULL,
            goal TEXT DEFAULT '',
            status TEXT DEFAULT 'pending',
            sigma REAL DEFAULT 0,
            artifacts_path TEXT DEFAULT '',
            created_at REAL NOT NULL,
            completed_at REAL DEFAULT NULL
        );
        CREATE TABLE IF NOT EXISTS audit_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            action TEXT NOT NULL,
            details TEXT DEFAULT '{}',
            seal TEXT DEFAULT '',
            ts_human TEXT DEFAULT '',
            created_at REAL NOT NULL
        );
    """)
    conn.commit()
    return conn


def get_tenant_db_by_org_id(org_id: int) -> sqlite3.Connection:
    """Org ID'den slug'ı bulup tenant DB döndür."""
    conn = _admin_db()
    try:
        row = conn.execute(
            "SELECT slug FROM organizations WHERE id=? AND is_active=1",
            (org_id,)
        ).fetchone()
        if not row:
            raise HTTPException(status_code=404, detail="Organizasyon bulunamadı")
        return get_tenant_db(row["slug"])
    finally:
        conn.close()


def get_tenant_artifacts_dir(org_slug: str, run_id: str) -> Path:
    """Tenant-specific artifact dizini."""
    artifacts_root = Path(os.getenv("ARTIFACT_ROOT", "/app/artifacts"))
    tenant_artifacts = artifacts_root / org_slug / run_id
    tenant_artifacts.mkdir(parents=True, exist_ok=True)
    return tenant_artifacts


# ─── Organization Endpoints ───────────────────────────────────

@router.post("")
async def create_organization(request: Request) -> JSONResponse:
    """Create a new organization with isolated tenant DB."""
    user = get_current_user(request)
    body = await request.json()
    name = body.get("name", "").strip()
    if not name or len(name) < 2:
        raise HTTPException(status_code=400, detail="Organizasyon adı gerekli (min 2 karakter)")

    slug = name.lower().replace(" ", "-").replace("_", "-")
    slug = "".join(c for c in slug if c.isalnum() or c == "-")

    now = time.time()
    conn = _admin_db()
    try:
        existing = conn.execute("SELECT id FROM organizations WHERE slug=?", (slug,)).fetchone()
        if existing:
            raise HTTPException(status_code=409, detail="Bu organizasyon adı zaten mevcut")

        cursor = conn.execute(
            "INSERT INTO organizations (name, slug, plan, contact_email, created_at, updated_at) VALUES (?,?,?,?,?,?)",
            (name, slug, body.get("plan", "starter"), body.get("contact_email", ""), now, now))
        org_id = cursor.lastrowid

        conn.execute(
            "INSERT INTO org_memberships (user_id, org_id, role, created_at) VALUES (?,?,?,?)",
            (user["sub"], org_id, "admin", now))
        conn.commit()

        # Initialize the tenant database (creates the file + tables)
        tenant_conn = get_tenant_db(slug)
        tenant_conn.close()
        print(f"[TENANCY] 🏢 Org '{name}' ({slug}) oluşturuldu — izole DB: tenants/{slug}/tenant.db")

        return JSONResponse({
            "status": "created",
            "org": {"id": org_id, "name": name, "slug": slug, "plan": "starter"},
            "tenant_db": f"tenants/{slug}/tenant.db",
        })
    finally:
        conn.close()


@router.get("")
async def list_organizations(request: Request) -> JSONResponse:
    """List organizations the current user belongs to."""
    user = get_current_user(request)
    conn = _admin_db()
    try:
        rows = conn.execute("""
            SELECT o.*, om.role as member_role
            FROM organizations o
            JOIN org_memberships om ON o.id = om.org_id
            WHERE om.user_id = ? AND o.is_active = 1
            ORDER BY o.created_at DESC
        """, (user["sub"],)).fetchall()

        # Enrich with tenant DB info
        orgs = []
        for r in rows:
            org = dict(r)
            tenant_path = TENANT_DATA_ROOT / r["slug"] / "tenant.db"
            org["tenant_db_exists"] = tenant_path.exists()
            org["tenant_db_size"] = tenant_path.stat().st_size if tenant_path.exists() else 0
            orgs.append(org)

        return JSONResponse({"organizations": orgs})
    finally:
        conn.close()


@router.get("/{org_id}")
async def get_organization(org_id: int, request: Request) -> JSONResponse:
    """Get organization details with tenant isolation info."""
    user = get_current_user(request)
    conn = _admin_db()
    try:
        org = conn.execute("SELECT * FROM organizations WHERE id=? AND is_active=1", (org_id,)).fetchone()
        if not org:
            raise HTTPException(status_code=404, detail="Organizasyon bulunamadı")

        membership = conn.execute(
            "SELECT role FROM org_memberships WHERE user_id=? AND org_id=?",
            (user["sub"], org_id)).fetchone()
        if not membership and user.get("role") != "founder":
            raise HTTPException(status_code=403, detail="Bu organizasyona erişim yetkiniz yok")

        member_count = conn.execute(
            "SELECT COUNT(*) FROM org_memberships WHERE org_id=?", (org_id,)).fetchone()[0]
        key_count = conn.execute(
            "SELECT COUNT(*) FROM api_keys WHERE org_id=? AND is_active=1", (org_id,)).fetchone()[0]

        result = dict(org)
        result["member_count"] = member_count
        result["api_key_count"] = key_count
        result["user_role"] = membership["role"] if membership else "founder"

        # Tenant isolation info
        tenant_path = TENANT_DATA_ROOT / org["slug"] / "tenant.db"
        result["tenant_db_path"] = f"tenants/{org['slug']}/tenant.db"
        result["tenant_db_exists"] = tenant_path.exists()
        result["tenant_db_size_bytes"] = tenant_path.stat().st_size if tenant_path.exists() else 0

        return JSONResponse({"org": result})
    finally:
        conn.close()


# ─── API Key Management ───────────────────────────────────────

@router.post("/{org_id}/api-keys")
async def create_api_key(org_id: int, request: Request) -> JSONResponse:
    """Generate a new API key for the organization."""
    user = get_current_user(request)
    body = await request.json()
    label = body.get("label", "default").strip()
    permissions = body.get("permissions", "read,audit")

    conn = _admin_db()
    try:
        membership = conn.execute(
            "SELECT role FROM org_memberships WHERE user_id=? AND org_id=?",
            (user["sub"], org_id)).fetchone()
        if not membership and user.get("role") != "founder":
            raise HTTPException(status_code=403, detail="Yetki yok")
        if membership and membership["role"] not in ("admin", "owner"):
            raise HTTPException(status_code=403, detail="API key oluşturmak için admin yetkisi gerekli")

        raw_key, key_hash = generate_api_key()
        key_prefix = raw_key[:12] + "..."

        conn.execute(
            "INSERT INTO api_keys (org_id, key_hash, key_prefix, label, permissions, created_at) VALUES (?,?,?,?,?,?)",
            (org_id, key_hash, key_prefix, label, permissions, time.time()))
        conn.commit()

        return JSONResponse({
            "status": "created",
            "api_key": raw_key,
            "prefix": key_prefix,
            "label": label,
            "warning": "Bu anahtarı kaydedin — tekrar gösterilmeyecek!"
        })
    finally:
        conn.close()


@router.get("/{org_id}/api-keys")
async def list_api_keys(org_id: int, request: Request) -> JSONResponse:
    """List API keys for an organization (hashes hidden)."""
    user = get_current_user(request)
    conn = _admin_db()
    try:
        membership = conn.execute(
            "SELECT role FROM org_memberships WHERE user_id=? AND org_id=?",
            (user["sub"], org_id)).fetchone()
        if not membership and user.get("role") != "founder":
            raise HTTPException(status_code=403, detail="Yetki yok")

        rows = conn.execute(
            "SELECT id, org_id, key_prefix, label, permissions, is_active, created_at, last_used_at, total_requests FROM api_keys WHERE org_id=? ORDER BY created_at DESC",
            (org_id,)).fetchall()
        return JSONResponse({"api_keys": [dict(r) for r in rows]})
    finally:
        conn.close()


@router.delete("/{org_id}/api-keys/{key_id}")
async def revoke_api_key(org_id: int, key_id: int, request: Request) -> JSONResponse:
    """Revoke (deactivate) an API key."""
    user = get_current_user(request)
    conn = _admin_db()
    try:
        membership = conn.execute(
            "SELECT role FROM org_memberships WHERE user_id=? AND org_id=?",
            (user["sub"], org_id)).fetchone()
        if not membership and user.get("role") != "founder":
            raise HTTPException(status_code=403, detail="Yetki yok")

        conn.execute("UPDATE api_keys SET is_active=0 WHERE id=? AND org_id=?", (key_id, org_id))
        conn.commit()
        return JSONResponse({"status": "revoked", "key_id": key_id})
    finally:
        conn.close()


# ─── API Key Validation (for external consumers) ─────────────

def validate_api_key(raw_key: str) -> Optional[Dict[str, Any]]:
    """
    Validate an API key and return org context.
    Returns None if invalid. Updates last_used_at and counter.
    """
    if not raw_key or not raw_key.startswith("yai_"):
        return None

    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    conn = _admin_db()
    try:
        row = conn.execute("""
            SELECT ak.*, o.name as org_name, o.slug as org_slug, o.plan as org_plan
            FROM api_keys ak
            JOIN organizations o ON ak.org_id = o.id
            WHERE ak.key_hash = ? AND ak.is_active = 1 AND o.is_active = 1
        """, (key_hash,)).fetchone()

        if not row:
            return None

        # Update usage counters
        conn.execute(
            "UPDATE api_keys SET last_used_at=?, total_requests=total_requests+1 WHERE id=?",
            (time.time(), row["id"]))
        conn.commit()

        return {
            "org_id": row["org_id"],
            "org_name": row["org_name"],
            "org_slug": row["org_slug"],
            "org_plan": row["org_plan"],
            "permissions": row["permissions"].split(","),
            "key_id": row["id"],
            "tenant_db_path": str(TENANT_DATA_ROOT / row["org_slug"] / "tenant.db"),
        }
    finally:
        conn.close()
