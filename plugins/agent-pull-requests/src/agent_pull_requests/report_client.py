"""Explicit negotiated report reads over the existing authenticated ServiceCaller."""

from __future__ import annotations

import asyncio
import json

from fleet_manager.gateway_service_channel import BODY_BYTES, DEADLINE, TransportError

from .report_read import parse_request, report_scope, response_budget, validate_exchange
from .watch_replay_client import ReplayClient, ReplayClientError
from .watch_replay_store import json_object


class ReportClient:
    """One selected live binding; no acquisition, persistence, retry or fallback."""

    def __init__(self, caller, *, route_prefix, binding, scope, response_limit=BODY_BYTES):
        ReplayClient(caller, route_prefix=route_prefix, target=binding.target,
                     repo=binding.repo, binding=binding)
        self.scope = report_scope(scope)
        if (self.scope["account"], self.scope["repo"]) != (binding.account, binding.repo):
            raise ValueError("report client scope differs from selected binding")
        self.caller, self.binding = caller, binding
        self.response_limit = response_budget(response_limit)
        version = "v2" if response_limit > BODY_BYTES else "v1"
        self.path = route_prefix + "/" + version + "/read/" + binding.target
        self._selected_limit = BODY_BYTES
        self._selected = False
        self._epoch = 0

    async def _exchange(self, operation, payload, *, epoch):
        limit = self.response_limit if operation == "capabilities" else self._selected_limit
        deadline = asyncio.get_running_loop().time() + min(
            payload.get("budget_seconds", DEADLINE), DEADLINE,
        )
        body = json.dumps(
            {"binding": {**self.binding.document(), "report_scope": self.scope},
             "request": payload}, allow_nan=False,
        ).encode("utf-8")
        if len(body) > BODY_BYTES:
            raise ValueError("report request limit")
        try:
            pending = self.caller.request(
                "POST", self.path + "/" + operation,
                request_headers=(("Content-Type", "application/json"),),
                body=body, response_limit=limit,
            )
            response = (
                await asyncio.wait_for(pending, max(0, deadline - asyncio.get_running_loop().time()))
                if self.response_limit > BODY_BYTES else await pending
            )
        except asyncio.TimeoutError:
            if epoch != self._epoch:
                raise ReplayClientError("report_discovery_superseded") from None
            self._selected = False
            raise ReplayClientError("report_deadline_exhausted") from None
        except TransportError as error:
            if epoch != self._epoch:
                raise ReplayClientError("report_discovery_superseded") from None
            self._selected = False
            raise ReplayClientError(error.code, http_status=error.status) from None
        if epoch != self._epoch:
            raise ReplayClientError("report_discovery_superseded")
        if response.status != 200:
            self._selected = False
            raise ReplayClientError(
                "report_unsupported" if response.status == 404 else "report_unavailable",
                http_status=response.status,
            )
        try:
            types = [value for name, value in response.headers if name.lower() == "content-type"]
            if (
                len(response.body) > limit or len(types) != 1
                or types[0].split(";")[0].strip().lower() != "application/json"
            ):
                raise ValueError
            value = json_object(response.body)
            if value.pop("binding", None) != self.binding.document():
                raise ValueError
            if self.response_limit > BODY_BYTES and operation == "capabilities":
                limit = response_budget(value.get("max_response_bytes"))
                if not BODY_BYTES < limit <= self.response_limit:
                    raise ValueError
            validate_exchange(value, operation=operation, scope=self.scope, number=payload.get("number"),
                              response_limit=limit)
            result = dict(value, binding=self.binding.document())
        except (ValueError, UnicodeError, RecursionError, OverflowError, TypeError):
            self._selected = False
            raise ReplayClientError("invalid_report_response") from None
        if self.response_limit > BODY_BYTES and asyncio.get_running_loop().time() >= deadline:
            self._selected = False
            raise ReplayClientError("report_deadline_exhausted")
        return result

    async def capabilities(self):
        self._epoch += 1
        epoch = self._epoch
        self._selected = False
        value = await self._exchange("capabilities", {}, epoch=epoch)
        if epoch != self._epoch:
            raise ReplayClientError("report_discovery_superseded")
        self._selected = True
        self._selected_limit = value["max_response_bytes"]
        return value

    async def read(self, operation, payload):
        if operation not in ("threads", "diff"):
            raise ValueError("invalid report read")
        payload = parse_request(operation, payload)
        if not self._selected:
            raise ReplayClientError("report_capability_required")
        if self.response_limit > BODY_BYTES:
            payload["expected_response_bytes"] = self._selected_limit
        epoch = self._epoch
        value = await self._exchange(operation, payload, epoch=epoch)
        if epoch != self._epoch:
            raise ReplayClientError("report_discovery_superseded")
        return value
