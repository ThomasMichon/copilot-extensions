"""Strict framing contract also executes on platforms without Unix sockets."""
from __future__ import annotations

import io

import pytest

from docker_resource_producer import VerificationError
from docker_resource_producer.http_stream import read_response


@pytest.mark.parametrize("body", [
    b"2\r\n[]\r\n0\r\n\r\n",
    b"1\r\n[\r\n1\r\n]\r\n0\r\nX-Result: complete\r\n\r\n",
])
def test_complete_chunked_body_and_trailers(body: bytes) -> None:
    with io.BufferedReader(io.BytesIO(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + body)) as source:
        assert read_response(source) == (200, b"[]")


@pytest.mark.parametrize("body", [
    b"2\r\n[]\r\n0\r\n", b"2\r\n[]XX0\r\n\r\n",
    b"2\r\n[]\r\n0\r\nX-End: missing-terminator\r\n",
    b"2\r\n[]\r\n0\r\nContent-Length: 2\r\n\r\n",
    b"2\r\n[]\r\n0\r\n Bad-Name: folded\r\n\r\n",
    b"+2\r\n[]\r\n0\r\n\r\n", b"2 \r\n[]\r\n0\r\n\r\n",
    b"2;extension=value\r\n[]\r\n0\r\n\r\n",
    b"800001\r\n", b"2\r\n[", b"2\n[]\r\n0\r\n\r\n",
])
def test_partial_ambiguous_or_unsupported_chunks_refuse(body: bytes) -> None:
    with io.BufferedReader(io.BytesIO(b"HTTP/1.1 200 OK\r\nTransfer-Encoding: chunked\r\n\r\n" + body)) as source:
        with pytest.raises(VerificationError):
            read_response(source)


@pytest.mark.parametrize("wire", [
    b"HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\n[]",
    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nContent-Length: 2\r\n\r\n[]",
    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nTransfer-Encoding: chunked\r\n\r\n[]",
    b"HTTP/1.1 200 OK\r\nTransfer-Encoding: gzip, chunked\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 2\r\nConnection: keep-alive, close\r\n\r\n[]",
    b"HTTP/1.1 200 OK\r\nContent-Length: +2\r\n\r\n[]",
    b"HTTP/1.1 200 OK\r\nContent-Length: 3\r\n\r\n[]",
    b"HTTP/1.1 204 No Content\r\nContent-Length: 2\r\n\r\n[]",
    b"HTTP/1.1 204 No Content\r\nTransfer-Encoding: chunked\r\n\r\n0\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nContent-Length: 8388609\r\n\r\n",
    b"HTTP/1.1 200 OK\r\nBad Header: value\r\nContent-Length: 2\r\n\r\n[]",
    b"HTTP/1.1 200 OK\r\nX-Test: \x00\r\nContent-Length: 2\r\n\r\n[]",
])
def test_response_framing_refuses(wire: bytes) -> None:
    with io.BufferedReader(io.BytesIO(wire)) as source:
        with pytest.raises(VerificationError):
            read_response(source)


def test_header_and_line_budgets_are_bounded() -> None:
    wire = b"HTTP/1.1 200 OK\r\n" + b"X-Test: " + b"a" * 8192 + b"\r\n\r\n"
    with io.BufferedReader(io.BytesIO(wire)) as source:
        with pytest.raises(VerificationError):
            read_response(source)


def test_empty_deletion_response_needs_no_length_header() -> None:
    with io.BufferedReader(io.BytesIO(b"HTTP/1.1 204 No Content\r\n\r\n")) as source:
        assert read_response(source) == (204, b"")
