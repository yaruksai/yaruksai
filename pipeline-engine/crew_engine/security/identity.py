from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Set


@dataclass(frozen=True)
class Identity:
    user_id: str
    roles: Set[str]


class IdentityProvider(Protocol):
    def get_identity(self, token: str) -> Identity:
        """MVP: local provider. Future: LDAP/OIDC adapter."""
        ...


class LocalIdentityProvider:
    def __init__(self, token_to_identity: dict[str, Identity]):
        self._db = token_to_identity

    def get_identity(self, token: str) -> Identity:
        if token not in self._db:
            raise PermissionError("Invalid token")
        return self._db[token]

