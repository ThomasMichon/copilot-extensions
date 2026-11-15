"""Source provenance controls; no untrusted marshal deserialization."""
from __future__ import annotations

import importlib.util
import base64
import marshal
import struct
import sys

import pytest

from agent_procutil import python_cache_pyc as pyc
from python_cache_pyc_fixtures import BYTECODE, FILENAME, code_record, native_format


def encoded(code):
    return importlib.util.MAGIC_NUMBER + b"\0" * 12 + marshal.dumps(code)


@pytest.mark.parametrize("optimize", [0, 1, 2])
@pytest.mark.parametrize("name", ["/fixture/versions/old/module.py",
                                  "C:\\fixture\\versions\\old\\module.py", "/fixture/caf\u00e9.py"])
def test_native_compiled_nested_code_is_admitted_only_for_qualified_formats(name, optimize):
    code = compile(
        'class Example:\n def method(self):\n  return lambda: (1, 2.5, 3j, "text", b"bytes")\n',
        name, "exec", optimize=optimize,
    )
    data = encoded(code)
    if native_format() is None:
        with pytest.raises(ValueError, match="unsupported"):
            pyc.source(data)
    else:
        assert pyc.source(data) == name


def test_nonexecuting_parser_never_calls_marshal_loads(monkeypatch):
    data = base64.b64decode(BYTECODE["313"])

    def forbidden(*args, **kwargs):
        pytest.fail("the bytecode reader must not deserialize or execute code")

    monkeypatch.setattr(marshal, "loads", forbidden)
    assert pyc.source(data) == FILENAME


@pytest.mark.parametrize("mutation", ["magic", "flags", "trailing", "truncated", "noncode", "oversize"])
def test_unknown_or_malformed_bytecode_is_refused(mutation):
    data = code_record()
    if mutation == "magic":
        data = b"nope" + data[4:]
    elif mutation == "flags":
        data = data[:4] + struct.pack("<I", 2) + data[8:]
    elif mutation == "trailing":
        data += b"extra"
    elif mutation == "truncated":
        data = data[:-1]
    elif mutation == "noncode":
        data = data[:16] + marshal.dumps(("not", "code"), 0)
    else:
        data += b"x" * (8 * 1024 * 1024)
    with pytest.raises(ValueError):
        pyc.source(data)


def test_every_truncation_and_invalid_reference_is_refused():
    data = code_record()
    for end in range(len(data)):
        with pytest.raises((ValueError, UnicodeError)):
            pyc.source(data[:end])
    with pytest.raises(ValueError, match="reference"):
        pyc.source(data[:16] + b"r" + struct.pack("<i", -1))


def test_nested_code_cannot_claim_a_different_source():
    nested = code_record(filename="/fixture/retained.py")[16:]
    data = code_record(raw_fields={"consts": b")\x01" + nested})
    with pytest.raises(ValueError, match="ambiguous"):
        pyc.source(data)


@pytest.mark.parametrize("payload", [
    b"(" + struct.pack("<i", 2**31 - 1),
    b"l" + struct.pack("<i", -(2**31)),
    b"[" + struct.pack("<i", -1),
    b")\x01" * 66 + b"N",
    b"\xa9\x01r" + struct.pack("<i", 0),
])
def test_forged_lengths_depth_and_cycles_are_bounded(payload):
    with pytest.raises(ValueError):
        pyc.source(code_record()[:16] + payload)


def test_parser_checks_deadline_between_values():
    data = code_record()
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


@pytest.mark.parametrize("version", ["310", "313"])
def test_minimal_code_field_controls_have_valid_framing(version):
    assert pyc.source(code_record(version)) == FILENAME


@pytest.mark.parametrize("version,field", [
    (version, field)
    for version, fields in (
        ("310", ("code", "consts", "names", "locals", "free", "cells", "line")),
        ("313", ("code", "consts", "names", "locals", "kinds", "line", "exception")),
    )
    for field in fields
])
def test_required_code_fields_reject_none_in_every_supported_layout(version, field):
    with pytest.raises(ValueError):
        pyc.source(code_record(version, **{field: None}))


@pytest.mark.parametrize("version", ["310", "313"])
@pytest.mark.parametrize("field", ["names", "locals"])
def test_required_name_tuples_reject_non_string_members(version, field):
    with pytest.raises(ValueError):
        pyc.source(code_record(version, **{field: (1,)}))


@pytest.mark.parametrize("payload", [
    b"f\x04nope",
    b"l\x01\0\0\0\0\x80",
    b"l\x01\0\0\0\0\0",
    b"l\xff\xff\xff\xff\0\0",
    b"l\x02\0\0\0\x01\0\0\0",
    b"u" + struct.pack("<i", 20000) + b"\xff" * 20000,
    b">" + struct.pack("<i", 1) + b"[" + struct.pack("<i", 0),
    b"{" + b"[" + struct.pack("<i", 0) + b"N0",
])
def test_malformed_scalar_and_container_constants_do_not_supply_provenance(payload):
    constants = b"(" + struct.pack("<i", 1) + payload
    with pytest.raises(ValueError):
        pyc.source(code_record(raw_fields={"consts": constants}))


@pytest.mark.parametrize("value", [0, 1, -1, 2**15, -(2**15), 2**100, -(2**100)])
def test_normalized_integer_constants_remain_admitted(value):
    assert pyc.source(code_record(consts=(value,))) == FILENAME
