"""Small HTTP client used by the CLI and zdd cutover orchestration."""

from __future__ import annotations

from typing import Any

import httpx


class AgentIndexClient:
    """Synchronous client for the local agent-index service."""

    def __init__(self, base_url: str, *, timeout: float = 120.0) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout

    def _request(
        self,
        method: str,
        path: str,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        with httpx.Client(timeout=self.timeout) as client:
            response = client.request(method, f"{self.base_url}{path}", json=json)
            response.raise_for_status()
            if not response.content:
                return {}
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def drain(self, *, timeout: float, poll: float, force: bool) -> dict[str, Any]:
        return self._request(
            "POST",
            "/drain",
            {"timeout": timeout, "poll": poll, "force": force},
        )

    def undrain(self) -> dict[str, Any]:
        return self._request("POST", "/undrain")

    def shutdown(self) -> dict[str, Any]:
        return self._request("POST", "/shutdown")

    def adopt_relay(self) -> dict[str, Any]:
        return self._request("POST", "/adopt-relay")
