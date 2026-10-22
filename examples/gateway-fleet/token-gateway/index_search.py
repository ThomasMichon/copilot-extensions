"""Experimental reference implementation; see the parent README."""

from __future__ import annotations

import http.client
import json
import logging
import os
import re
import threading
import time
from dataclasses import dataclass
from urllib.parse import parse_qsl, urlencode, urlsplit

log = logging.getLogger("hgf.gateway.index")

MAX_QUERY_LENGTH = 2048
MAX_FILTER_LENGTH = 256
MAX_QUERY_STRING_BYTES = 16384
MAX_RESPONSE_BYTES = 1024 * 1024
CONNECT_TIMEOUT = 3.0
READ_TIMEOUT = 5.0
REQUEST_TIMEOUT = 10.0
_PARAMETERS = frozenset({"q", "source", "language", "repo", "limit"})
_slots = threading.BoundedSemaphore(8)


class IndexSearchError(Exception):
    def __init__(self, status: int, code: str) -> None:
        super().__init__(code)
        self.status = status
        self.code = code


@dataclass(frozen=True)
class Backend:
    scheme: str
    host: str
    port: int | None
    installation_id: str


def _backend() -> Backend:
    endpoint = os.environ.get("HGF_INDEX_ENDPOINT", "")
    installation_id = os.environ.get("HGF_INDEX_INSTALLATION_ID", "")
    if not endpoint or not installation_id:
        raise IndexSearchError(503, "index_not_configured")
    try:
        parsed = urlsplit(endpoint)
        port = parsed.port
        valid = (
            parsed.scheme in {"http", "https"}
            and parsed.hostname
            and parsed.username is None
            and parsed.password is None
            and parsed.path in {"", "/"}
            and not parsed.query
            and not parsed.fragment
            and "?" not in endpoint
            and "#" not in endpoint
            and all(32 < ord(c) < 127 for c in endpoint)
            and 0 < len(installation_id) <= 256
            and all(32 < ord(c) < 127 for c in installation_id)
            and (port is None or 1 <= port <= 65535)
        )
    except ValueError as exc:
        raise IndexSearchError(503, "index_configuration_invalid") from exc
    if not valid:
        raise IndexSearchError(503, "index_configuration_invalid")
    return Backend(parsed.scheme, parsed.hostname, port, installation_id)


def _parameters(raw_query: str) -> dict[str, str]:
    if len(raw_query.encode("utf-8")) > MAX_QUERY_STRING_BYTES:
        raise IndexSearchError(400, "index_invalid_parameters")
    if re.search(r"%(?![0-9a-fA-F]{2})", raw_query):
        raise IndexSearchError(400, "index_invalid_parameters")
    try:
        pairs = parse_qsl(
            raw_query, keep_blank_values=True, strict_parsing=True,
            encoding="utf-8", errors="strict", max_num_fields=len(_PARAMETERS),
        )
    except ValueError as exc:
        raise IndexSearchError(400, "index_invalid_parameters") from exc
    parameters: dict[str, str] = {}
    for name, value in pairs:
        if name not in _PARAMETERS or name in parameters:
            raise IndexSearchError(400, "index_invalid_parameters")
        bound = MAX_QUERY_LENGTH if name == "q" else MAX_FILTER_LENGTH
        if not value.strip() or len(value) > bound:
            raise IndexSearchError(400, "index_invalid_parameters")
        if any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise IndexSearchError(400, "index_invalid_parameters")
        parameters[name] = value
    if "q" not in parameters:
        raise IndexSearchError(400, "index_invalid_parameters")
    limit = parameters.get("limit", "10")
    if not re.fullmatch(r"[0-9]{1,3}", limit) or not 1 <= int(limit) <= 100:
        raise IndexSearchError(400, "index_invalid_parameters")
    parameters["limit"] = str(int(limit))
    return parameters


def _unique_object(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate JSON field")
        result[key] = value
    return result


def _invalid_constant(value: str) -> None:
    raise ValueError("non-finite JSON number")


def _response(raw: bytes, parameters: dict[str, str]) -> dict:
    try:
        payload = json.loads(
            raw.decode("utf-8"), object_pairs_hook=_unique_object,
            parse_constant=_invalid_constant,
        )
        valid = (
            isinstance(payload, dict)
            and payload.get("query") == parameters["q"]
            and type(payload.get("available")) is bool
            and isinstance(payload.get("hits"), list)
            and len(payload["hits"]) <= int(parameters["limit"])
            and all(isinstance(hit, dict) for hit in payload["hits"])
        )

        if valid:
            json.dumps(payload, ensure_ascii=False, allow_nan=False).encode("utf-8")
    except (ValueError, UnicodeError, RecursionError) as exc:
        raise IndexSearchError(502, "index_invalid_response") from exc
    if not valid:
        raise IndexSearchError(502, "index_invalid_response")
    if not payload["available"]:
        raise IndexSearchError(503, "index_unavailable")
    result = {name: payload[name] for name in ("query", "available", "hits")}
    if len(json.dumps(result).encode("utf-8")) > MAX_RESPONSE_BYTES:
        raise IndexSearchError(502, "index_response_too_large")
    return result


def _fetch(backend: Backend, parameters: dict[str, str]) -> dict:
    connection_type = (
        http.client.HTTPSConnection if backend.scheme == "https"
        else http.client.HTTPConnection
    )
    connection = connection_type(
        backend.host, backend.port, timeout=CONNECT_TIMEOUT,
    )
    deadline = time.monotonic() + REQUEST_TIMEOUT

    def read_json(path: str, bound: int) -> bytes:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        if connection.sock is None:
            connection.timeout = min(CONNECT_TIMEOUT, remaining)
            connection.connect()
        if connection.sock is None:
            raise OSError("backend connection has no socket")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError
        sock = connection.sock
        sock.settimeout(min(READ_TIMEOUT, remaining))
        connection.request(
            "GET", path,
            headers={
                "Accept": "application/json",
                "Accept-Encoding": "identity",
                "X-Agent-Index-Installation-Id": backend.installation_id,
            },
        )
        with connection.getresponse() as response:
            if response.status != 200:

                raise IndexSearchError(502, "index_backend_rejected")
            content_type = response.getheader("Content-Type", "").split(";")[0]
            if content_type.strip().lower() != "application/json":
                raise IndexSearchError(502, "index_invalid_response")
            if response.getheader("Content-Encoding", "identity").lower() != "identity":
                raise IndexSearchError(502, "index_invalid_response")
            length = response.getheader("Content-Length")
            if length is not None:
                if not re.fullmatch(r"[0-9]{1,20}", length):
                    raise IndexSearchError(502, "index_invalid_response")
                if int(length) > bound:
                    raise IndexSearchError(502, "index_response_too_large")
            chunks = bytearray()
            while not response.isclosed():
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise TimeoutError

                sock.settimeout(min(READ_TIMEOUT, remaining))
                chunk = response.read1(min(65536, bound + 1 - len(chunks)))
                if time.monotonic() >= deadline:
                    raise TimeoutError
                if not chunk:
                    break
                chunks.extend(chunk)
                if len(chunks) > bound:
                    raise IndexSearchError(502, "index_response_too_large")
            if length is not None and len(chunks) != int(length):
                raise IndexSearchError(502, "index_invalid_response")
            return bytes(chunks)

    try:
        raw_health = read_json("/health", 65536)
        try:
            health = json.loads(
                raw_health.decode("utf-8"), object_pairs_hook=_unique_object,
                parse_constant=_invalid_constant,
            )
        except (ValueError, UnicodeError, RecursionError) as exc:
            raise IndexSearchError(502, "index_invalid_response") from exc
        if not isinstance(health, dict):
            raise IndexSearchError(502, "index_invalid_response")

        if health.get("installationId") != backend.installation_id:
            raise IndexSearchError(503, "index_identity_mismatch")
        if health.get("status") != "ok":
            raise IndexSearchError(503, "index_unavailable")
        return _response(
            read_json("/search?" + urlencode(parameters), MAX_RESPONSE_BYTES),
            parameters,
        )
    except TimeoutError as exc:
        raise IndexSearchError(504, "index_backend_timeout") from exc
    except http.client.HTTPException as exc:
        raise IndexSearchError(502, "index_invalid_response") from exc
    except UnicodeError as exc:
        raise IndexSearchError(503, "index_configuration_invalid") from exc
    except OSError as exc:
        raise IndexSearchError(503, "index_backend_unreachable") from exc
    finally:
        connection.close()


def search_index(raw_query: str) -> dict:
    pass
    try:
        parameters = _parameters(raw_query)
        backend = _backend()
        if not _slots.acquire(blocking=False):
            raise IndexSearchError(503, "index_busy")
        try:
            return _fetch(backend, parameters)
        finally:
            _slots.release()
    except IndexSearchError as exc:
        log.warning("index search failed: status=%d code=%s", exc.status, exc.code)
        raise
