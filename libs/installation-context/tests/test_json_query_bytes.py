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


def _query(document: bytes) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        [AWK, "-f", str(READER), "-v", "mode=hex", "-v", "query_path=value"],
        input=document,
        capture_output=True,
        env={**os.environ, "LC_ALL": "C"},
        check=False,
        timeout=10,
        **({"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}),
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
