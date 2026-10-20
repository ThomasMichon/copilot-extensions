"""Explicit authenticated RPC transport; no discovery, installation or retries."""

from __future__ import annotations

import http.client
import ipaddress
import math
import os
from pathlib import Path
import re
import subprocess
import sys
from urllib.parse import urlsplit

from .client import ClientError
from .store import (
    ChatError, MAX_BODY_BYTES, MAX_PAGE_BYTES, json_bytes, strict_json, validate_request,
)

STDIO_REQUEST = "agent-chat.gateway-request"
STDIO_RESPONSE = "agent-chat.gateway-response"
HTTP_RESULT = "agent-chat.http-result"
HTTP_ALLOWANCE = 15
MAX_HTTP_INPUT = 3 * MAX_BODY_BYTES
MAX_STDERR_BYTES = 64 * 1024
MAX_CREDENTIAL_BYTES = 32768 + 2


def command(value: object) -> list[str]:
    if (not isinstance(value, list) or not value
            or any(not isinstance(item, str) or not item or any(c in item for c in "\0\r\n")
                   for item in value)
            or not Path(value[0]).is_absolute()
            or value[0].lower().endswith((".cmd", ".bat", ".ps1"))):
        raise ClientError("invalid_configuration", "Provide complete absolute executable/interpreter argv.")
    name = Path(value[0]).name.lower()
    if name == "wsl.exe" or os.name != "nt" and name.endswith((".exe", ".com")):
        raise ClientError("provider_unsupported", "Cross-OS execution requires the published opt-in owner helper.")
    return list(value)


def _execute(argv: list[str], *, timeout: float, payload: bytes | None, max_capture: int) -> bytes:
    argv = command(argv)
    try:
        from agent_procutil.owned_process import OutputLimitExceeded, run_caller_owned
    except ImportError:
        raise ClientError(
            "provider_unsupported",
            "The client role requires the published canonical run_caller_owned capability.",
        ) from None
    try:
        result = run_caller_owned(
            argv, timeout=timeout, input_text=payload.decode("utf-8") if payload is not None else None,
            max_stdout_bytes=max_capture, max_stderr_bytes=MAX_STDERR_BYTES,
        )
        if result.returncode:
            raise ClientError("provider_failed", f"The owned provider exited with status {result.returncode}.")
        if not isinstance(result.stdout, str):
            raise ClientError("invalid_response", "The provider did not return text output.")
        raw = result.stdout.encode("utf-8")
        if len(raw) > max_capture:
            raise ClientError("invalid_response", "Provider output exceeds the response byte bound.")
        return raw
    except OutputLimitExceeded as error:
        raise ClientError(
            "invalid_response", f"Provider {error.stream} exceeds its raw byte bound; no automatic replay.",
        ) from None
    except subprocess.TimeoutExpired:
        raise ClientError("provider_timeout", "The owned provider exceeded its deadline.") from None
    except (OSError, RuntimeError, UnicodeError) as error:
        raise ClientError(
            "provider_unavailable", f"Owned provider execution failed: {type(error).__name__}.",
        ) from None


def run_command(
    argv: list[str], *, timeout: float, payload: bytes | None = None,
    max_capture: int = MAX_PAGE_BYTES,
) -> bytes:
    return _execute(argv, timeout=timeout, payload=payload, max_capture=max_capture)


def authorization(token: str) -> dict[str, str]:
    if (not token or len(token) > 32768
            or any(ord(char) <= 32 or ord(char) >= 127 for char in token)):
        raise ClientError("invalid_credential", "Credential provider returned an invalid value.")
    scheme = "Bear" + "er "
    return {"Authorization": scheme + token}


def _origin(endpoint):
    try:
        if not isinstance(endpoint, str) or len(endpoint) > 4096:
            raise ValueError("endpoint type or bound")
        parsed = urlsplit(endpoint)
        port = parsed.port
        if (parsed.scheme not in {"http", "https"} or not parsed.hostname
                or parsed.username or parsed.password or parsed.path not in {"", "/"}
                or parsed.query or parsed.fragment or "?" in endpoint or "#" in endpoint
                or any(ord(char) <= 32 or ord(char) >= 127 for char in endpoint)
                or port is not None and not 1 <= port <= 65535):
            raise ValueError("endpoint shape")
        try:
            loopback = ipaddress.ip_address(parsed.hostname).is_loopback
        except ValueError:
            loopback = parsed.hostname.lower() == "localhost"
        if parsed.scheme == "http" and not loopback:
            raise ValueError("unprotected remote endpoint")
    except ValueError:
        raise ClientError(
            "invalid_configuration", "Use an explicit HTTPS origin or an existing owned loopback route.",
        ) from None
    return parsed, loopback


class GatewayTransport:
    def __init__(self, config: dict, *, runner=run_command):
        endpoint = config.get("endpoint")
        parsed, loopback = _origin(endpoint)
        if ("token_command" in config) == ("request_command" in config):
            raise ClientError("invalid_configuration", "Select exactly one explicit credential or RPC provider.")
        self.request_command = command(config["request_command"]) if "request_command" in config else None
        self.token_command = command(config["token_command"]) if "token_command" in config else None
        if self.request_command is not None and not loopback:
            raise ClientError("invalid_configuration", "The fixed-route stdio provider requires loopback.")
        self.parsed, self.runner = parsed, runner
        self.identity = endpoint.rstrip("/")

    @staticmethod
    def _result(status: int, result: dict) -> dict:
        if status != 200:
            code = result.get("error")
            if not isinstance(code, str) or not re.fullmatch(r"[A-Za-z0-9_]{1,64}", code):
                code = "service_error"
            raise ClientError(code, f"Service refused the request with HTTP {status}.", status=status)
        return result

    def call(self, payload: dict) -> dict:
        op = validate_request(payload)
        raw = json_bytes(payload)
        if len(raw) > MAX_BODY_BYTES:
            raise ClientError("invalid_request", "Request exceeds the byte bound.")
        timeout = payload.get("timeout", 30) + HTTP_ALLOWANCE if op in {"wait", "scan"} else HTTP_ALLOWANCE
        if self.request_command is not None:
            envelope = json_bytes({
                "schema": STDIO_REQUEST, "version": 1, "endpoint": self.identity, "payload": payload,
            })
            if len(envelope) > MAX_BODY_BYTES:
                raise ClientError("invalid_request", "Provider envelope exceeds the byte bound.")
            output = self.runner(self.request_command, timeout=timeout + 45, payload=envelope)
            try:
                value = strict_json(output, max_bytes=MAX_PAGE_BYTES)
            except ChatError:
                raise ClientError("invalid_response", "Invalid provider JSON; no automatic replay.") from None
            if (not isinstance(value, dict) or set(value) != {"schema", "version", "status", "result"}
                    or value["schema"] != STDIO_RESPONSE or type(value["version"]) is not int
                    or value["version"] != 1 or type(value["status"]) is not int
                    or not 100 <= value["status"] <= 599 or not isinstance(value["result"], dict)):
                raise ClientError("invalid_response", "Invalid provider response; no automatic replay.")
            return self._result(value["status"], value["result"])
        try:
            token = self.runner(
                self.token_command, timeout=30, max_capture=MAX_CREDENTIAL_BYTES,
            ).decode("utf-8").strip()
        except UnicodeError:
            raise ClientError("invalid_credential", "Credential provider returned invalid UTF-8.") from None
        authorization(token)
        frame = json_bytes({
            "endpoint": self.identity, "payload": payload, "credential": token, "timeout": timeout,
        })
        if len(frame) > MAX_HTTP_INPUT:
            raise ClientError("invalid_request", "Internal HTTP input exceeds its bound.")
        try:
            output = _execute(
                [sys.executable, "-I", "-m", "agent_chat_service.transport", "--http-worker"],
                timeout=timeout, payload=frame, max_capture=MAX_PAGE_BYTES + 512,
            )
        except ClientError as error:
            if error.code == "provider_timeout":
                raise ClientError("http_deadline", "The total HTTP operation deadline expired; no automatic retry.") from None
            raise
        header, separator, data = output.partition(b"\n")
        try:
            if not separator or len(header) > 512:
                raise ValueError("HTTP worker framing")
            control = strict_json(header)
            if (not isinstance(control, dict) or set(control) != {"schema", "version", "status"}
                    or control["schema"] != HTTP_RESULT or type(control["version"]) is not int
                    or control["version"] != 1 or type(control["status"]) is not int
                    or not 100 <= control["status"] <= 599):
                raise ValueError("HTTP worker result")
            result = strict_json(data, max_bytes=MAX_PAGE_BYTES)
            if not isinstance(result, dict):
                raise ValueError("HTTP response object")
        except (ChatError, ValueError):
            raise ClientError("invalid_response", "Invalid owned HTTP worker reply; no automatic retry.") from None
        return self._result(control["status"], result)


def _http_exchange(frame: dict) -> tuple[int, bytes]:
    if (not isinstance(frame, dict) or set(frame) != {"endpoint", "payload", "credential", "timeout"}
            or type(frame["timeout"]) not in (int, float) or not math.isfinite(frame["timeout"])
            or not 0 < frame["timeout"] <= 315):
        raise ClientError("invalid_request", "Invalid internal HTTP controls.")
    parsed, _ = _origin(frame["endpoint"])
    validate_request(frame["payload"])
    raw = json_bytes(frame["payload"])
    if len(raw) > MAX_BODY_BYTES or not isinstance(frame["credential"], str):
        raise ClientError("invalid_request", "Invalid internal HTTP input.")
    headers = authorization(frame["credential"])
    headers["Content-Type"] = "application/json"
    connection_type = http.client.HTTPSConnection if parsed.scheme == "https" else http.client.HTTPConnection
    connection = connection_type(parsed.hostname, parsed.port, timeout=frame["timeout"])
    try:
        connection.request("POST", "/api/v1/chat", body=raw, headers=headers)
        response = connection.getresponse()
        if (response.getheader("Content-Type", "").split(";", 1)[0].strip() != "application/json"
                or response.getheader("Content-Encoding")):
            raise ClientError("invalid_response", "Invalid HTTP response metadata.")
        data = response.read(MAX_PAGE_BYTES + 1)
        if len(data) > MAX_PAGE_BYTES or response.length not in (None, 0):
            raise ClientError("invalid_response", "Oversized or incomplete HTTP response.")
        return response.status, data
    except (OSError, http.client.HTTPException, ValueError, UnicodeError) as error:
        raise ClientError(
            "transport_unavailable", f"Transport failed: {type(error).__name__}; no automatic replay.",
        ) from None
    finally:
        connection.close()


def _http_worker() -> int:
    try:
        raw = sys.stdin.buffer.read(MAX_HTTP_INPUT + 1)
        if len(raw) > MAX_HTTP_INPUT:
            raise ClientError("invalid_request", "Internal HTTP input exceeds its bound.")
        status, data = _http_exchange(strict_json(raw, max_bytes=MAX_HTTP_INPUT))
    except (ClientError, ChatError) as error:
        status, data = 503, json_bytes({"error": error.code})
    header = json_bytes({"schema": HTTP_RESULT, "version": 1, "status": status})
    sys.stdout.buffer.write(header + b"\n" + data)
    sys.stdout.buffer.flush()
    return 0


if __name__ == "__main__":
    if sys.argv[1:] != ["--http-worker"]:
        raise SystemExit("Internal owned HTTP worker.")
    raise SystemExit(_http_worker())
