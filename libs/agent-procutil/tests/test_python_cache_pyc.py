"""Source provenance controls; no untrusted marshal deserialization."""
from __future__ import annotations

import importlib.util
import base64
import marshal
import struct
import sys
import types

import pytest

from agent_procutil import python_cache_pyc as pyc
from python_cache_pyc_fixtures import BYTECODE, FILENAME


def encoded(code):
    return importlib.util.MAGIC_NUMBER + b"\0" * 12 + marshal.dumps(code)


@pytest.mark.parametrize("optimize", [0, 1, 2])
@pytest.mark.parametrize("name", ["/fixture/versions/old/module.py",
                                  "C:\\fixture\\versions\\old\\module.py", "/fixture/caf\u00e9.py"])
def test_real_compiled_nested_code_and_referenced_filenames(name, optimize):
    code = compile(
        'class Example:\n def method(self):\n  return lambda: (1, 2.5, 3j, "text", b"bytes")\n',
        name, "exec", optimize=optimize,
    )
    assert pyc.source(encoded(code)) == name


def test_nonexecuting_parser_never_calls_marshal_loads(monkeypatch):
    data = encoded(compile("raise RuntimeError('must not execute')", "/fixture/module.py", "exec"))

    def forbidden(*args, **kwargs):
        pytest.fail("the bytecode reader must not deserialize or execute code")

    monkeypatch.setattr(marshal, "loads", forbidden)
    assert pyc.source(data) == "/fixture/module.py"


@pytest.mark.parametrize("mutation", ["magic", "flags", "trailing", "truncated", "noncode", "oversize"])
def test_unknown_or_malformed_bytecode_is_refused(mutation):
    data = encoded(compile("answer = 42", "/fixture/module.py", "exec"))
    if mutation == "magic":
        data = b"nope" + data[4:]
    elif mutation == "flags":
        data = data[:4] + struct.pack("<I", 2) + data[8:]
    elif mutation == "trailing":
        data += b"extra"
    elif mutation == "truncated":
        data = data[:-1]
    elif mutation == "noncode":
        data = encoded(("not", "code"))
    else:
        data += b"x" * (8 * 1024 * 1024)
    with pytest.raises(ValueError):
        pyc.source(data)


def test_every_truncation_and_invalid_reference_is_refused():
    data = encoded(compile("def nested(): return 42", "/fixture/module.py", "exec"))
    for end in range(len(data)):
        with pytest.raises((ValueError, UnicodeError)):
            pyc.source(data[:end])
    with pytest.raises(ValueError, match="reference"):
        pyc.source(data[:16] + b"r" + struct.pack("<i", -1))


def test_nested_code_cannot_claim_a_different_source():
    code = compile("def nested(): return 42", "/fixture/old.py", "exec")
    consts = tuple(
        item.replace(co_filename="/fixture/retained.py") if isinstance(item, types.CodeType) else item
        for item in code.co_consts
    )
    with pytest.raises(ValueError, match="ambiguous"):
        pyc.source(encoded(code.replace(co_consts=consts)))


@pytest.mark.parametrize("payload", [
    b"(" + struct.pack("<i", 2**31 - 1),
    b"l" + struct.pack("<i", -(2**31)),
    b"[" + struct.pack("<i", -1),
    b")\x01" * 66 + b"N",
    b"\xa9\x01r" + struct.pack("<i", 0),
])
def test_forged_lengths_depth_and_cycles_are_bounded(payload):
    with pytest.raises(ValueError):
        pyc.source(importlib.util.MAGIC_NUMBER + b"\0" * 12 + payload)


def test_parser_checks_deadline_between_values():
    data = encoded(compile("answer = 42", "/fixture/module.py", "exec"))
    checks = []

    def expired():
        checks.append(True)
        if len(checks) == 10:
            raise TimeoutError

    with pytest.raises(TimeoutError):
        pyc.source(data, check=expired)
    assert len(checks) == 10


@pytest.mark.parametrize("version", sorted(BYTECODE))
def test_actual_historical_formats_do_not_depend_on_the_running_interpreter(version, monkeypatch):
    data = base64.b64decode(BYTECODE[version])
    monkeypatch.setattr(sys, "version_info", (3, 14))
    assert pyc.source(data) == FILENAME
    assert pyc.cache_tag(data) == "cpython-" + version
