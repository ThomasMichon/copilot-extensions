"""core-delegation: reach a plugin's heavy core over a tokened, discovered seam.

See :mod:`core_delegation.delegation` for the full docstring. This package gives
a service-bearing plugin a shared, dependency-free way to delegate a request to a
wired **core** (an in-process default, a remote engine, or a container)
as just another transport target -- discovering it via ``endpoint-rendezvous``,
shipping newline-framed JSON with an optional bearer token, and returning
``None`` to fall through when no core is wired so the built-in / user-mode path
still works.
"""

from __future__ import annotations

from .delegation import (
    TOKEN_KEY,
    TRANSPORT_TAG,
    default_accept,
    delegate,
)

__all__ = [
    "TOKEN_KEY",
    "TRANSPORT_TAG",
    "default_accept",
    "delegate",
]
