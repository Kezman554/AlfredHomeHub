"""Shared FastAPI dependencies.

Routers depend on get_vault() rather than constructing a Vault, so tests can
override the dependency with a Vault pointed at a fixture directory.
"""

from __future__ import annotations

from functools import lru_cache

from .config import VAULT_PATH
from .vault import Vault


@lru_cache(maxsize=1)
def get_vault() -> Vault:
    return Vault(root=VAULT_PATH)
