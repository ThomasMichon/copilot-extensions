"""Real archive controls for the dependency-free wheel inspection boundary."""
from __future__ import annotations

import base64
from dataclasses import replace
import hashlib
import io
import itertools
import json
import os
from pathlib import Path
import subprocess
import struct
import sys
import time
from types import SimpleNamespace
import zipfile
import zlib

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
import installer_wheel_inputs as inspection
import runtime_profile
from runtime_profile_wheels import verify_profile_wheels
from wheel_input_fixture import PREFIX, wheel_bytes


@pytest.fixture
def limits():
    return inspection.WheelLimits(
        max_archive_bytes=100_000, max_expanded_bytes=100_000,
        max_member_bytes=20_000, max_metadata_bytes=10_000, max_members=30,
        max_tags=100,
        max_metadata_member_bytes=10_000,
    )


@pytest.fixture
def wheel_path(tmp_path):
    path = tmp_path / "demo_pkg-1.0-py3-none-any.whl"
    path.write_bytes(wheel_bytes())
    return path


def inspect(path, limits, **kwargs):
    return inspection.inspect_wheel(
        path, sha256="sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        limits=limits, deadline=time.monotonic() + 10, **kwargs,
    )


def _verify_selected(path, entry, *, limits, deadline):
    from packaging.markers import default_environment

    helper = wheel_bytes(
        name="helper", prefix="helper-2.0.dist-info/",
        metadata=b"Metadata-Version: 2.4\nName: helper\nVersion: 2.0\n\n",
    )
    helper_entry = {
        "name": "helper", "version": "2.0", "filename": "helper-2.0-py3-none-any.whl",
        "sha256": "sha256:" + hashlib.sha256(helper).hexdigest(),
        "requires_python": "", "provides_extras": [], "selected_extras": [], "requires_dist": [],
    }
    record = {
        "schema": runtime_profile.SCHEMA, "version": 1,
        "release": {"identity": "fixture-release", "sha256": "sha256:" + "2" * 64},
        "mode": "source", "artifact": None, "component": "index-engine", "backend": "cpu",
        "approval_source": "fixture-authority", "acquisition_policy": "fixture-policy",
        "target": {"device": "cpu", "environment": default_environment(), "tags": ["py3-none-any"]},
        "roots": ["demo-pkg[fast]==1.0"], "wheels": [entry, helper_entry],
    }
    # A frozen record is still revalidated by the complete-set byte API.
    profile = runtime_profile.RuntimeProfile(json.dumps(record).encode())
    with path.open("rb") as stream:
        verify_profile_wheels(
            profile, {path.name: stream, helper_entry["filename"]: io.BytesIO(helper)},
            max_archive_bytes=limits.max_archive_bytes,
            max_unpacked_bytes=limits.max_expanded_bytes,
            max_metadata_bytes=limits.max_metadata_member_bytes,
            limits=limits, deadline=deadline,
        )


def test_inspects_actual_metadata_record_and_tags(wheel_path, limits):
    facts = inspect(wheel_path, limits)
    assert facts.name == "demo-pkg"
    assert facts.version == "1.0"
    assert facts.requires_python == ">=3.10"
    assert facts.provides_extras == ("fast",)
    assert facts.requires_dist == ("helper>=2; extra == 'fast'",)
    assert facts.tags == ("py3-none-any",)
    assert facts.sha256 == "sha256:" + hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    assert facts.size == wheel_path.stat().st_size
    assert facts.member_hashes["demo_pkg/__init__.py"] == hashlib.sha256(b"VALUE = 1\n").hexdigest()


def test_wrong_archive_digest_refuses(wheel_path, limits):
    with pytest.raises(inspection.WheelError, match="digest"):
        inspection.inspect_wheel(
            wheel_path, sha256="sha256:" + "0" * 64, limits=limits,
            deadline=time.monotonic() + 10,
        )


def test_path_and_handle_identity_agree_after_an_ordinary_overwrite(wheel_path):
    wheel_path.write_bytes(wheel_path.read_bytes())
    before = wheel_path.lstat()
    os.utime(wheel_path, ns=(before.st_atime_ns, before.st_mtime_ns - 1_000_000_000))
    with wheel_path.open("rb") as stream:
        assert inspection._identity(wheel_path.lstat()) == inspection._identity(os.fstat(stream.fileno()))


@pytest.mark.parametrize("field,value", [
    ("max_archive_bytes", 10), ("max_expanded_bytes", 10),
    ("max_member_bytes", 10), ("max_metadata_bytes", 10), ("max_members", 2),
    ("max_metadata_member_bytes", 10),
])
def test_all_limits_are_enforced(wheel_path, limits, field, value):
    with pytest.raises(inspection.WheelError, match="limit"):
        inspect(wheel_path, replace(limits, **{field: value}))


def test_actual_directory_count_is_bounded_before_zipfile_allocation(wheel_path, limits, monkeypatch):
    data = bytearray(wheel_path.read_bytes())
    end = data.rfind(b"PK\x05\x06")
    struct.pack_into("<2H", data, end + 8, 0, 0)
    wheel_path.write_bytes(data)
    monkeypatch.setattr(inspection.zipfile, "ZipFile", lambda *_: pytest.fail("unbounded ZIP indexing"))
    with pytest.raises(inspection.WheelError, match="actual member count limit"):
        inspect(wheel_path, replace(limits, max_members=2))


def test_directory_bytes_are_bounded_before_zipfile_allocation(wheel_path, limits, monkeypatch):
    monkeypatch.setattr(inspection.zipfile, "ZipFile", lambda *_: pytest.fail("unbounded ZIP indexing"))
    with pytest.raises(inspection.WheelError, match="directory metadata limit"):
        inspect(wheel_path, replace(limits, max_metadata_bytes=10))


def test_directory_preflight_observes_deadline_before_zipfile_allocation(wheel_path, limits, monkeypatch):
    wheel_path.write_bytes(wheel_bytes(extra={f"extra{index}": b"x" for index in range(20)}))
    ticks = itertools.count(1)
    monkeypatch.setattr(inspection.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(inspection.zipfile, "ZipFile", lambda *_: pytest.fail("unbounded ZIP indexing"))
    with pytest.raises(inspection.WheelError, match="deadline"):
        inspection.inspect_wheel(
            wheel_path, sha256="sha256:" + hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
            limits=limits, deadline=15,
        )


def test_ordinary_zip64_directory_is_inspected(wheel_path, limits):
    data = wheel_path.read_bytes()
    end = data.rfind(b"PK\x05\x06")
    _, _, _, _, count, size, offset, _ = struct.unpack("<4s4H2LH", data[end:end + 22])
    record = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, size, offset)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, end, 1)
    footer = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xffff, 0xffff, 0xffffffff, 0xffffffff, 0)
    wheel_path.write_bytes(data[:end] + record + locator + footer)
    assert inspect(wheel_path, limits).name == "demo-pkg"


@pytest.mark.parametrize("count", [65_534, 65_535, 65_536])
def test_stdlib_directory_count_boundaries_allow_zip32_and_zip64(limits, count):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w", compression=zipfile.ZIP_STORED) as archive:
        for index in range(count):
            archive.writestr(f"entry{index}", b"")
    limits = replace(
        limits, max_archive_bytes=20 << 20, max_metadata_bytes=20 << 20,
        max_members=count,
    )
    inspection.preflight_archive(stream, limits=limits, deadline=time.monotonic() + 20)
    assert stream.tell() == 0
    with pytest.raises(inspection.WheelError, match="count limit"):
        inspection.preflight_archive(
            stream, limits=replace(limits, max_members=count - 1), deadline=time.monotonic() + 20,
        )


@pytest.mark.parametrize("offset", [2**63, 2**64 - 1])
def test_zip64_local_offset_is_bounded_before_member_open(wheel_path, limits, monkeypatch, offset):
    target = "demo_pkg/__init__.py"
    output = io.BytesIO()
    with zipfile.ZipFile(wheel_path) as source, zipfile.ZipFile(output, "w") as destination:
        for member in source.infolist():
            replacement = zipfile.ZipInfo(member.filename)
            replacement.compress_type = zipfile.ZIP_DEFLATED
            if member.filename == target:
                replacement.extra = struct.pack("<HHQ", 1, 8, offset)
            destination.writestr(replacement, source.read(member))
    data = bytearray(output.getvalue())
    end = data.rfind(b"PK\x05\x06")
    position = struct.unpack_from("<L", data, end + 16)[0]
    while data[position:position + 4] == b"PK\x01\x02":
        lengths = struct.unpack_from("<3H", data, position + 28)
        if data[position + 46:position + 46 + lengths[0]] == target.encode():
            struct.pack_into("<L", data, position + 42, 0xffffffff)
            break
        position += 46 + sum(lengths)
    else:
        pytest.fail("fixture member not found")
    wheel_path.write_bytes(data)
    original = inspection.zipfile.ZipFile.open

    def guarded_open(bundle, member, *args, **kwargs):
        if getattr(member, "filename", member) == target:
            pytest.fail("unbounded local-header seek")
        return original(bundle, member, *args, **kwargs)

    monkeypatch.setattr(inspection.zipfile.ZipFile, "open", guarded_open)
    with pytest.raises(inspection.WheelError, match="offset"):
        inspect(wheel_path, limits)


def test_zip64_count_limit_precedes_zipfile_allocation(wheel_path, limits, monkeypatch):
    data = wheel_path.read_bytes()
    end = data.rfind(b"PK\x05\x06")
    _, _, _, _, count, size, offset, _ = struct.unpack("<4s4H2LH", data[end:end + 22])
    record = struct.pack("<4sQ2H2L4Q", b"PK\x06\x06", 44, 45, 45, 0, 0, count, count, size, offset)
    locator = struct.pack("<4sLQL", b"PK\x06\x07", 0, end, 1)
    footer = struct.pack("<4s4H2LH", b"PK\x05\x06", 0, 0, 0xffff, 0xffff, 0xffffffff, 0xffffffff, 0)
    wheel_path.write_bytes(data[:end] + record + locator + footer)
    monkeypatch.setattr(inspection.zipfile, "ZipFile", lambda *_: pytest.fail("unbounded ZIP indexing"))
    with pytest.raises(inspection.WheelError, match="member count limit"):
        inspect(wheel_path, replace(limits, max_members=2))


@pytest.mark.parametrize("value", [0, -1, True, float("inf"), 1.5])
def test_limits_must_be_explicit_positive_integers(limits, value):
    with pytest.raises(inspection.WheelError, match="limit"):
        replace(limits, max_members=value)


@pytest.mark.parametrize("deadline", [0, float("inf"), float("nan"), True])
def test_invalid_or_exhausted_deadline_refuses_before_open(tmp_path, limits, deadline):
    with pytest.raises(inspection.WheelError, match="deadline"):
        inspection.inspect_wheel(
            tmp_path / "absent.whl", sha256="sha256:" + "0" * 64,
            limits=limits, deadline=deadline,
        )


def test_archive_deadline_is_checked_after_final_fact_parsing(wheel_path, limits, monkeypatch):
    now = [0]
    monkeypatch.setattr(inspection, "time", SimpleNamespace(monotonic=lambda: now[0]))
    single = inspection._single

    def expiring_header(metadata, name, **kwargs):
        value = single(metadata, name, **kwargs)
        if name == "Requires-Python":
            now[0] = 2
        return value

    monkeypatch.setattr(inspection, "_single", expiring_header)
    with pytest.raises(inspection.WheelError, match="deadline"):
        inspection.inspect_archive(io.BytesIO(wheel_path.read_bytes()), limits=limits, deadline=1)


@pytest.mark.parametrize("name", [
    "../escape", "/absolute", "C:drive", "a\\b", "a//b", "a/./b",
    "a/../b", "other-1.0.dist-info/METADATA", "a\0tail",
    "shadow-9.0.DIST-INFO/METADATA", "nested/shadow-9.0.Dist-Info/METADATA",
    "demo_pkg/__init__.py.", "demo_pkg/__init__.py ",
    "demo_pkg./module.py", "demo_pkg /module.py", "trailing//",
])
def test_unsafe_and_ambiguous_members_refuse(wheel_path, limits, name):
    data = wheel_bytes(extra={name: b"bad"})
    if "\0" in name:
        # zipfile's writer truncates NUL names, so mutate the stored raw name.
        data = wheel_bytes(extra={"aXtail": b"bad"}).replace(b"aXtail", b"a\0tail")
    wheel_path.write_bytes(data)
    with pytest.raises(inspection.WheelError):
        inspect(wheel_path, limits)


@pytest.mark.parametrize("name", ["NUL.py", "folder/COM1.txt", "folder/conout$", "a?b", "control\x01"])
def test_windows_device_and_invalid_components_follow_the_receiving_host(wheel_path, limits, name):
    wheel_path.write_bytes(wheel_bytes(extra={name: b"not extracted"}))
    if os.name == "nt":
        with pytest.raises(inspection.WheelError, match="unsafe"):
            inspect(wheel_path, limits)
    else:
        assert inspect(wheel_path, limits).name == "demo-pkg"


def test_duplicate_members_refuse(wheel_path, limits):
    with pytest.warns(UserWarning):
        with zipfile.ZipFile(wheel_path, "a") as archive:
            archive.writestr(PREFIX + "METADATA", b"duplicate")
    with pytest.raises(inspection.WheelError):
        inspect(wheel_path, limits)


def test_link_member_refuses(wheel_path, limits):
    with zipfile.ZipFile(wheel_path, "a") as archive:
        link = zipfile.ZipInfo("link")
        link.create_system = 3
        link.external_attr = 0o120777 << 16
        archive.writestr(link, b"target")
    with pytest.raises(inspection.WheelError):
        inspect(wheel_path, limits)


@pytest.mark.parametrize("extra", [
    {"DEMO_PKG/__init__.py": b"collision"},
    {"demo_pkg": b"not a directory"},
])
def test_ambiguous_install_paths_refuse(wheel_path, limits, extra):
    wheel_path.write_bytes(wheel_bytes(extra=extra))
    with pytest.raises(inspection.WheelError, match="ambiguous"):
        inspect(wheel_path, limits)


def test_encrypted_member_refuses(wheel_path, limits):
    data = bytearray(wheel_path.read_bytes())
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        start = data.index(signature)
        data[start + offset] |= 1
    wheel_path.write_bytes(data)
    with pytest.raises(inspection.WheelError):
        inspect(wheel_path, limits)


@pytest.mark.parametrize("compression", [12, 14, 99])
def test_unbounded_or_unsupported_compression_refuses_before_decoding(wheel_path, limits, compression):
    data = bytearray(wheel_path.read_bytes())
    for signature, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        start = data.index(signature)
        data[start + offset:start + offset + 2] = compression.to_bytes(2, "little")
    wheel_path.write_bytes(data)
    with pytest.raises(inspection.WheelError, match="unsafe"):
        inspect(wheel_path, limits)


def forge_member_size(data, name, prefix, *, compressed_size=None):
    result = bytearray(data)
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        local_offset = archive.getinfo(name).header_offset
    crc = zlib.crc32(prefix)
    struct.pack_into("<L", result, local_offset + 14, crc)
    struct.pack_into("<L", result, local_offset + 22, len(prefix))
    if compressed_size is not None:
        struct.pack_into("<L", result, local_offset + 18, compressed_size)
    end = result.rfind(b"PK\x05\x06")
    position = struct.unpack_from("<L", result, end + 16)[0]
    while result[position:position + 4] == b"PK\x01\x02":
        lengths = struct.unpack_from("<3H", result, position + 28)
        if result[position + 46:position + 46 + lengths[0]] == name.encode():
            struct.pack_into("<L", result, position + 16, crc)
            struct.pack_into("<L", result, position + 24, len(prefix))
            if compressed_size is not None:
                struct.pack_into("<L", result, position + 20, compressed_size)
            return bytes(result)
        position += 46 + sum(lengths)
    raise AssertionError("fixture member not found")


@pytest.mark.parametrize("compression", [zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED])
@pytest.mark.parametrize("name,prefix", [("payload.bin", b"prefix"), ("empty/", b"")])
def test_complete_stream_must_match_declared_prefix(wheel_path, limits, name, prefix, compression):
    def record_prefix(rows):
        if name.endswith("/"):
            rows[:] = [row for row in rows if row[0] != name]
        else:
            row = next(row for row in rows if row[0] == name)
            row[1] = "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(prefix).digest()).rstrip(b"=").decode()
            row[2] = str(len(prefix))

    data = wheel_bytes(
        extra={name: prefix + b"hidden" * 5000}, record_mutator=record_prefix,
        compression=compression,
    )
    wheel_path.write_bytes(forge_member_size(data, name, prefix))
    with zipfile.ZipFile(wheel_path) as archive:
        assert archive.read(name) == prefix
    with pytest.raises(inspection.WheelError):
        inspect(wheel_path, replace(limits, max_archive_bytes=200_000))


def test_corrupt_deflate_is_a_structured_refusal(wheel_path, limits):
    data = bytearray(wheel_path.read_bytes())
    with zipfile.ZipFile(wheel_path) as archive:
        offset = archive.getinfo("demo_pkg/__init__.py").header_offset
    lengths = struct.unpack_from("<2H", data, offset + 26)
    start = offset + 30 + sum(lengths)
    data[start] |= 0x06
    wheel_path.write_bytes(data)
    with pytest.raises(inspection.WheelError) as refusal:
        inspect(wheel_path, limits)
    assert isinstance(refusal.value.__cause__, zlib.error)
    with pytest.raises(runtime_profile.ProfileError):
        _verify_selected(
            wheel_path, selected_record(wheel_path), limits=limits, deadline=time.monotonic() + 10,
        )


def test_missing_deflate_end_marker_refuses_even_when_prefix_crc_matches(wheel_path, limits):
    name = "demo_pkg/__init__.py"
    data = wheel_path.read_bytes()
    with zipfile.ZipFile(wheel_path) as archive:
        original = archive.read(name)
        truncated = archive.getinfo(name).compress_size - 1
    wheel_path.write_bytes(forge_member_size(data, name, original, compressed_size=truncated))
    with zipfile.ZipFile(wheel_path) as archive:
        assert archive.read(name) == original
    with pytest.raises(inspection.WheelError, match="incomplete"):
        inspect(wheel_path, limits)


def test_hardlinked_source_refuses(wheel_path, limits, tmp_path):
    os.link(wheel_path, tmp_path / "alias")
    with pytest.raises(inspection.WheelError, match="ordinary"):
        inspect(wheel_path, limits)


def test_path_replacement_during_inspection_refuses(wheel_path, limits, monkeypatch):
    original = inspection.inspect_archive

    def replace_path(*args, **kwargs):
        facts = original(*args, **kwargs)
        replacement = wheel_path.with_suffix(".replacement")
        replacement.write_bytes(wheel_path.read_bytes())
        replacement.replace(wheel_path)
        return facts

    monkeypatch.setattr(inspection, "inspect_archive", replace_path)
    # Windows may deny replacement while the source handle is held.
    with pytest.raises(inspection.WheelError):
        inspect(wheel_path, limits)


def test_compressed_metadata_budget_refuses(wheel_path, limits):
    wheel_path.write_bytes(wheel_bytes(metadata=b"Description: " + b"x" * 15_000))
    assert wheel_path.stat().st_size < limits.max_metadata_bytes
    with pytest.raises(inspection.WheelError, match="metadata limit"):
        inspect(wheel_path, limits)


def test_symlinked_source_refuses(wheel_path, limits, tmp_path, native_file_symlink):
    alias = tmp_path / "alias.whl"
    native_file_symlink(alias, wheel_path)
    with pytest.raises(inspection.WheelError, match="linked"):
        inspect(alias, limits)


def test_bootstrap_interpreter_needs_no_site_packages(wheel_path):
    program = """
import hashlib, pathlib, sys, time
sys.path.insert(0, sys.argv[1])
from installer_wheel_inputs import WheelLimits, inspect_wheel
path = pathlib.Path(sys.argv[2])
facts = inspect_wheel(path, sha256='sha256:' + hashlib.sha256(path.read_bytes()).hexdigest(),
    limits=WheelLimits(100000, 100000, 20000, 10000, 30, 100, 10000), deadline=time.monotonic() + 5)
assert facts.name == 'demo-pkg' and facts.tags == ('py3-none-any',)
assert 'packaging' not in sys.modules
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", program,
         str(Path(inspection.__file__).parent), str(wheel_path)],
        capture_output=True, text=True, timeout=15,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.parametrize("metadata", [
    b"Metadata-Version: 2.3\nName: demo\nName: demo\nVersion: 1.0\n\n",
    b"Metadata-Version: 2.3\nName: demo\nVersion: 1.0\nRequires-Python: >=3\nRequires-Python: >=3\n\n",
    b"Metadata-Version: 2.3\nName: \xff\nVersion: 1.0\n\n",
    b"Metadata-Version: 2.3\nName: demo\nmalformed\nVersion: 1.0\n\n",
])
def test_malformed_metadata_refuses(wheel_path, limits, metadata):
    wheel_path.write_bytes(wheel_bytes(metadata=metadata))
    with pytest.raises(inspection.WheelError):
        inspect(wheel_path, limits)


@pytest.mark.parametrize("mutator", [
    lambda rows: rows.pop(0),
    lambda rows: rows.append(rows[0]),
    lambda rows: rows[0].__setitem__(1, "sha256=wrong"),
    lambda rows: rows[0].__setitem__(2, "999"),
    lambda rows: rows[-1].__setitem__(1, "sha256=self"),
])
def test_record_inconsistency_refuses(wheel_path, limits, mutator):
    wheel_path.write_bytes(wheel_bytes(record_mutator=mutator))
    with pytest.raises(inspection.WheelError, match="RECORD"):
        inspect(wheel_path, limits)


def test_source_mutation_during_inspection_refuses(wheel_path, limits, monkeypatch):
    original = inspection.inspect_archive

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        wheel_path.write_bytes(wheel_path.read_bytes() + b"changed")
        return result

    monkeypatch.setattr(inspection, "inspect_archive", mutate)
    with pytest.raises(inspection.WheelError, match="changed"):
        inspect(wheel_path, limits)


def test_held_handle_change_time_is_checked_separately(wheel_path, limits, monkeypatch):
    original = inspection.os.fstat
    calls = 0

    def changed_time(descriptor):
        nonlocal calls
        actual = original(descriptor)
        calls += 1
        return SimpleNamespace(
            st_dev=actual.st_dev, st_ino=actual.st_ino, st_size=actual.st_size,
            st_mtime_ns=actual.st_mtime_ns,
            st_birthtime_ns=getattr(actual, "st_birthtime_ns", actual.st_ctime_ns),
            st_ctime_ns=actual.st_ctime_ns + (1 if calls > 1 else 0),
        )

    monkeypatch.setattr(inspection.os, "fstat", changed_time)
    with pytest.raises(inspection.WheelError, match="changed"):
        inspect(wheel_path, limits)


def test_deadline_checked_during_archive_reads(wheel_path, limits, monkeypatch):
    ticks = iter([1.0] * 5 + [20.0] * 100)
    monkeypatch.setattr(inspection.time, "monotonic", lambda: next(ticks))
    with pytest.raises(inspection.WheelError, match="deadline"):
        inspection.inspect_wheel(
            wheel_path, sha256="sha256:" + hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
            limits=limits, deadline=10.0,
        )


def test_deep_member_walk_observes_deadline_before_metadata(wheel_path, limits, monkeypatch):
    wheel_path.write_bytes(wheel_bytes(extra={"/".join(["nested"] * 500) + "/module.py": b"x"}))
    ticks = itertools.count(1)
    monkeypatch.setattr(inspection.time, "monotonic", lambda: next(ticks))
    monkeypatch.setattr(inspection, "_headers", lambda *_: pytest.fail("walk exceeded its deadline"))
    with pytest.raises(inspection.WheelError, match="deadline"):
        inspection.inspect_wheel(
            wheel_path, sha256="sha256:" + hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
            limits=limits, deadline=50,
        )


def selected_record(path):
    return {
        "filename": path.name, "name": "demo.pkg", "version": "1.0.0",
        "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        "requires_python": ">=3.10", "provides_extras": ["fast"],
        "selected_extras": ["fast"], "requires_dist": ["helper>=2; extra == 'fast'"],
    }


def test_selected_record_matches_semantic_names_and_versions(wheel_path, limits):
    record = selected_record(wheel_path)
    _verify_selected(
        wheel_path, record, limits=limits, deadline=time.monotonic() + 10,
    )


def test_selected_record_retains_exact_dependency_spelling_contract(wheel_path, limits):
    record = selected_record(wheel_path)
    record["requires_dist"] = ['Helper >= 2; extra == "fast"']
    with pytest.raises(runtime_profile.ProfileError, match="dependency metadata differs"):
        _verify_selected(wheel_path, record, limits=limits, deadline=time.monotonic() + 10)


@pytest.mark.parametrize("field,value", [
    ("name", "other"), ("version", "2.0"), ("requires_python", ">=3.11"),
    ("provides_extras", []), ("requires_dist", []),
    ("sha256", "sha256:" + "0" * 64), ("filename", "demo_pkg-2-py3-none-any.whl"),
])
def test_each_selected_record_binding_refuses(wheel_path, limits, field, value):
    record = {**selected_record(wheel_path), field: value}
    with pytest.raises(runtime_profile.ProfileError):
        _verify_selected(
            wheel_path, record, limits=limits, deadline=time.monotonic() + 10,
        )


@pytest.mark.parametrize("data", [
    wheel_bytes(metadata=b"Metadata-Version: 2.3\nName: other\nVersion: 1.0\n\n"),
    wheel_bytes(metadata=b"Metadata-Version: 2.3\nName: demo-pkg\nVersion: 2.0\n\n"),
    wheel_bytes(wheel=b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py2-none-any\n\n"),
    wheel_bytes(metadata=(
        b"Metadata-Version: 2.3\nName: demo-pkg\nVersion: 1.0\nRequires-Python: >=3.11\n\n"
    )),
])
def test_actual_metadata_cannot_be_hidden_by_rehashed_archive(wheel_path, limits, data):
    record = selected_record(wheel_path)
    wheel_path.write_bytes(data)
    record["sha256"] = "sha256:" + hashlib.sha256(data).hexdigest()
    with pytest.raises(runtime_profile.ProfileError):
        _verify_selected(
            wheel_path, record, limits=limits, deadline=time.monotonic() + 10,
        )


@pytest.mark.parametrize("before,after", [
    (b"Requires-Dist: helper>=2", b"Requires-Dist: helper>=3"),
    (b"Provides-Extra: fast", b"Provides-Extra: slow"),
    (b"Provides-Extra: fast", b"Provides-Extra: fast\nProvides-Extra: FAST"),
])
def test_actual_dependency_and_extra_changes_refuse(wheel_path, limits, before, after):
    record = selected_record(wheel_path)
    with zipfile.ZipFile(wheel_path) as archive:
        metadata = archive.read(PREFIX + "METADATA").replace(before, after)
    wheel_path.write_bytes(wheel_bytes(metadata=metadata))
    record["sha256"] = "sha256:" + hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    with pytest.raises(runtime_profile.ProfileError):
        _verify_selected(
            wheel_path, record, limits=limits, deadline=time.monotonic() + 10,
        )


@pytest.mark.parametrize("alternatives", [2, 200])
def test_compressed_tags_refuse_before_cartesian_allocation(wheel_path, limits, monkeypatch, alternatives):
    field = ".".join(f"tag{index}" for index in range(alternatives))
    tag = "-".join([field] * 3)
    wheel_path.write_bytes(wheel_bytes(wheel=(
        "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: " + tag + "\n\n"
    ).encode()))
    parse_tag = runtime_profile.parse_tag
    monkeypatch.setattr(runtime_profile, "parse_tag", lambda text: (
        pytest.fail("unbounded Tag allocation") if text == tag else parse_tag(text)
    ))
    with pytest.raises(runtime_profile.ProfileError):
        _verify_selected(
            wheel_path, selected_record(wheel_path), limits=limits, deadline=time.monotonic() + 10,
        )


def test_selected_filename_tag_expansion_is_also_bounded(wheel_path, limits, monkeypatch):
    field = ".".join(f"tag{index}" for index in range(10))
    path = wheel_path.with_name("demo_pkg-1.0-" + "-".join([field] * 3) + ".whl")
    wheel_path.rename(path)
    monkeypatch.setattr(
        runtime_profile, "parse_wheel_filename", lambda *_: pytest.fail("unbounded filename allocation"),
    )
    with pytest.raises(runtime_profile.ProfileError, match="limit"):
        _verify_selected(
            path, selected_record(path), limits=limits, deadline=time.monotonic() + 10,
        )


def test_repeated_compressed_alternatives_retain_exact_wheel_tag_contract(wheel_path, limits):
    tag = ".".join(["py3"] * 200) + "-none-any"
    wheel_path.write_bytes(wheel_bytes(wheel=(
        "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: " + tag + "\n\n"
    ).encode()))
    with pytest.raises(runtime_profile.ProfileError):
        _verify_selected(
            wheel_path, selected_record(wheel_path), limits=limits, deadline=time.monotonic() + 10,
        )


def test_email_encoded_words_cannot_silently_change_dependency_metadata(wheel_path, limits):
    record = selected_record(wheel_path)
    with zipfile.ZipFile(wheel_path) as archive:
        metadata = archive.read(PREFIX + "METADATA")
    original = b"helper>=2; extra == 'fast'"
    encoded = b"=?utf-8?b?" + base64.b64encode(original) + b"?="
    metadata = metadata.replace(b"Requires-Dist: " + original, b"Requires-Dist: " + encoded)
    wheel_path.write_bytes(wheel_bytes(metadata=metadata))
    record["sha256"] = "sha256:" + hashlib.sha256(wheel_path.read_bytes()).hexdigest()
    assert inspect(wheel_path, limits).requires_dist == (encoded.decode("ascii"),)
    with pytest.raises(runtime_profile.ProfileError):
        _verify_selected(
            wheel_path, record, limits=limits, deadline=time.monotonic() + 10,
        )
