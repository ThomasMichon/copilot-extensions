"""Read pools whose slots are held by tasks that aren't progressing."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, cast

import httpx


class PoolStarvationClientMixin:
    """The coordinator's starved-pool read."""

    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]

    def pool_starvation(self, *, timeout: float | None = None) -> list[dict[str, Any]]:
        options: dict[str, Any] = {"timeout": timeout} if timeout is not None else {}
        return cast(list[dict[str, Any]], self._unwrap(self._http.get("/pool-starvation", **options))["pools"])
