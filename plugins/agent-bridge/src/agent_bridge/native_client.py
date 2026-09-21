"""Native HTTP operations on the existing authenticated Bridge client."""
from __future__ import annotations
import math
import urllib.parse
from typing import Any

class NativeClient:
    def get_live_session(self, session_id: str) -> dict[str, Any]:
        from .client import BridgeClientError
        try:
            return self._request("GET", f"/api/v1/live-sessions/{session_id}") or {}
        except BridgeClientError as exc:
            if exc.status == 404:
                return {}
            raise

    def native_capabilities(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/native-executions/capabilities") or {}


    def native_start(self, request: dict[str, Any]) -> dict[str, Any]:
        return self._request("POST", "/api/v1/native-executions", request) or {}


    def native_status(self, execution_id: str, *, generation: str | None = None) -> dict[str, Any]:
        path = "/api/v1/native-executions/" + urllib.parse.quote(execution_id, safe="")
        if generation:
            path += "?" + urllib.parse.urlencode({"generation": generation})
        return self._request("GET", path) or {}


    def native_stop(self, execution_id: str, generation: str, *, force: bool = False) -> dict[str, Any]:
        body: dict[str, Any] = {"generation": generation}
        if force:
            body["force"] = True
        return self._request(
            "POST", "/api/v1/native-executions/" + urllib.parse.quote(execution_id, safe="") + "/stop",
            body, request_timeout=210,
        ) or {}


    def native_resolve(self, target: str) -> dict[str, Any] | None:
        value = self._request(
            "GET", "/api/v1/native-executions/resolve?" + urllib.parse.urlencode({"target": target}),
        ) or {}
        return value.get("execution")


    def native_list(self) -> dict[str, Any]:
        return self._request("GET", "/api/v1/native-executions") or {}


    def native_message(self, execution_id: str, payload: dict[str, Any]) -> dict[str, Any]:
        from .client import BridgeClientError
        wait = float(payload.get("waitTimeout", 120))
        if not math.isfinite(wait) or not 0 < wait <= 300:
            raise BridgeClientError(400, "Native reply timeout must be in (0,300]")
        return self._request(
            "POST", "/api/v1/native-executions/" + urllib.parse.quote(execution_id, safe="") + "/messages",
            payload, request_timeout=wait + 90 if payload.get("wait") else 90,
        ) or {}
