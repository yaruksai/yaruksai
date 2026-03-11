"""
app/auth_rs256.py — JWT RS256 Authentication + RBAC
═══════════════════════════════════════════════════════

Sprint 1 — Foundation Stability
CEO Spec §2: RS256 asimetrik auth, 4 rol, endpoint yetki matrisi.

Roles: admin, engineer, auditor, readonly
Scopes: audit:write, audit:read, ledger:read, agents:run
"""

from __future__ import annotations

import os
import time
import uuid
from functools import wraps
from typing import Dict, List, Optional, Set

from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

try:
    from jose import jwt, JWTError
except ImportError:
    # Fallback: PyJWT
    import jwt as _jwt
    JWTError = Exception
    class jwt:
        @staticmethod
        def encode(payload, key, algorithm):
            return _jwt.encode(payload, key, algorithm=algorithm)
        @staticmethod
        def decode(token, key, algorithms, options=None):
            return _jwt.decode(token, key, algorithms=algorithms, options=options or {})

# ════════════════════════════════════════════════════════
#  KEY MANAGEMENT — RS256 ONLY (CEO Spec §2)
# ════════════════════════════════════════════════════════
#
# Production: JWT_PRIVATE_KEY + JWT_PUBLIC_KEY env vars (base64-encoded PEM)
# Dev/Test: auto-generate RSA 2048-bit key pair (ephemeral, loud warning)

import base64

_ALGORITHM = "RS256"  # HARDCODED — HS256 devre dışı
_TOKEN_EXPIRE_SECONDS = int(os.getenv("JWT_EXPIRE_SECONDS", "3600"))
_IS_PRODUCTION = False

# RS256 key pair — load from env (base64-encoded PEM)
_raw_private = os.getenv("JWT_PRIVATE_KEY", "")
_raw_public = os.getenv("JWT_PUBLIC_KEY", "")

def _decode_key(raw: str) -> str:
    """Decode base64-encoded PEM key, or return raw if already PEM."""
    if raw.startswith("-----"):
        return raw  # Already PEM format
    try:
        return base64.b64decode(raw).decode("utf-8")
    except Exception:
        return raw

if _raw_private and _raw_public:
    # PRODUCTION MODE — keys from env
    _PRIVATE_KEY = _decode_key(_raw_private)
    _PUBLIC_KEY = _decode_key(_raw_public)
    _IS_PRODUCTION = True
    print("[AUTH] 🛡️  [PRODUCTION] RS256 key pair loaded from environment.")
elif _raw_private:
    # Partial — private only, derive public
    _PRIVATE_KEY = _decode_key(_raw_private)
    try:
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PublicFormat, load_pem_private_key,
        )
        _key_obj = load_pem_private_key(_PRIVATE_KEY.encode(), password=None)
        _PUBLIC_KEY = _key_obj.public_key().public_bytes(
            Encoding.PEM, PublicFormat.SubjectPublicKeyInfo
        ).decode()
        _IS_PRODUCTION = True
        print("[AUTH] 🛡️  [PRODUCTION] RS256 — private key loaded, public derived.")
    except Exception as e:
        print(f"[AUTH] ⛔ Public key derivation failed: {e}")
        _PUBLIC_KEY = _PRIVATE_KEY
else:
    # DEV MODE: auto-generate RSA key pair
    _PRIVATE_KEY = ""
    _PUBLIC_KEY = ""
    try:
        from cryptography.hazmat.primitives.asymmetric import rsa
        from cryptography.hazmat.primitives.serialization import (
            Encoding, PrivateFormat, PublicFormat, NoEncryption,
        )
        from cryptography.hazmat.backends import default_backend

        _dev_key = rsa.generate_private_key(
            public_exponent=65537, key_size=2048, backend=default_backend()
        )
        _PRIVATE_KEY = _dev_key.private_bytes(
            Encoding.PEM,
            PrivateFormat.PKCS8,
            NoEncryption(),
        ).decode()
        _PUBLIC_KEY = _dev_key.public_key().public_bytes(
            Encoding.PEM,
            PublicFormat.SubjectPublicKeyInfo,
        ).decode()
        print("[AUTH] ⚠️  [DEV MODE] RS256 key pair auto-generated. "
              "Set JWT_PRIVATE_KEY + JWT_PUBLIC_KEY env vars for production!")
    except ImportError:
        _ALGORITHM = "HS256"
        _PRIVATE_KEY = os.getenv("JWT_SECRET_KEY", "yaruksai-dev-only-secret")
        _PUBLIC_KEY = _PRIVATE_KEY
        print("[AUTH] ⛔ cryptography paketi yok — HS256 dev fallback aktif. "
              "PRODUCTION'DA KULLANILAMAZ.")


# ════════════════════════════════════════════════════════
#  ROLE DEFINITIONS
# ════════════════════════════════════════════════════════

ROLES: Dict[str, Set[str]] = {
    "admin":    {"audit:write", "audit:read", "ledger:read", "agents:run", "admin:all"},
    "engineer": {"audit:write", "audit:read", "ledger:read", "agents:run"},
    "auditor":  {"audit:read", "ledger:read"},
    "readonly": {"ledger:read"},
}

# CEO Spec §2.2 — Endpoint Permission Matrix
ENDPOINT_PERMISSIONS: Dict[str, Dict[str, Set[str]]] = {
    "POST /v1/audit":         {"allowed_roles": {"admin", "engineer"}},
    "POST /v1/audit?shadow":  {"allowed_roles": {"admin", "engineer", "auditor"}},
    "GET /v1/ledger":         {"allowed_roles": {"admin", "engineer", "auditor", "readonly"}},
    "POST /v1/agents/run":    {"allowed_roles": {"admin", "engineer"}},
    "POST /v1/emanet/decide": {"allowed_roles": {"admin", "engineer"}},
    "GET /v1/emanet/status":  {"allowed_roles": {"admin", "engineer", "auditor", "readonly"}},
    "GET /health":            {"allowed_roles": {"admin", "engineer", "auditor", "readonly"}},
    "POST /auth/token":       {"allowed_roles": {"admin", "engineer", "auditor", "readonly"}},
}

# Client credentials store (in production → DB)
CLIENT_CREDENTIALS: Dict[str, Dict] = {
    "yaruksai-antigravity": {
        "secret": os.getenv("CLIENT_SECRET_ANTIGRAVITY", "antigravity-secret-2026"),
        "role": "admin",
    },
    "alphaehr-integration": {
        "secret": os.getenv("CLIENT_SECRET_ALPHAEHR", "alphaehr-secret-2026"),
        "role": "engineer",
    },
    "alphaehr-auditor": {
        "secret": os.getenv("CLIENT_SECRET_AUDITOR", "auditor-secret-2026"),
        "role": "auditor",
    },
    "alphaehr-viewer": {
        "secret": os.getenv("CLIENT_SECRET_VIEWER", "viewer-secret-2026"),
        "role": "readonly",
    },
}


# ════════════════════════════════════════════════════════
#  TOKEN CREATION & VALIDATION
# ════════════════════════════════════════════════════════

def create_token(
    client_id: str,
    role: str,
    scopes: Optional[List[str]] = None,
) -> Dict:
    """
    Create JWT access token.

    Returns: {access_token, token_type, expires_in, scope}
    """
    if role not in ROLES:
        raise ValueError(f"Invalid role: {role}")

    now = int(time.time())
    effective_scopes = scopes or list(ROLES[role])

    payload = {
        "sub": client_id,
        "role": role,
        "scope": " ".join(effective_scopes),
        "exp": now + _TOKEN_EXPIRE_SECONDS,
        "iat": now,
        "jti": str(uuid.uuid4()),
        "iss": "yaruksai",
    }

    token = jwt.encode(payload, _PRIVATE_KEY, algorithm=_ALGORITHM)

    return {
        "access_token": token,
        "token_type": "Bearer",
        "expires_in": _TOKEN_EXPIRE_SECONDS,
        "scope": " ".join(effective_scopes),
        "role": role,
    }


def verify_token(token: str) -> Dict:
    """
    Verify and decode JWT token.

    Returns decoded payload or raises HTTPException.
    """
    try:
        payload = jwt.decode(
            token,
            _PUBLIC_KEY,
            algorithms=[_ALGORITHM],
            options={"verify_exp": True},
        )
        return payload
    except JWTError as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid or expired token: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token validation failed: {e}",
            headers={"WWW-Authenticate": "Bearer"},
        )


# ════════════════════════════════════════════════════════
#  FASTAPI DEPENDENCIES
# ════════════════════════════════════════════════════════

_bearer_scheme = HTTPBearer(auto_error=False)


async def get_current_user(
    request: Request,
    credentials: Optional[HTTPAuthorizationCredentials] = Depends(_bearer_scheme),
) -> Dict:
    """
    FastAPI dependency: extract and verify JWT from Authorization header.
    Also checks query param ?key= for backward compatibility.
    """
    token = None

    # 1. Bearer token from Authorization header
    if credentials and credentials.credentials:
        token = credentials.credentials

    # 2. Fallback: legacy admin key (backward compat)
    if not token:
        legacy_key = request.headers.get("X-Admin-Key", "")
        if not legacy_key:
            legacy_key = request.query_params.get("key", "")
        if legacy_key:
            admin_key = os.getenv("YARUKSAI_ADMIN_KEY", "yaruksai-commander-2026")
            if legacy_key == admin_key:
                return {
                    "sub": "legacy-admin",
                    "role": "admin",
                    "scope": "audit:write audit:read ledger:read agents:run admin:all",
                }

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required. Provide Bearer token or X-Admin-Key.",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return verify_token(token)


def require_role(*allowed_roles: str):
    """
    FastAPI dependency factory: require specific role(s).

    Usage:
        @app.post("/v1/audit")
        async def audit(user=Depends(require_role("admin", "engineer"))):
            ...
    """
    async def _check(user: Dict = Depends(get_current_user)) -> Dict:
        user_role = user.get("role", "")
        if user_role not in allowed_roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Role '{user_role}' not authorized. Required: {allowed_roles}",
            )
        return user
    return _check


def require_scope(*required_scopes: str):
    """
    FastAPI dependency factory: require specific scope(s).
    """
    async def _check(user: Dict = Depends(get_current_user)) -> Dict:
        user_scopes = set(user.get("scope", "").split())
        missing = set(required_scopes) - user_scopes
        if missing:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Missing scopes: {missing}",
            )
        return user
    return _check
