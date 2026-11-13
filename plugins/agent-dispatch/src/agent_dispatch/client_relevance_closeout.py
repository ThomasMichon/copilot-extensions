"""Original-owner closeout through the pinned task coordinator."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from urllib.parse import quote

from .client_errors import DispatchError, DispatchUpgradeRequired
from .task_relevance_contract import CLOSEOUT_PROTOCOL

if TYPE_CHECKING:
    import httpx


class RelevanceCloseoutClientMixin:
    if TYPE_CHECKING:
        _http: httpx.Client
        _unwrap: Callable[[httpx.Response], Any]
        _owner_settlement_timeout: Callable[[], httpx.Timeout]

    def _closeout_result(self, response: httpx.Response) -> dict:
        try:
            value = self._unwrap(response)
        except DispatchError as exc:
            if exc.status_code in {404, 405}:
                raise DispatchUpgradeRequired(
                    "running coordinator lacks original-owner relevance closeout v1; "
                    "upgrade the receiver; no abandonment fallback",
                ) from exc
            raise
        except ValueError as exc:
            raise DispatchUpgradeRequired("invalid original-owner closeout response") from exc
        if (
            not isinstance(value, dict)
            or value.get("protocol") != CLOSEOUT_PROTOCOL
            or type(value.get("version")) is not int
            or value["version"] != 1
            or not isinstance(value.get("result"), dict)
        ):
            raise DispatchUpgradeRequired("running coordinator lacks a valid closeout v1 response")
        return value["result"]

    def relevance_closeout(self, task_id: str) -> dict:
        """Negotiate on the worker's own route, without global health authority."""
        return self._closeout_result(
            self._http.get(
                f"/tasks/{quote(task_id, safe='')}/relevance-closeout",
            )
        )

    def _mutate_relevance_closeout(self, task_id: str, arguments: dict, operation: str) -> dict:
        current = self.relevance_closeout(task_id)
        expected = {
            "task_id": task_id,
            "request_id": arguments["request_id"],
            "owner": arguments["worker_id"],
            "owner_session_id": arguments["native_id"],
            "generation": arguments["expected_generation"],
        }
        if any(
            type(current.get(key)) is not type(value) or current[key] != value
            for key, value in expected.items()
        ):
            raise DispatchError(409, "original-owner closeout changed; custody retained")
        return self._closeout_result(
            self._http.post(
                f"/tasks/{quote(task_id, safe='')}/relevance-closeout/{operation}",
                json={"version": 1, **arguments},
                timeout=self._owner_settlement_timeout(),
            )
        )

    def prepare_relevance_closeout(self, task_id: str, arguments: dict) -> dict:
        return self._mutate_relevance_closeout(task_id, arguments, "prepare")

    def finish_relevance_closeout(self, task_id: str, arguments: dict) -> dict:
        return self._mutate_relevance_closeout(task_id, arguments, "finish")
