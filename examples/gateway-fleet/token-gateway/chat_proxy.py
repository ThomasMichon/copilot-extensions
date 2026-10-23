"""Experimental reference implementation; see the parent README."""

from __future__ import annotations

import http.client
import json
import math
import os
import re
import threading
import time
from urllib.parse import urlsplit

from socket_deadline import socket_deadline

MAX_BODY = 65536
MAX_RESPONSE = 1024 * 1024
REQUEST_OVERHEAD = 10.0
_ordinary = threading.BoundedSemaphore(8)
_waiters = threading.BoundedSemaphore(16)
_operations = {"join", "leave", "post", "read", "cursor", "wait", "scan", "members", "resolve"}


class ChatProxyError(Exception):
    def __init__(self, status: int, code: str):
        super().__init__(code)
        self.status, self.code = status, code


def _decode(raw: bytes) -> dict:
    def pairs(items):
        value = {}
        for key, entry in items:
            if key in value:
                raise ValueError("duplicate field")
            value[key] = entry
        return value

    def constant(_value):
        raise ValueError("non-finite number")

    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=pairs, parse_constant=constant)
        json.dumps(value, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise ChatProxyError(400, "chat_invalid_json") from exc
    if not isinstance(value, dict):
        raise ChatProxyError(400, "chat_invalid_json")
    return value


def read_request(handler) -> dict:
    lengths = handler.headers.get_all("Content-Length", [])
    if (len(lengths) != 1 or not re.fullmatch(r"[0-9]{1,6}", lengths[0])
            or not 0 < int(lengths[0]) <= MAX_BODY
            or handler.headers.get("Transfer-Encoding")
            or handler.headers.get("Content-Encoding")
            or handler.headers.get("Content-Type", "").split(";", 1)[0].strip()
            != "application/json"):
        raise ChatProxyError(400, "chat_invalid_request")
    previous = handler.connection.gettimeout()
    try:
        handler.connection.settimeout(5)
        raw = handler.rfile.read(int(lengths[0]))
    except (OSError, TimeoutError) as exc:
        raise ChatProxyError(408, "chat_request_timeout") from exc
    finally:
        handler.connection.settimeout(previous)
    if len(raw) != int(lengths[0]):
        raise ChatProxyError(400, "chat_incomplete_request")
    return _decode(raw)


def forward(payload: dict) -> dict:
    operation = payload.get("op")
    if not isinstance(operation, str) or operation not in _operations:
        raise ChatProxyError(400, "chat_invalid_operation")
    long_poll = operation in {"wait", "scan"}
    wait = payload.get("timeout", 30) if long_poll else 0
    if (type(wait) not in (int, float) or not math.isfinite(wait)
            or not 0 <= wait <= 300):
        raise ChatProxyError(400, "chat_invalid_timeout")
    slot = _waiters if long_poll else _ordinary
    if not slot.acquire(blocking=False):
        raise ChatProxyError(503, "chat_gateway_busy")
    try:
        return _forward(payload, wait)
    finally:
        slot.release()


def _forward(payload: dict, wait: float) -> dict:
    endpoint = os.environ.get("HGF_CHAT_ENDPOINT", "")
    identity = os.environ.get("HGF_CHAT_INSTALLATION_ID", "")
    if not endpoint or not identity:
        raise ChatProxyError(503, "chat_not_configured")
    try:
        backend = urlsplit(endpoint)
        port = backend.port
    except ValueError as exc:
        raise ChatProxyError(503, "chat_configuration_invalid") from exc
    if (backend.scheme not in {"http", "https"} or not backend.hostname
            or backend.username or backend.password or backend.path not in {"", "/"}
            or backend.query or backend.fragment or "?" in endpoint or "#" in endpoint
            or any(ord(c) <= 32 or ord(c) >= 127 for c in endpoint)
            or not 1 <= len(identity) <= 128
            or any(ord(c) <= 32 or ord(c) >= 127 for c in identity)
            or port is not None and not 1 <= port <= 65535):
        raise ChatProxyError(503, "chat_configuration_invalid")
    connection_type = (
        http.client.HTTPSConnection if backend.scheme == "https"
        else http.client.HTTPConnection
    )
    deadline = time.monotonic() + wait + REQUEST_OVERHEAD

    def request(method: str, path: str, body=None, *, timeout: float = 5):
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ChatProxyError(504, "chat_backend_timeout")
        connection = connection_type(backend.hostname, port, timeout=min(timeout, 3, remaining))
        try:

            headers = {"Content-Type": "application/json"}
            connection.request(method, path, body=body, headers=headers)
            if connection.sock is not None:
                connection.sock.settimeout(timeout)
            if connection.sock is None:
                raise OSError("Backend has no socket")
            with socket_deadline(connection.sock, deadline), connection.getresponse() as response:
                if time.monotonic() >= deadline:
                    raise TimeoutError
                if (response.getheader("Content-Type", "").split(";", 1)[0].strip()
                        != "application/json" or response.getheader("Content-Encoding")):
                    raise ChatProxyError(502, "chat_backend_invalid_response")
                raw = bytearray()
                while True:
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        raise ChatProxyError(504, "chat_backend_timeout")
                    if connection.sock is not None:
                        connection.sock.settimeout(min(timeout, remaining))
                    block = response.read1(min(65536, MAX_RESPONSE + 1 - len(raw)))
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    raw.extend(block)
                    if len(raw) > MAX_RESPONSE:
                        raise ChatProxyError(502, "chat_backend_response_too_large")
                    if not block:
                        break
                if response.length not in (None, 0):
                    raise ChatProxyError(502, "chat_backend_invalid_response")
                try:
                    result = _decode(bytes(raw))
                except ChatProxyError as exc:
                    raise ChatProxyError(502, "chat_backend_invalid_response") from exc
                if response.status != 200:
                    code = result.get("error")
                    if not isinstance(code, str) or not re.fullmatch(r"[a-z][a-z0-9_]{0,63}", code):
                        raise ChatProxyError(502, "chat_backend_failed")
                    if not code.startswith("chat_"):
                        code = "chat_" + code
                    status = response.status if response.status in {400, 404, 409, 413, 429, 503} else 502
                    raise ChatProxyError(status, code)
                return result
        except TimeoutError as exc:
            raise ChatProxyError(504, "chat_backend_timeout") from exc
        except (OSError, http.client.HTTPException) as exc:
            raise ChatProxyError(503, "chat_backend_unavailable") from exc
        finally:
            connection.close()

    health = request("GET", "/health")
    if (health.get("status") != "ok" or health.get("service") != "chat"
            or type(health.get("schema")) is not int or health.get("schema") != 1
            or health.get("installation_id") != identity):
        raise ChatProxyError(503, "chat_backend_identity_mismatch")
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    if len(raw) > MAX_BODY:
        raise ChatProxyError(413, "chat_request_too_large")
    return request("POST", "/v1/chat", raw, timeout=wait + 5)
