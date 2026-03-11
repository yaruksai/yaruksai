# pipeline-engine/app/auth_jwt.py
"""
YARUKSAİ — JWT Authentication & API Key Auth
═════════════════════════════════════════════
Dual-auth: JWT Bearer tokens for web UI, API keys for external integrations.
"""

import os
import time
import hashlib
import secrets
from typing import Optional, Dict, Any

import jwt
from fastapi import Request, HTTPException

JWT_SECRET = os.getenv("YARUKSAI_JWT_SECRET", "yaruksai-vericore-secret-2026-change-in-production")
JWT_ALGORITHM = "HS256"
JWT_EXPIRY_HOURS = int(os.getenv("JWT_EXPIRY_HOURS", "168"))  # 7 days default


# ─── JWT Operations ───────────────────────────────────────────

def create_access_token(
    user_id: int,
    username: str,
    role: str,
    org_id: Optional[int] = None,
    display_name: str = "",
) -> str:
    """Create a signed JWT token."""
    payload = {
        "sub": user_id,
        "username": username,
        "role": role,
        "org_id": org_id,
        "display_name": display_name,
        "iat": int(time.time()),
        "exp": int(time.time()) + JWT_EXPIRY_HOURS * 3600,
        "iss": "yaruksai-vericore",
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def verify_token(token: str) -> Dict[str, Any]:
    """Verify and decode a JWT token. Raises HTTPException on failure."""
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token süresi dolmuş")
    except jwt.InvalidTokenError:
        raise HTTPException(status_code=401, detail="Geçersiz token")


# ─── API Key Operations ───────────────────────────────────────

def generate_api_key() -> tuple[str, str]:
    """Generate a new API key. Returns (raw_key, key_hash)."""
    raw_key = f"yai_{secrets.token_hex(24)}"
    key_hash = hashlib.sha256(raw_key.encode()).hexdigest()
    return raw_key, key_hash


def verify_api_key_hash(raw_key: str, stored_hash: str) -> bool:
    """Verify an API key against its stored hash."""
    return hashlib.sha256(raw_key.encode()).hexdigest() == stored_hash


# ─── FastAPI Auth Dependency ──────────────────────────────────

def extract_token(request: Request) -> Optional[str]:
    """Extract JWT token from Authorization header."""
    auth = request.headers.get("Authorization", "")
    if auth.startswith("Bearer "):
        return auth[7:]
    return None


def extract_api_key(request: Request) -> Optional[str]:
    """Extract API key from X-API-Key header."""
    return request.headers.get("X-API-Key")


def get_current_user(request: Request) -> Dict[str, Any]:
    """
    FastAPI dependency — extracts and verifies JWT from request.
    Falls back to X-Admin-Key for backward compatibility.
    Returns user payload dict.
    """
    # Try JWT first
    token = extract_token(request)
    if token:
        return verify_token(token)

    # Fallback: legacy admin key
    admin_key = os.getenv("YARUKSAI_ADMIN_KEY", "yaruksai-commander-2026")
    legacy_key = request.headers.get("X-Admin-Key", "")
    if legacy_key and legacy_key == admin_key:
        return {
            "sub": 0,
            "username": "admin",
            "role": "founder",
            "org_id": None,
            "display_name": "Legacy Admin",
        }

    raise HTTPException(status_code=401, detail="Kimlik doğrulama gerekli")


def require_role(user: Dict[str, Any], roles: list[str]) -> None:
    """Check that user has one of the required roles."""
    if user.get("role") not in roles:
        raise HTTPException(status_code=403, detail="Yetkisiz erişim")
