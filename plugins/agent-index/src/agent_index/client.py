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
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        filtered_params = (
            {key: value for key, value in params.items() if value is not None}
            if params is not None
            else None
        )
        with httpx.Client(timeout=self.timeout) as client:
            response = client.request(
                method,
                f"{self.base_url}{path}",
                json=json,
                params=filtered_params,
            )
            response.raise_for_status()
            if not response.content:
                return {}
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def search(
        self,
        query: str,
        *,
        limit: int,
        source: str | None,
        language: str | None,
        repo: str | None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/search",
            params={
                "q": query,
                "limit": limit,
                "source": source,
                "language": language,
                "repo": repo,
            },
        )

    def similar(
        self,
        chunk_id: str,
        *,
        limit: int,
        source: str | None,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/similar",
            params={"id": chunk_id, "limit": limit, "source": source},
        )

    def clusters(
        self,
        *,
        source: str | None,
        bucket: str | None,
        model: str | None,
        exact_dupes_only: bool,
        limit: int,
    ) -> dict[str, Any]:
        return self._request(
            "GET",
            "/clusters",
            params={
                "source": source,
                "bucket": bucket,
                "model": model,
                "exact_dupes_only": exact_dupes_only,
                "limit": limit,
            },
        )

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
