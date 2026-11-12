"""Opt-in relevance transport; no emitter/evaluator is migrated by this module."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .client_errors import DispatchUpgradeRequired
from .task_relevance_contract import (
    RELEVANCE_EFFECTIVE_SCOPE, RELEVANCE_PATH, RELEVANCE_PROTOCOL, RELEVANCE_SCOPE,
)

if TYPE_CHECKING:
    import httpx


class RelevanceClientMixin:
    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]
        _control_headers: Callable[[], dict[str, str]]
        health: Callable[[], dict]

    def _require_relevance(self, *, effective_scope: bool = False) -> None:
        health = self.health()
        contract = health.get("task_relevance") if isinstance(health, dict) else None
        scope = RELEVANCE_EFFECTIVE_SCOPE if effective_scope else RELEVANCE_SCOPE
        if (
            not isinstance(contract, dict)
            or contract.get("protocol") != RELEVANCE_PROTOCOL
            or not isinstance(contract.get("supported_versions"), list)
            or not any(type(v) is int and v == 1 for v in contract["supported_versions"])
            or not isinstance(contract.get("registration_scopes"), list)
            or scope not in contract["registration_scopes"]
            or contract.get("control_authority_configured") is not True
        ):
            raise DispatchUpgradeRequired(
                f"running coordinator lacks control-authorized relevance scope {scope}; "
                "upgrade the receiver before reporting, with no legacy abandonment fallback"
            )

    def _relevance_result(self, response: httpx.Response) -> dict:
        result = self._unwrap(response)
        if (
            not isinstance(result, dict) or result.get("protocol") != RELEVANCE_PROTOCOL
            or type(result.get("version")) is not int or result["version"] != 1
            or not isinstance(result.get("result"), dict)
        ):
            raise DispatchUpgradeRequired("running coordinator returned an invalid relevance receipt")
        return result["result"]

    def relevance_snapshot(
        self, task_id: str, *, registration_id: str, effective_scope: bool = False,
    ) -> dict:
        self._require_relevance(
            effective_scope=effective_scope or registration_id.startswith("declared:"),
        )
        return self._relevance_result(self._http.get(
            f"{RELEVANCE_PATH}/tasks/{quote(task_id, safe='')}",
            params={"registration_id": registration_id}, headers=self._control_headers(),
        ))

    def report_task_relevance(self, task_id: str, *, report: dict) -> dict:
        """Send an original retained request once; unknown outcomes require receipt recovery."""
        self._require_relevance(effective_scope=report.get("effective_revision") is not None)
        return self._relevance_result(self._http.post(
            f"{RELEVANCE_PATH}/tasks/{quote(task_id, safe='')}", json=report,
            headers=self._control_headers(),
        ))

    def relevance_receipt(
        self, task_id: str, request_id: str, *, registration_id: str,
    ) -> dict:
        self._require_relevance()
        return self._relevance_result(self._http.get(
            f"{RELEVANCE_PATH}/tasks/{quote(task_id, safe='')}/receipts/{quote(request_id, safe='')}",
            params={"registration_id": registration_id}, headers=self._control_headers(),
        ))
