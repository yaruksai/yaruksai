"""
app/shared.py — Shared utilities used by route modules.
══════════════════════════════════════════════════════════

Extracted from main.py to enable route splitting.
Provides: admin auth, ledger logging, legal disclaimer,
          global state (emergency/boot), weights, file utils.
"""

import hashlib
import json
import os
import re
import sqlite3
import time
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

from fastapi import Request, HTTPException


# ─── Admin Auth ────────────────────────────────────────────────
ADMIN_KEY = os.getenv("YARUKSAI_ADMIN_KEY", "yaruksai-commander-2026")

def check_admin(request: Request) -> bool:
    """Admin yetkisi kontrolü."""
    key = request.headers.get("X-Admin-Key", "")
    if not key:
        key = request.query_params.get("key", "")
    return key == ADMIN_KEY


# ─── Admin Ledger ──────────────────────────────────────────────
ADMIN_LEDGER = Path(os.getenv("ADMIN_LEDGER", "/app/artifacts/admin_ledger.jsonl"))

def log_admin_action(action: str, details: dict):
    """Tüm admin eylemlerini WORM ledger'a yaz."""
    entry = {
        "action": action,
        "details": details,
        "timestamp": time.time(),
        "ts_human": datetime.now().isoformat(),
    }
    entry_json = json.dumps(entry, ensure_ascii=False, sort_keys=True)
    entry["seal"] = hashlib.sha256(entry_json.encode()).hexdigest()

    ADMIN_LEDGER.parent.mkdir(parents=True, exist_ok=True)
    with open(ADMIN_LEDGER, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry, ensure_ascii=False) + "\n")


# ─── Legal Disclaimer ─────────────────────────────────────────
LEGAL_DISCLAIMER = {
    "tr": "Bu rapor teknik denetim belgesidir. Nihai karar sorumluluğu "
          "kullanıcıya aittir. Hukuki bağlayıcılığı yoktur.",
    "en": "This report is a technical audit document. Final decision "
          "responsibility rests with the operator. Not legally binding.",
    "ref": "EU AI Act Article 3(4) — Operator Accountability",
}


# ─── Global State (thread-safe via single worker) ─────────────
EMERGENCY_STOPPED = False
EMERGENCY_STOP_INFO: Dict[str, Any] = {}

BOOT_LOCKED = False
BOOT_REPORT: Dict[str, Any] = {}

def get_emergency_state() -> tuple:
    return EMERGENCY_STOPPED, EMERGENCY_STOP_INFO

def set_emergency_state(stopped: bool, info: Dict[str, Any] = None):
    global EMERGENCY_STOPPED, EMERGENCY_STOP_INFO
    EMERGENCY_STOPPED = stopped
    EMERGENCY_STOP_INFO = info or {}

def get_boot_state() -> tuple:
    return BOOT_LOCKED, BOOT_REPORT

def set_boot_state(locked: bool, report: Dict[str, Any] = None):
    global BOOT_LOCKED, BOOT_REPORT
    BOOT_LOCKED = locked
    BOOT_REPORT = report or {}


# ─── File / Path Utilities ────────────────────────────────────
ARTIFACT_ROOT = Path(os.getenv("ARTIFACT_ROOT", "/app/artifacts")).resolve()

def safe_run_id() -> str:
    return f"run_{uuid.uuid4().hex}"

def is_safe_relpath(p: str) -> bool:
    """Path traversal koruması."""
    if not p or p.startswith("/") or "\\" in p:
        return False
    norm = Path(p)
    if any(part in ("..", "") for part in norm.parts):
        return False
    return True

def run_dir(run_id: str) -> Path:
    """Validate run_id format and return resolved path."""
    if not re.fullmatch(r"run_[0-9a-f]{32}", run_id):
        raise HTTPException(status_code=400, detail="Invalid run_id format")
    d = (ARTIFACT_ROOT / run_id).resolve()
    if ARTIFACT_ROOT not in d.parents and d != ARTIFACT_ROOT:
        raise HTTPException(status_code=400, detail="Invalid run_id path")
    return d

def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")

def list_files_recursive(base: Path) -> List[str]:
    out: List[str] = []
    if not base.exists():
        return out
    for p in base.rglob("*"):
        if p.is_file():
            rel = p.relative_to(base).as_posix()
            if is_safe_relpath(rel):
                out.append(rel)
    out.sort()
    return out


# ─── Weights Controller ───────────────────────────────────────
WEIGHTS_FILE = Path(os.getenv("WEIGHTS_FILE", "/app/config/weights.json"))

DEFAULT_WEIGHTS = {
    "adalet":  {"w": 0.18, "label": "Adalet (العدل)"},
    "tevhid":  {"w": 0.18, "label": "Tevhid (التوحيد)"},
    "emanet":  {"w": 0.14, "label": "Emanet (الأمانة)"},
    "mizan":   {"w": 0.14, "label": "Mizan (الميزان)"},
    "sidk":    {"w": 0.12, "label": "Sıdk (الصدق)"},
    "ihsan":   {"w": 0.12, "label": "İhsan (الإحسان)"},
    "itikat":  {"w": 0.12, "label": "İtikat (الاعتقاد)"},
}

# 🛡️ DEMİR NİZAM: Minimum ağırlık eşikleri
MINIMUM_WEIGHTS = {
    "adalet": 0.10,
    "tevhid": 0.08,
    "mizan":  0.08,
    "emanet": 0.05,
    "sidk":   0.05,
    "ihsan":  0.03,
    "itikat": 0.03,
}

def load_weights():
    if WEIGHTS_FILE.exists():
        try:
            return json.loads(WEIGHTS_FILE.read_text(encoding="utf-8"))
        except Exception:
            pass
    return DEFAULT_WEIGHTS.copy()

def save_weights(data):
    WEIGHTS_FILE.parent.mkdir(parents=True, exist_ok=True)
    WEIGHTS_FILE.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


# ─── Admin DB ──────────────────────────────────────────────────
ADMIN_DB = Path(os.getenv("ADMIN_DB", "/app/data/admin.db"))

def admin_db() -> sqlite3.Connection:
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
