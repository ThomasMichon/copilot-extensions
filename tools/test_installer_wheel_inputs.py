"""Real archive controls for the dependency-free wheel inspection boundary."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import subprocess
import sys
import time
import zipfile

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
import installer_wheel_inputs as inspection
import runtime_profile
from wheel_input_fixture import PREFIX, wheel_bytes


@pytest.fixture
def limits():
    return inspection.WheelLimits(
        max_archive_bytes=100_000, max_expanded_bytes=100_000,
        max_member_bytes=20_000, max_metadata_bytes=10_000, max_members=30,
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


@pytest.mark.parametrize("field,value", [
    ("max_archive_bytes", 10), ("max_expanded_bytes", 10),
    ("max_member_bytes", 10), ("max_metadata_bytes", 10), ("max_members", 2),
])
def test_all_limits_are_enforced(wheel_path, limits, field, value):
    with pytest.raises(inspection.WheelError, match="limit"):
        inspect(wheel_path, replace(limits, **{field: value}))


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


@pytest.mark.parametrize("name", [
    "../escape", "/absolute", "C:drive", "a\\b", "a//b", "a/./b",
    "a/../b", "other-1.0.dist-info/METADATA", "a\0tail",
])
def test_unsafe_and_ambiguous_members_refuse(wheel_path, limits, name):
    data = wheel_bytes(extra={name: b"bad"})
    if "\0" in name:
        # zipfile's writer truncates NUL names, so mutate the stored raw name.
        data = wheel_bytes(extra={"aXtail": b"bad"}).replace(b"aXtail", b"a\0tail")
    wheel_path.write_bytes(data)
    with pytest.raises(inspection.WheelError):
        inspect(wheel_path, limits)


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
    limits=WheelLimits(100000, 100000, 20000, 10000, 30), deadline=time.monotonic() + 5)
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


def test_deadline_checked_during_archive_reads(wheel_path, limits, monkeypatch):
    ticks = iter([1.0] * 5 + [20.0] * 100)
    monkeypatch.setattr(inspection.time, "monotonic", lambda: next(ticks))
    with pytest.raises(inspection.WheelError, match="deadline"):
        inspection.inspect_wheel(
            wheel_path, sha256="sha256:" + hashlib.sha256(wheel_path.read_bytes()).hexdigest(),
            limits=limits, deadline=10.0,
        )


def selected_record(path):
    return {
        "filename": path.name, "name": "demo.pkg", "version": "1.0.0",
        "sha256": "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest(),
        "requires_python": ">=3.10", "provides_extras": ["fast"],
        "selected_extras": ["fast"], "requires_dist": ["helper>=2; extra == 'fast'"],
    }


def test_selected_record_matches_semantic_names_versions_and_requirements(wheel_path, limits):
    record = selected_record(wheel_path)
    record["requires_dist"] = ['Helper >= 2; extra == "fast"']
    facts = runtime_profile.inspect_profile_wheel(
        wheel_path, record, limits=limits, deadline=time.monotonic() + 10,
    )
    assert facts.name == "demo-pkg"


@pytest.mark.parametrize("field,value", [
    ("name", "other"), ("version", "2.0"), ("requires_python", ">=3.11"),
    ("provides_extras", []), ("requires_dist", []),
    ("sha256", "sha256:" + "0" * 64), ("filename", "demo_pkg-2-py3-none-any.whl"),
])
def test_each_selected_record_binding_refuses(wheel_path, limits, field, value):
    record = {**selected_record(wheel_path), field: value}
    with pytest.raises(runtime_profile.ProfileError):
        runtime_profile.inspect_profile_wheel(
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
        runtime_profile.inspect_profile_wheel(
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
        runtime_profile.inspect_profile_wheel(
            wheel_path, record, limits=limits, deadline=time.monotonic() + 10,
        )
