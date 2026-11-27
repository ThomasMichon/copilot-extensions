"""Keep the bootstrap reader's byte lookup exact under the C locale."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import subprocess

import pytest

READER = Path(__file__).resolve().parents[1] / "json-query.awk"
AWK = shutil.which("awk")
pytestmark = pytest.mark.skipif(AWK is None, reason="awk is unavailable")


def _query(document: bytes, *, mode: str = "hex") -> subprocess.CompletedProcess[bytes]:
    assert AWK is not None
    return subprocess.run(
        [AWK, "-f", str(READER), "-v", f"mode={mode}", "-v", "query_path=value"],
        input=document,
        capture_output=True,
        env={**os.environ, "LC_ALL": "C"},
        check=False,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0,
    )


@pytest.mark.parametrize("ensure_ascii", [False, True])
def test_hex_query_preserves_every_utf8_byte(ensure_ascii: bool) -> None:
    value = "".join(chr(index) for index in range(128))
    value += "".join(chr(index) for index in range(128, 2048))
    value += "\u0800\ud7ff\ue000\uffff\U00010000\U0010ffff"
    result = _query(json.dumps({"value": value}, ensure_ascii=ensure_ascii).encode())
    assert result.returncode == 0, result.stderr
    assert result.stdout == value.encode().hex().encode()


@pytest.mark.parametrize("invalid", [
    *[bytes([index]) for index in range(128, 256)],
    b"\xc0\x80", b"\xe0\x80\x80", b"\xed\xa0\x80",
    b"\xf0\x80\x80\x80", b"\xf4\x90\x80\x80", b"\xf5\x80\x80\x80",
    b"\xc2", b"\xe0\xa0", b"\xf0\x90\x80", b"\xc2\x7f",
])
def test_hex_query_still_refuses_invalid_utf8(invalid: bytes) -> None:
    result = _query(b'{"value":"' + invalid + b'"}')
    assert result.returncode == 2
    assert b"invalid UTF-8" in result.stderr


def test_hex_query_still_refuses_duplicate_keys() -> None:
    result = _query(b'{"value":"first","value":"second"}')
    assert result.returncode == 2
    assert b"duplicate" in result.stderr.lower()


@pytest.mark.parametrize("escaped", [
    b"\\u00gg", b"\\u000", b"\\ud800", b"\\udc00",
    b"\\ud800\\u0000", b"\\ud800\\udc0g",
])
def test_string_query_refuses_invalid_unicode_escape_quads(escaped: bytes) -> None:
    result = _query(b'{"value":"' + escaped + b'"}', mode="string")
    assert result.returncode == 2
    assert result.stdout == b""


@pytest.mark.parametrize("ensure_ascii", [False, True])
@pytest.mark.parametrize("value", ['/managed quote"back\\slash/\u00e9\U0001f600/bin/python', "line\n"])
def test_string_query_preserves_decoded_bytes(ensure_ascii: bool, value: str) -> None:
    result = _query(json.dumps({"value": value}, ensure_ascii=ensure_ascii).encode(), mode="string")
    assert result.returncode == 0, result.stderr
    assert result.stdout == value.encode()


@pytest.mark.parametrize("value", [None, 42, False, [], {}, "/managed/\x00/python"])
def test_string_query_refuses_nonstrings_and_nul_before_shell_capture(value: object) -> None:
    result = _query(json.dumps({"value": value}).encode(), mode="string")
    assert result.returncode == 2
    assert result.stdout == b""


@pytest.mark.parametrize("mode,prefix,status", [
    ("deployment-string", b"\xef\xbb\xbf", 0),
    ("string", b"\xef\xbb\xbf", 2),
    ("deployment-string", b"\xef\xbb\xbf\xef\xbb\xbf", 2),
])
def test_deployment_string_bom_compatibility_is_not_protocol_relaxation(
    mode: str, prefix: bytes, status: int,
) -> None:
    value = "/managed/\u00e9/bin/python"
    result = _query(prefix + json.dumps({"value": value}).encode(), mode=mode)
    assert result.returncode == status
    assert result.stdout == (value.encode() if status == 0 else b"")
