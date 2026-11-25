"""Foreground and credential custody for lifecycle-admitted requests."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from . import cache_warm_selection, service_cache

if TYPE_CHECKING:
    from .cache import PersistentCache
    from .service import VaultService


def handle_admitted(
    service: VaultService, request: dict[str, Any], peer: str, transport: str,
    resolve_kpdb: Callable[..., str | None], selected_cache: Callable[[], PersistentCache],
) -> dict[str, Any]:
    from .service import _DISPATCH_WITHOUT_CREDENTIAL_LOCK

    with service._cache_warm.foreground():
        try:
            if request.get("action") in _DISPATCH_WITHOUT_CREDENTIAL_LOCK:
                return cache_warm_selection.request_result(
                    service, request, service._dispatch_request(request, peer, transport),
                )
            with service._credential_lock, contextlib.ExitStack() as custody:
                service._check_password_ttl()
                custody.enter_context(service_cache.custody(request, resolve_kpdb, selected_cache))
                return cache_warm_selection.request_result(
                    service, request, service._dispatch_request(request, peer, transport),
                )
        except service_cache.ServiceCacheUnavailable:
            return {"ok": False, "error": service_cache.UNAVAILABLE}
