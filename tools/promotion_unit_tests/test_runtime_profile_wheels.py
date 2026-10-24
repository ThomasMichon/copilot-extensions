"""Pure actual-byte profile checks; no package code, feed or installer actor."""
from __future__ import annotations

import base64
import csv
from dataclasses import replace
import hashlib
import io
import sys
import time
from types import SimpleNamespace
import zipfile

import pytest

from runtime_profile import ProfileError, SCHEMA, validate_profile
from runtime_profile_wheels import verify_profile_wheels
from installer_wheel_inputs import WheelLimits, inspect_archive


_ENVIRONMENT = {
    "implementation_name": "cpython", "implementation_version": "3.11.9",
    "os_name": "nt", "platform_machine": "AMD64", "platform_release": "11",
    "platform_system": "Windows", "platform_version": "10.0.1",
    "python_full_version": "3.11.9", "platform_python_implementation": "CPython",
    "python_version": "3.11", "sys_platform": "win32",
}


@pytest.fixture(autouse=True)
def _synthetic_host_defaults(monkeypatch):
    monkeypatch.setattr("packaging.markers.default_environment", lambda: dict(_ENVIRONMENT))


def _record():
    def wheel(name, **fields):
        return {
            "name": name, "version": "1.0", "filename": f"{name}-1.0-py3-none-any.whl",
            "sha256": "sha256:" + "1" * 64, "requires_python": "",
            "provides_extras": [], "selected_extras": [], "requires_dist": [], **fields,
        }

    return {
        "schema": SCHEMA, "version": 1,
        "release": {"identity": "fixture-release", "sha256": "sha256:" + "2" * 64},
        "mode": "source", "artifact": None, "component": "index-engine", "backend": "cpu",
        "approval_source": "fixture-authority", "acquisition_policy": "fixture-policy",
        "target": {"device": "cpu", "environment": dict(_ENVIRONMENT), "tags": ["py3-none-any"]},
        "roots": ["engine[metrics]==1.0"],
        "wheels": [
            wheel("engine", requires_python=">=3.11", provides_extras=["metrics"],
                  selected_extras=["metrics"], requires_dist=[
                      'counter>=1; extra == "metrics"',
                      'counter>=1; sys_platform == "linux"',
                  ]),
            wheel("counter"),
        ],
    }


def _metadata(wheel):
    return (
        f"Metadata-Version: 2.4\nName: {wheel['name']}\nVersion: {wheel['version']}\n"
        + (f"Requires-Python: {wheel['requires_python']}\n" if wheel["requires_python"] else "")
        + "".join(f"Provides-Extra: {extra}\n" for extra in wheel["provides_extras"])
        + "".join(f"Requires-Dist: {edge}\n" for edge in wheel["requires_dist"])
    )


def _encoded(
    wheel, *, metadata=None, wheel_header=None, extra=None, bad_record=False,
    record_algorithm="sha256", member_comment=None,
):
    prefix = wheel["filename"].split("-py3-")[0] + ".dist-info/"
    if metadata is None:
        metadata = _metadata(wheel)
    files = {
        prefix + "METADATA": metadata.encode(),
        prefix + "WHEEL": (
            wheel_header or "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
        ).encode(),
        wheel["name"] + "/__init__.py": b"raise RuntimeError('must never execute')\n",
        **(extra or {}),
    }
    record = io.StringIO(newline="")
    writer = csv.writer(record)
    for name, data in files.items():
        digest = base64.urlsafe_b64encode(
            hashlib.new(record_algorithm, data).digest(),
        ).rstrip(b"=").decode()
        writer.writerow([
            name, record_algorithm + "=" + ("0" * len(digest) if bad_record else digest),
            str(len(data)),
        ])
    writer.writerow([prefix + "RECORD", "", ""])
    files[prefix + "RECORD"] = record.getvalue().encode()
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for name, data in files.items():
            if member_comment is None:
                bundle.writestr(name, data)
            else:
                member = zipfile.ZipInfo(name)
                member.comment = member_comment
                bundle.writestr(member, data, compress_type=zipfile.ZIP_DEFLATED)
    return archive.getvalue()


def _inputs(*, mutate=None):
    record = _record()
    wheels = {}
    for index, wheel in enumerate(record["wheels"]):
        options = (mutate or {}) if index == 0 else {}
        data = _encoded(wheel, **options)
        wheel["sha256"] = "sha256:" + hashlib.sha256(data).hexdigest()
        wheels[wheel["filename"]] = io.BytesIO(data)
    return validate_profile(record), wheels


def _verify(profile, wheels, **limits):
    verify_profile_wheels(profile, wheels, **{
        "max_archive_bytes": 1 << 20, "max_unpacked_bytes": 1 << 20,
        "max_metadata_bytes": 1 << 16, **limits,
    })


def test_shared_token_parser_retains_builder_numeric_version_policy():
    from pathlib import Path
    from artifact_source_build import PromotionArtifactError, parse_wheel_identity

    assert parse_wheel_identity(Path("engine-v1.0-py3-none-any.whl"))["version"] == "v1.0"
    with pytest.raises(PromotionArtifactError):
        parse_wheel_identity(Path("engine-v1.0-py3-none-any.whl"), numeric_version=True)

def test_byte_verification_does_not_import_builder_or_feed_machinery(monkeypatch):
    for name in ("build_python_artifacts", "build_toolchain_lock", "governed_feed_trust"):
        monkeypatch.setitem(sys.modules, name, None)
    profile, wheels = _inputs()
    _verify(profile, wheels)

@pytest.mark.parametrize("name", ["example.pkg", "example-pkg", "example_pkg", "Example.Pkg"])
def test_runtime_distribution_name_aliases_match_dotted_filename(name):
    record = _record()
    record["roots"] = ["example-pkg[metrics]==1.0"]
    wheel = record["wheels"][0]
    wheel.update(name="example-pkg", filename="example.pkg-1.0-py3-none-any.whl")
    streams = {}
    for index, item in enumerate(record["wheels"]):
        metadata = _metadata(item).replace("Name: example-pkg\n", f"Name: {name}\n")
        data = _encoded(item, metadata=metadata if index == 0 else None)
        item["sha256"] = "sha256:" + hashlib.sha256(data).hexdigest()
        streams[item["filename"]] = io.BytesIO(data)
    _verify(validate_profile(record), streams)

@pytest.mark.parametrize("algorithm", ["sha256", "sha384", "sha512"])
def test_runtime_record_strong_digests_preserve_archive_sha256(algorithm):
    profile, wheels = _inputs(mutate={"record_algorithm": algorithm})
    _verify(profile, wheels)
    profile, wheels = _inputs(mutate={"record_algorithm": algorithm, "bad_record": True})
    with pytest.raises(ProfileError):
        _verify(profile, wheels)

@pytest.mark.parametrize("algorithm", ["sha256", "sha384", "sha512"])
def test_record_source_facts(algorithm):
    profile, wheels = _inputs(mutate={"record_algorithm": algorithm})
    stream = wheels[profile.record()["wheels"][0]["filename"]]
    limits = WheelLimits(1 << 20, 1 << 20, 1 << 20, 1 << 16, 100, 100, 1 << 16)
    facts = inspect_archive(
        stream, limits=limits, deadline=time.monotonic() + 5,
        record_algorithms=frozenset({algorithm}),
    )
    with zipfile.ZipFile(stream) as archive:
        assert facts.unpacked_size == sum(member.file_size for member in archive.infolist())
        assert facts.member_hashes["engine/__init__.py"] == hashlib.sha256(
            archive.read("engine/__init__.py"),
        ).hexdigest()
    if algorithm != "sha256":
        from installer_wheel_inputs import WheelError
        with pytest.raises(WheelError, match="unsupported wheel RECORD digest"):
            inspect_archive(stream, limits=limits, deadline=time.monotonic() + 5)

@pytest.mark.parametrize("field,value", [
    ("max_archive_bytes", 1), ("max_expanded_bytes", 1), ("max_member_bytes", 1),
    ("max_metadata_bytes", 1), ("max_metadata_member_bytes", 1), ("max_members", 3),
])
def test_inspector_limits(field, value):
    profile, wheels = _inputs()
    limits = WheelLimits(1 << 20, 1 << 20, 1 << 20, 1 << 16, 100, 100, 1 << 16)
    with pytest.raises(ProfileError):
        _verify(profile, wheels, limits=replace(limits, **{field: value}))

@pytest.mark.parametrize("deadline", [0, -1, True, float("nan"), float("inf")])
def test_invalid_deadline(deadline):
    profile, wheels = _inputs()
    with pytest.raises(ProfileError):
        _verify(profile, wheels, deadline=deadline)

def test_complete_profile_checks_deadline_during_archive_hashing(monkeypatch):
    import installer_wheel_inputs

    profile, wheels = _inputs()
    now = [0]
    monkeypatch.setattr(installer_wheel_inputs, "time", SimpleNamespace(monotonic=lambda: now[0]))

    class ExpiringStream(io.BytesIO):
        def read(self, size=-1):
            now[0] = 2
            return super().read(size)

    filename = next(iter(wheels))
    wheels[filename] = ExpiringStream(wheels[filename].getvalue())
    with pytest.raises(ProfileError):
        _verify(profile, wheels, deadline=1)

def test_complete_profile_does_not_inherit_the_first_party_archive_cap(monkeypatch):
    import promotion_python_artifacts as promotion

    profile, wheels = _inputs()
    monkeypatch.setattr(promotion, "_BUNDLE_LIMIT", 1)
    _verify(profile, wheels)
    wheel = profile.record()["wheels"][0]
    with pytest.raises(promotion.PromotionArtifactError, match="archive limit"):
        promotion.validate_wheel(
            wheels[wheel["filename"]], wheel | {"generator": "locked"},
        )

def test_per_wheel_archive_limit_bounds_the_initial_digest_pass():
    profile, wheels = _inputs()

    class MeasuredStream(io.BytesIO):
        read_bytes = 0

        def read(self, size=-1):
            data = super().read(size)
            self.read_bytes += len(data)
            return data

    filename = next(iter(wheels))
    stream = MeasuredStream(wheels[filename].getvalue())
    wheels[filename] = stream
    limits = WheelLimits(16, 1 << 20, 1 << 20, 1 << 16, 100, 100, 1 << 16)
    with pytest.raises(ProfileError):
        _verify(profile, wheels, limits=limits)
    assert stream.read_bytes == limits.max_archive_bytes + 1


@pytest.mark.parametrize("explicit_limits", [False, True])
def test_directory_budget(explicit_limits):
    profile, wheels = _inputs(mutate={"member_comment": b"x" * 4_000})
    limits = WheelLimits(1 << 20, 1 << 20, 1 << 20, 1 << 16, 100, 100, 1 << 16)
    options = {"limits": limits} if explicit_limits else {}
    _verify(profile, wheels, max_metadata_bytes=500, **options)
    with pytest.raises(ProfileError):
        _verify(profile, wheels, max_metadata_bytes=1, **options)
    if explicit_limits:
        with pytest.raises(ProfileError):
            _verify(profile, wheels, max_metadata_bytes=500,
                    limits=replace(limits, max_metadata_bytes=1_000))


def test_final_hash_deadline(monkeypatch):
    import installer_wheel_inputs
    import runtime_profile_wheels

    profile, wheels = _inputs()
    now = [0]
    final_rehash = [False]
    monkeypatch.setattr(installer_wheel_inputs, "time", SimpleNamespace(monotonic=lambda: now[0]))

    class ExpiringEOF(io.BytesIO):
        def read(self, size=-1):
            data = super().read(size)
            if final_rehash[0] and not data:
                now[0] = 2
            return data

    filename = list(wheels)[-1]
    stream = ExpiringEOF(wheels[filename].getvalue())
    wheels[filename] = stream
    original = runtime_profile_wheels.inspect_wheel

    def inspection(*args, **kwargs):
        result = original(*args, **kwargs)
        if args[0] is stream:
            final_rehash[0] = True
        return result

    monkeypatch.setattr(runtime_profile_wheels, "inspect_wheel", inspection)
    with pytest.raises(ProfileError):
        _verify(profile, wheels, deadline=1)
    assert final_rehash[0] and now[0] == 2

@pytest.mark.parametrize("parser", ["parse_tag", "parse_wheel_filename", "Requirement"])
def test_validation_deadline(monkeypatch, parser):
    import installer_wheel_inputs
    import runtime_profile

    profile, wheels = _inputs()
    now = [0]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(installer_wheel_inputs, "time", clock)
    monkeypatch.setattr(runtime_profile, "time", clock)
    original = getattr(runtime_profile, parser)
    calls = []

    def expiring_parser(*args, **kwargs):
        if calls:
            pytest.fail("packaging parsing continued after profile deadline")
        calls.append(args)
        result = original(*args, **kwargs)
        now[0] = 2
        return result

    monkeypatch.setattr(runtime_profile, parser, expiring_parser)
    with pytest.raises(ProfileError):
        _verify(profile, wheels, deadline=1)
    assert len(calls) == 1

def test_expiry_during_dependency_walk_stops_before_the_next_marker(monkeypatch):
    import installer_wheel_inputs
    import runtime_profile
    from packaging.markers import Marker

    profile, wheels = _inputs()
    now = [0]
    clock = SimpleNamespace(monotonic=lambda: now[0])
    monkeypatch.setattr(installer_wheel_inputs, "time", clock)
    monkeypatch.setattr(runtime_profile, "time", clock)
    original = Marker.evaluate
    calls = []

    def expiring_marker(self, *args, **kwargs):
        if calls:
            pytest.fail("dependency walk continued after profile deadline")
        calls.append(self)
        result = original(self, *args, **kwargs)
        now[0] = 2
        return result

    monkeypatch.setattr(Marker, "evaluate", expiring_marker)
    with pytest.raises(ProfileError):
        _verify(profile, wheels, deadline=1)
    assert len(calls) == 1

@pytest.mark.parametrize("algorithm", ["md5", "sha1", "sha224"])
def test_runtime_record_weak_digests_refuse(algorithm):
    profile, wheels = _inputs(mutate={"record_algorithm": algorithm})
    with pytest.raises(ProfileError):
        _verify(profile, wheels)

@pytest.mark.parametrize("algorithm", ["sha384", "sha512"])
def test_first_party_record_policy_remains_sha256(algorithm):
    from promotion_python_artifacts import PromotionArtifactError, validate_wheel

    header = "Wheel-Version: 1.0\nGenerator: locked\nRoot-Is-Purelib: true\nTag: py3-none-any\n"
    profile, wheels = _inputs(mutate={
        "record_algorithm": algorithm, "wheel_header": header,
    })
    wheel = profile.record()["wheels"][0]
    with pytest.raises(PromotionArtifactError, match="unsupported wheel RECORD digest"):
        validate_wheel(wheels[wheel["filename"]], wheel | {"generator": "locked"})

def test_runtime_version_identity_uses_pep440_without_changing_first_party_policy():
    record = _record()
    streams = {}
    for index, wheel in enumerate(record["wheels"]):
        if index == 0:
            wheel["filename"] = "engine-v1.0-py3-none-any.whl"
        data = _encoded(wheel)
        wheel["sha256"] = "sha256:" + hashlib.sha256(data).hexdigest()
        streams[wheel["filename"]] = io.BytesIO(data)
    _verify(validate_profile(record), streams)

def test_complete_actual_wheel_closure_never_executes_payload():
    profile, wheels = _inputs()
    _verify(profile, wheels)
    assert profile.record()["wheels"][0]["selected_extras"] == ["metrics"]
    assert all(not stream.closed for stream in wheels.values())

def test_retained_file_handles_use_the_same_complete_byte_contract(tmp_path):
    from contextlib import ExitStack

    profile, wheels = _inputs()
    with ExitStack() as retained:
        files = {}
        for name, stream in wheels.items():
            path = tmp_path / name
            path.write_bytes(stream.getvalue())
            files[name] = retained.enter_context(path.open("rb"))
        _verify(profile, files)
        assert all(not stream.closed for stream in files.values())
    assert all(stream.closed for stream in files.values())

def test_no_optional_python_header_has_no_invented_constraint():
    record = _record()
    wheels = {}
    for wheel in record["wheels"]:
        wheel["requires_python"] = ""
        data = _encoded(wheel)
        wheel["sha256"] = "sha256:" + hashlib.sha256(data).hexdigest()
        wheels[wheel["filename"]] = io.BytesIO(data)
    _verify(validate_profile(record), wheels)

@pytest.mark.parametrize("metadata", [
    "Metadata-Version: 2.4\nName: other\nVersion: 1.0\n",
    "Metadata-Version: 2.4\nName: engine\nVersion: 2.0\n",
    "Metadata-Version: 2.4\nName: engine\nName: engine\nVersion: 1.0\n",
    "Metadata-Version: 2.4\nName: engine\nVersion: 1.0\n",
    "Metadata-Version: 2.4\nName: engine\nVersion: 1.0\nRequires-Python: >=3.10\n",
    "Metadata-Version: 2.4\nName: engine\nVersion: 1.0\nRequires-Python: >=3.11\n"
    "Requires-Python: >=3.11\n",
    "Metadata-Version: 2.4\nName: engine\nVersion: 1.0\nRequires-Python: >=3.11\n"
    "Provides-Extra: metrics\nRequires-Dist: counter>=2\n",
    "Metadata-Version: 2.4\nName: engine\nVersion: 1.0\nRequires-Python: >=3.11\n"
    "Provides-Extra: metrics\nRequires-Dist: counter @ https://example.invalid/a.whl\n",
])
def test_actual_metadata_cannot_hide_behind_matching_archive_digest(metadata):
    profile, wheels = _inputs(mutate={"metadata": metadata})
    with pytest.raises(ProfileError):
        _verify(profile, wheels)

@pytest.mark.parametrize("before,after", [
    ("Requires-Python: >=3.11\n", ""),
    ("Requires-Python: >=3.11\n", "Requires-Python: >=3.10\n"),
    ("Provides-Extra: metrics\n", ""),
    ("Provides-Extra: metrics\n", "Provides-Extra: other\n"),
    ("Provides-Extra: metrics\n", "Provides-Extra: metrics\nProvides-Extra: undeclared\n"),
    ('Requires-Dist: counter>=1; extra == "metrics"\n', ""),
    ('Requires-Dist: counter>=1; extra == "metrics"\n', "Requires-Dist: counter>=2\n"),
    ('sys_platform == "linux"', 'sys_platform == "win32"'),
    ("Version: 1.0\n", "Version: 1.0\nRequires-Dist: hidden>=1\n"),
])
def test_each_actual_dependency_field_must_match_the_frozen_record(before, after):
    original = _metadata(_record()["wheels"][0])
    assert before in original
    profile, wheels = _inputs(mutate={"metadata": original.replace(before, after)})
    with pytest.raises(ProfileError, match="dependency metadata differs"):
        _verify(profile, wheels)

@pytest.mark.parametrize("mutate", [
    {"bad_record": True},
    {"extra": {"../escape.py": b"unsafe"}},
    {"extra": {"other-1.0.dist-info/METADATA": b"foreign"}},
    {"wheel_header": "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: cp312-cp312-win_amd64\n"},
])
def test_shared_archive_validation_remains_required(mutate):
    profile, wheels = _inputs(mutate=mutate)
    with pytest.raises(ProfileError):
        _verify(profile, wheels)

@pytest.mark.parametrize("change", ["missing", "extra", "tampered", "not-zip"])
def test_wrong_or_tampered_retained_set_refuses(change):
    profile, wheels = _inputs()
    name = next(iter(wheels))
    if change == "missing":
        wheels.pop(name)
    elif change == "extra":
        wheels["unselected-1.0-py3-none-any.whl"] = io.BytesIO(b"extra")
    elif change == "tampered":
        wheels[name].write(b"bad")
    else:
        data = b"not a zip"
        wheels[name] = io.BytesIO(data)
        record = profile.record()
        record["wheels"][0]["sha256"] = "sha256:" + hashlib.sha256(data).hexdigest()
        profile = validate_profile(record)
    with pytest.raises(ProfileError):
        _verify(profile, wheels)

def test_closure_budgets_are_inclusive_and_shared_not_per_wheel():
    profile, wheels = _inputs()
    compressed = sum(len(stream.getvalue()) for stream in wheels.values())
    unpacked = 0
    largest_metadata = 0
    for stream in wheels.values():
        with zipfile.ZipFile(stream) as archive:
            unpacked += sum(member.file_size for member in archive.infolist())
            largest_metadata = max(largest_metadata, *(
                member.file_size for member in archive.infolist()
                if member.filename.rsplit("/", 1)[-1] in {"METADATA", "WHEEL", "RECORD"}
            ))
    limits = dict(
        max_archive_bytes=compressed, max_unpacked_bytes=unpacked,
        max_metadata_bytes=largest_metadata,
    )
    _verify(profile, wheels, **limits)
    for limit in limits:
        with pytest.raises(ProfileError):
            _verify(profile, wheels, **(limits | {limit: limits[limit] - 1}))

@pytest.mark.parametrize("limit", ["max_archive_bytes", "max_unpacked_bytes", "max_metadata_bytes"])
@pytest.mark.parametrize("value", [0, -1, True, 1.5, None])
def test_no_implicit_or_unlimited_budget(limit, value):
    profile, wheels = _inputs()
    with pytest.raises(ProfileError, match="positive byte budgets"):
        _verify(profile, wheels, **{limit: value})

def test_first_party_wrapper_still_requires_its_locked_generator():
    from promotion_python_artifacts import PromotionArtifactError, validate_wheel

    profile, wheels = _inputs()
    record = profile.record()["wheels"][0]
    for generator in (None, "", "invented-generator"):
        with pytest.raises(PromotionArtifactError):
            validate_wheel(wheels[record["filename"]], record | {"generator": generator})

def test_mutation_while_inspecting_cannot_certify_a_different_archive():
    profile, wheels = _inputs()
    wheel = profile.record()["wheels"][0]
    original = wheels[wheel["filename"]].getvalue()
    changed = _encoded(wheel, extra={"unexpected.py": b"changed"})

    class MutatingStream(io.BytesIO):
        seeks = 0

        def seek(self, offset, whence=0):
            if offset == 0 and whence == 0:
                self.seeks += 1
                if self.seeks == 2:
                    self.seek(0, 2)
                    self.truncate(0)
                    super().seek(0)
                    self.write(changed)
            return super().seek(offset, whence)

    wheels[wheel["filename"]] = MutatingStream(original)
    with pytest.raises(ProfileError):
        _verify(profile, wheels)
