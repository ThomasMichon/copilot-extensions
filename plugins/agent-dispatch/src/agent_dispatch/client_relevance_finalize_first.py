"""Supervisor finalize-first settlement transport for obsolete tasks."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .client_errors import DispatchError, DispatchUpgradeRequired
from .task_relevance_contract import FINALIZE_FIRST_PATH, FINALIZE_FIRST_PROTOCOL, RELEVANCE_PATH

if TYPE_CHECKING:
    import httpx


class RelevanceFinalizeFirstClientMixin:
    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]
        _control_headers: Callable[[], dict[str, str]]

    def _finalize_first_result(self, response: httpx.Response) -> Any:
        try:
            value = self._unwrap(response)
        except DispatchError as exc:
            if exc.status_code in {404, 405}:
                raise DispatchUpgradeRequired(
                    "running coordinator lacks relevance finalize-first v1",
                ) from exc
            raise
        except ValueError as exc:
            raise DispatchUpgradeRequired("invalid relevance finalize-first response") from exc
        if (
            not isinstance(value, dict) or value.get("protocol") != FINALIZE_FIRST_PROTOCOL
            or type(value.get("version")) is not int or value["version"] != 1
            or "result" not in value
        ):
            raise DispatchUpgradeRequired("running coordinator lacks a valid finalize-first v1 response")
        return value["result"]

    def _finalize_first_post(self, task_id: str, action: str, body: dict[str, Any]) -> dict[str, Any]:
        result = self._finalize_first_result(self._http.post(
            f"{RELEVANCE_PATH}/tasks/{quote(task_id, safe='')}/finalize-first/{action}",
            json={"version": 1, **body}, headers=self._control_headers(),
        ))
        if not isinstance(result, dict):
            raise DispatchUpgradeRequired("invalid relevance finalize-first result")
        return result

    def relevance_finalize_candidates(self, supervisor_id: str) -> list[dict[str, Any]]:
        result = self._finalize_first_result(self._http.get(
            FINALIZE_FIRST_PATH, params={"supervisor_id": supervisor_id},
            headers=self._control_headers(),
        ))
        if not isinstance(result, list) or not all(isinstance(item, dict) for item in result):
            raise DispatchUpgradeRequired("invalid relevance finalize-first candidates")
        return result

    def fence_relevance_finalize(
        self, task_id: str, *, supervisor_id: str, generation: int, reservation_key: str,
    ) -> dict[str, Any]:
        return self._finalize_first_post(task_id, "fence", {
            "supervisor_id": supervisor_id, "generation": generation,
            "reservation_key": reservation_key,
        })

    def settle_relevance_finalize(self, task_id: str, *, token: str, evidence: dict[str, Any]) -> dict[str, Any]:
        return self._finalize_first_post(task_id, "settle", {"token": token, "evidence": evidence})

    def release_relevance_finalize(self, task_id: str, *, token: str, detail: str) -> dict[str, Any]:
        return self._finalize_first_post(task_id, "release", {"token": token, "detail": detail})
