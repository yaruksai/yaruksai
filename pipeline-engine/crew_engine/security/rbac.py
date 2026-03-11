from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Set


@dataclass(frozen=True)
class Actor:
    user_id: str
    roles: Set[str]


class RBACError(Exception):
    pass


def require_roles(actor: Actor, allowed_roles: Iterable[str]) -> None:
    """Fail-closed RBAC guard: allowed_roles boşsa bile izin verme."""
    allowed = set(allowed_roles or [])
    if not allowed:
        raise RBACError("RBAC misconfigured: allowed_roles is empty (fail-closed).")
    if not (actor.roles & allowed):
        raise RBACError(f"Forbidden: requires one of {sorted(allowed)}")
