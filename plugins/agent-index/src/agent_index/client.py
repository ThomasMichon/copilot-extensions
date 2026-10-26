"""Small HTTP client used by the CLI and zdd cutover orchestration."""

from __future__ import annotations

import os
from typing import Any

import httpx

from .indexing.change_hints import ChangeHints
from .indexing.change_hints_contract import require_intake_v1, validate_scope


class AgentIndexClient:
    """Synchronous client for the local agent-index service."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 120.0,
        installation_id: str | None = None,
        instance_token: str | None = None,
        transaction_token: str | None = None,
        read_headers: dict[str, str] | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.installation_id = (
            os.environ.get("AGENT_INDEX_INSTALLATION_ID", "")
            if installation_id is None
            else installation_id
        )
        self.instance_token = instance_token
        self.transaction_token = transaction_token
        self.read_headers = read_headers

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
        if self.read_headers is not None and (
            method != "GET" or path not in {"/health", "/status", "/search", "/similar", "/clusters"}
        ):
            raise RuntimeError("authenticated HTTP read clients cannot perform lifecycle operations")
        options = (
            {"trust_env": False, "follow_redirects": False}
            if self.read_headers is not None else {}
        )
        with httpx.Client(timeout=self.timeout, **options) as client:
            headers = {"X-Agent-Index-Installation-Id": self.installation_id}
            headers.update(self.read_headers or {})
            if self.instance_token is not None:
                headers["X-Agent-Index-Instance-Token"] = self.instance_token
            if self.transaction_token is not None:
                headers["X-Agent-Index-Transaction-Token"] = self.transaction_token
            response = client.request(
                method,
                f"{self.base_url}{path}",
                json=json,
                params=filtered_params,
                headers=headers,
            )
            response.raise_for_status()
            if not response.content:
                return {}
            data = response.json()
            return data if isinstance(data, dict) else {"data": data}

    def health(self) -> dict[str, Any]:
        return self._request("GET", "/health")

    def status(self) -> dict[str, Any]:
        return self._request("GET", "/status")

    def reindex(
        self, *, source: str | None = None, full: bool = False,
        change_hints: ChangeHints | None = None,
    ) -> dict[str, Any]:
        """Enqueue ordinary work or negotiate lossless durable hint intake.

        A hinted request never falls back to the legacy endpoint, including
        after a provider rollback or an unknown acceptance outcome.
        """
        if change_hints is None:
            return self._request("POST", "/reindex", json={"source": source, "full": full})
        validate_scope(source, full, change_hints)
        require_intake_v1(self.health())
        result = self._request("POST", "/reindex/hints", json={
            "source": source, "full": False, "change_hints": change_hints.to_dict(),
        })
        if type(result.get("accepted")) is not bool:
            raise RuntimeError("Receiver returned invalid hint receipt; acceptance outcome unknown")
        if result.get("accepted") is True:
            try:
                task = result["task"]
                stored = ChangeHints.from_dict(task["change_hints"])
                if task["source"] != source or task["full"] is not False:
                    raise ValueError("Unexpected accepted scope")
                if stored.union(change_hints) != stored:
                    raise ValueError("Missing accepted identities")
            except (KeyError, TypeError, ValueError):
                raise RuntimeError(
                    "Receiver did not confirm required hints; acceptance outcome unknown"
                ) from None
        return result

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

    def promote(self) -> dict[str, Any]:
        return self._request("POST", "/promote")

    def adopt_relay(self) -> dict[str, Any]:
        return self._request("POST", "/adopt-relay")
