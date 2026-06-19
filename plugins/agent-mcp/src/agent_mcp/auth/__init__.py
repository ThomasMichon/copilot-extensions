"""Auth injectors: declare *what form of auth to inject* per bridge."""

from __future__ import annotations

from .base import AuthInjector, CompositeInjector, NoneInjector, TokenInjector
from .injectors import (
    CommandInjector,
    EntraInjector,
    EnvInjector,
    GhInjector,
    GitCredentialInjector,
    build_injector,
    parse_response,
)

__all__ = [
    "AuthInjector",
    "CommandInjector",
    "CompositeInjector",
    "EntraInjector",
    "EnvInjector",
    "GhInjector",
    "GitCredentialInjector",
    "NoneInjector",
    "TokenInjector",
    "build_injector",
    "parse_response",
]
