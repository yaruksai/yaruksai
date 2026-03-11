"""
app/routes/__init__.py — Route Registry
"""
from app.routes.admin_os import router as admin_os_router
from app.routes.user_auth import router as user_auth_router
from app.routes.emergency import router as emergency_router
from app.routes.audit import router as audit_router
from app.routes.pipeline import router as pipeline_router

ALL_ROUTERS = [
    admin_os_router,
    user_auth_router,
    emergency_router,
    audit_router,
    pipeline_router,
]

__all__ = ["ALL_ROUTERS"]
