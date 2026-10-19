"""Strict, bounded HTTP/1.1 framing for a non-reconnecting daemon stream."""
from __future__ import annotations

import io
import re

from .models import VerificationError

MAX_BODY = 8 * 1024 * 1024
_MAX_HEADERS = 64 * 1024
_TOKEN = rb"[!#$%&'*+.^_`|~0-9A-Za-z-]+"
_CHUNK = re.compile(rb"[0-9A-Fa-f]+")
_FIELD = re.compile(_TOKEN)


def _line(source: io.BufferedReader, remaining: int) -> bytes:
    line = source.readline(min(8192, remaining) + 1)
    if len(line) > min(8192, remaining) or not line.endswith(b"\r\n"):
        raise VerificationError("Malformed or truncated daemon HTTP line")
    return line


def _headers(source: io.BufferedReader, *, trailers: bool = False) -> dict[bytes, list[bytes]]:
    result: dict[bytes, list[bytes]] = {}
    remaining = _MAX_HEADERS
    while True:
        line = _line(source, remaining)
        remaining -= len(line)
        if line == b"\r\n":
            return result
        name, separator, value = line[:-2].partition(b":")
        if not separator or _FIELD.fullmatch(name) is None or any(
            byte < 32 and byte != 9 or byte == 127 for byte in value
        ):
            raise VerificationError("Malformed daemon HTTP header")
        name = name.lower()
        if trailers and name in {b"content-length", b"transfer-encoding", b"connection", b"trailer"}:
            raise VerificationError("Daemon trailers cannot change response framing")
        result.setdefault(name, []).append(value.strip(b" \t"))


def _exact(source: io.BufferedReader, count: int) -> bytes:
    value = source.read(count)
    if len(value) != count:
        raise VerificationError("Truncated daemon HTTP body")
    return value


def read_response(source: io.BufferedReader) -> tuple[int, bytes]:
    """Accept only a complete persistent response within strict size limits.

    Chunk extensions are deliberately unsupported; no metadata omitted by this
    limited protocol can authorize a resource closeout.
    """
    status = _line(source, 8192)
    match = re.fullmatch(rb"HTTP/1\.1 ([0-9]{3})(?: [\x20-\x7e]*)?\r\n", status)
    if match is None:
        raise VerificationError("Malformed daemon HTTP status")
    code = int(match[1])
    headers = _headers(source)
    lengths = headers.get(b"content-length", [])
    transfers = headers.get(b"transfer-encoding", [])
    connection = b",".join(headers.get(b"connection", [])).lower()
    if b"close" in {part.strip() for part in connection.split(b",")}:
        raise VerificationError("Daemon response closes the bound connection")
    if len(lengths) > 1 or len(transfers) > 1 or lengths and transfers:
        raise VerificationError("Ambiguous daemon response framing")
    if code == 204:
        if transfers or lengths and lengths != [b"0"]:
            raise VerificationError("Unexpected daemon deletion response body")
        return code, b""
    if transfers:
        if transfers != [b"chunked"]:
            raise VerificationError("Unsupported daemon transfer encoding")
        body = bytearray()
        while True:
            size_text = _line(source, 8192)[:-2]
            if _CHUNK.fullmatch(size_text) is None or len(size_text) > 8:
                raise VerificationError("Invalid or unsupported daemon chunk size")
            size = int(size_text, 16)
            if size == 0:
                _headers(source, trailers=True)
                return code, bytes(body)
            if size > MAX_BODY - len(body):
                raise VerificationError("Oversized daemon chunked response")
            body.extend(_exact(source, size))
            if _exact(source, 2) != b"\r\n":
                raise VerificationError("Invalid daemon chunk delimiter")
    if len(lengths) != 1 or not re.fullmatch(rb"[0-9]{1,10}", lengths[0]):
        raise VerificationError("Missing or invalid daemon response length")
    size = int(lengths[0])
    if size > MAX_BODY:
        raise VerificationError("Oversized daemon response")
    return code, _exact(source, size)
