"""Auth injectors: declare *what form of auth to inject* per bridge."""

from __future__ import annotations

from .base import AuthInjector, NoneInjector, TokenInjector
from .injectors import (
    EntraInjector,
    EnvInjector,
    GhInjector,
    GitCredentialInjector,
    build_injector,
    parse_response,
)

__all__ = [
    "AuthInjector",
    "EntraInjector",
    "EnvInjector",
    "GhInjector",
    "GitCredentialInjector",
    "NoneInjector",
    "TokenInjector",
    "build_injector",
    "parse_response",
]
