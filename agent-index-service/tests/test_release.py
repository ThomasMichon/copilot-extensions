"""Synthetic standard wheels only: no native stack, processes or network."""

from __future__ import annotations

import base64
import copy
import csv
import hashlib
import io
import json
import stat
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from packaging.version import Version

from agent_index_service import release
from agent_index_service.release import ReleaseError, build_descriptor, verify_descriptor

COMMIT = "0123456789abcdef" * 2 + "01234567"
VERSIONS = {
    "agent-index-service": "0.1.1.dev1",
    "agent-index": "0.10.12.dev1",
    "agent-zdd": "0.1.0.dev7",
    "agent-procutil": "0.1.0.dev7",
    "agent-dropin-registry": "0.1.0.dev2",
}
REQUIREMENTS = {
    "agent-index-service": [
        "agent-index>=0.10.12.dev1",
        'agent-index[store,server]>=0.10.12.dev1; extra == "native"',
        "pyyaml>=6.0",
    ],
    "agent-index": ["agent-zdd", "agent-procutil", "agent-dropin-registry", "httpx>=0.27"],
}
EXTRAS = {"agent-index-service": ["native"], "agent-index": ["store", "server"]}
WHEEL = "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"


def _record_bytes(entries, record_name, algorithm="sha256"):
    output = io.StringIO(newline="")
    writer = csv.writer(output)
    signatures = {record_name + ".jws", record_name + ".p7s"}
    for name, value in entries.items():
        if name == record_name or name in signatures or name.endswith("/"):
            continue
        digest = hashlib.new(algorithm, value).digest()
        writer.writerow([
            name, algorithm + "=" + base64.urlsafe_b64encode(digest).rstrip(b"=").decode(),
            str(len(value)),
        ])
    writer.writerow([record_name, "", ""])
    return output.getvalue().encode()


def _write_archive(path, entries, *, regenerate=True):
    if regenerate:
        record_name = next((name for name in entries if name.endswith("/RECORD")), None)
        if record_name is None:
            metadata = next(name for name in entries if name.endswith("/METADATA"))
            record_name = metadata.removesuffix("METADATA") + "RECORD"
        entries[record_name] = _record_bytes(entries, record_name)
    with zipfile.ZipFile(path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, value in entries.items():
            archive.writestr(name, value)


def _make_wheel(root, name, version, requirements=None):
    normalized = str(Version(version))
    escaped = name.replace("-", "_")
    filename = f"{escaped}-{normalized}-py3-none-any.whl"
    info = f"{escaped}-{normalized}.dist-info"
    lines = ["Metadata-Version: 2.4", f"Name: {name}", f"Version: {version}"]
    lines.extend(f"Requires-Dist: {req}" for req in (
        REQUIREMENTS.get(name, []) if requirements is None else requirements
    ))
    lines.extend(f"Provides-Extra: {extra}" for extra in EXTRAS.get(name, []))
    path = root / filename
    _write_archive(path, {
        f"{info}/METADATA": ("\n".join(lines) + "\n\n").encode(),
        f"{info}/WHEEL": WHEEL.encode(),
        f"{escaped}/__init__.py": b"raise AssertionError('must not import wheel')\n",
    })
    return path


def _find(root, distribution):
    return next(root.glob(f"{distribution.replace('-', '_')}-*.whl"))


def _rewrite(path, transform, *, regenerate=True):
    with zipfile.ZipFile(path) as archive:
        entries = {entry.filename: archive.read(entry) for entry in archive.infolist()}
    changed = transform(entries)
    _write_archive(path, changed, regenerate=regenerate)


def _mutate_metadata(bundle, transform, name="agent-index-service", record="METADATA"):
    path = _find(bundle, name)

    def mutate(entries):
        key = next(key for key in entries if key.endswith(f"/{record}"))
        entries[key] = transform(entries[key].decode()).encode()
        return entries

    _rewrite(path, mutate)


@pytest.fixture
def bundle(tmp_path):
    root = tmp_path / "bundle"
    root.mkdir()
    for name, version in VERSIONS.items():
        _make_wheel(root, name, version)
    return root


@pytest.fixture
def descriptor(bundle):
    return build_descriptor(bundle, source_commit=COMMIT)


def _save(bundle, descriptor):
    path = bundle / "release.json"
    path.write_text(json.dumps(descriptor), encoding="utf-8")
    return path


def test_exact_shape_and_round_trip_without_writes(bundle):
    before = {path.name: path.read_bytes() for path in bundle.iterdir()}
    result = build_descriptor(bundle, source_commit=COMMIT)
    expected = {
        "schema": "agent-index-service.release", "schema_version": 1,
        "source_commit": COMMIT, "service_version": VERSIONS["agent-index-service"],
        "core_version": VERSIONS["agent-index"], "config_schema_version": 1,
        "artifacts": [{
            "distribution": name, "version": VERSIONS[name],
            "file": _find(bundle, name).name,
            "sha256": hashlib.sha256(_find(bundle, name).read_bytes()).hexdigest(),
        } for name in sorted(VERSIONS)],
    }
    assert result == expected
    assert {path.name: path.read_bytes() for path in bundle.iterdir()} == before
    path = _save(bundle, result)
    before[path.name] = path.read_bytes()
    assert verify_descriptor(path, expected_source_commit=COMMIT) == expected
    assert {path.name: path.read_bytes() for path in bundle.iterdir()} == before


def test_canonical_versions_and_artifact_order(bundle, descriptor):
    descriptor["service_version"] = "0.1.1-dev1"
    descriptor["artifacts"].reverse()
    for artifact in descriptor["artifacts"]:
        artifact["version"] = artifact["version"].replace(".dev", "-dev")
    result = verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)
    assert result == build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("commit", ["a" * 39, "a" * 41, "A" * 40, "g" * 40, "", None, 1])
def test_commit_is_strict(bundle, descriptor, commit):
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=commit)
    with pytest.raises(ReleaseError):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=commit)
    descriptor["source_commit"] = commit
    with pytest.raises(ReleaseError):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


def test_wrong_trusted_revision(bundle, descriptor):
    with pytest.raises(ReleaseError, match="trusted revision"):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit="f" * 40)


def test_changed_bytes_fail_hash_even_when_zip_still_reads(bundle, descriptor):
    with _find(bundle, "agent-index").open("ab") as stream:
        stream.write(b"changed")
    with pytest.raises(ReleaseError, match="digest"):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


@pytest.mark.parametrize("version", ["not-a-version", "", " 1.0", None, 1])
def test_bad_descriptor_versions(bundle, descriptor, version):
    for key in ("service_version", "core_version"):
        bad = copy.deepcopy(descriptor)
        bad[key] = version
        with pytest.raises(ReleaseError):
            verify_descriptor(_save(bundle, bad), expected_source_commit=COMMIT)
    descriptor["artifacts"][0]["version"] = version
    with pytest.raises(ReleaseError):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


@pytest.mark.parametrize("field", ["service_version", "core_version"])
def test_top_level_version_mismatch(bundle, descriptor, field):
    descriptor[field] = "99.0"
    with pytest.raises(ReleaseError, match="versions"):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


@pytest.mark.parametrize("field,value", [
    ("version", "99.0"), ("distribution", "agent-unknown"), ("distribution", []),
    ("sha256", "f" * 64), ("sha256", "A" * 64), ("sha256", "a" * 63),
    ("sha256", "g" * 64), ("sha256", None),
])
def test_artifact_identity_and_digest_validation(bundle, descriptor, field, value):
    descriptor["artifacts"][0][field] = value
    with pytest.raises(ReleaseError):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate"])
def test_exact_bundle_distributions(bundle, change):
    if change == "missing":
        _find(bundle, "agent-zdd").unlink()
    elif change == "extra":
        _make_wheel(bundle, "agent-unknown", "1.0")
    else:
        _make_wheel(bundle, "agent-zdd", "9.0")
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("change", ["missing", "extra", "duplicate"])
def test_exact_descriptor_artifacts(bundle, descriptor, change):
    if change == "missing":
        descriptor["artifacts"].pop()
    elif change == "extra":
        descriptor["artifacts"].append(copy.deepcopy(descriptor["artifacts"][0]))
    else:
        descriptor["artifacts"][-1] = copy.deepcopy(descriptor["artifacts"][0])
    with pytest.raises(ReleaseError):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


def test_missing_wheel(bundle, descriptor):
    _find(bundle, "agent-zdd").unlink()
    with pytest.raises(ReleaseError):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


def test_wheel_filename_identity(bundle):
    path = _find(bundle, "agent-index-service")
    path.rename(bundle / path.name.replace("0.1.1.dev1", "9.0"))
    with pytest.raises(ReleaseError, match="identity"):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("old,new", [
    ("Name: agent-index-service", "Name: agent-index"),
    ("Version: 0.1.1.dev1", "Version: 9.0"),
    ("Version: 0.1.1.dev1", "Version: malformed"),
    ("Name: agent-index-service", ""),
    ("Version: 0.1.1.dev1", ""),
    ("Metadata-Version: 2.4", ""),
    ("Metadata-Version: 2.4", "Metadata-Version: 999"),
    ("Name: agent-index-service", "Name: agent-index-service\nName: agent-index-service"),
    ("Version: 0.1.1.dev1", "Version: 0.1.1.dev1\nVersion: 0.1.1.dev1"),
    ("Metadata-Version: 2.4", "Metadata-Version: 2.4\nMetadata-Version: 2.4"),
])
def test_bad_wheel_metadata(bundle, old, new):
    _mutate_metadata(bundle, lambda text: text.replace(old, new))
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("mutation", [
    lambda text: text.replace("Tag: py3-none-any", "Tag: broken"),
    lambda text: text.replace("Tag: py3-none-any", "Tag: cp311-none-win_amd64"),
    lambda text: text.replace("Wheel-Version: 1.0", "Wheel-Version: 9.0"),
    lambda text: text.replace("Root-Is-Purelib: true", "Root-Is-Purelib: invalid"),
    lambda text: text.replace("Wheel-Version: 1.0", "Wheel-Version: 1.0\nBuild: 1"),
    lambda text: text + "unexpected body",
    lambda text: text.replace("Tag: py3-none-any", "Tag: py3-none-any\nTag: py3-none-any"),
])
def test_bad_wheel_record(bundle, mutation):
    _mutate_metadata(bundle, mutation, record="WHEEL")
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("record", ["METADATA", "WHEEL"])
def test_missing_and_multiple_zip_metadata(bundle, record):
    path = _find(bundle, "agent-index-service")
    original = path.read_bytes()

    def remove(entries):
        return {key: val for key, val in entries.items() if not key.endswith(f"/{record}")}

    _rewrite(path, remove)
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)
    path.write_bytes(original)
    with zipfile.ZipFile(path, "a") as archive:
        archive.writestr(f"other/{record}", b"unexpected metadata")
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)


def test_duplicate_zip_member(bundle):
    path = _find(bundle, "agent-index-service")
    with zipfile.ZipFile(path, "a") as archive:
        name = next(name for name in archive.namelist() if name.endswith("/METADATA"))
        data = archive.read(name)
        with pytest.warns(UserWarning, match="Duplicate name"):
            archive.writestr(name, data)
    with pytest.raises(ReleaseError, match="duplicate"):
        build_descriptor(bundle, source_commit=COMMIT)


def test_metadata_limit(bundle):
    _mutate_metadata(bundle, lambda text: text + "x" * (1024 * 1024 + 1))
    with pytest.raises(ReleaseError, match="1 MiB"):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("requirements", [
    ["agent-index>=9.0", REQUIREMENTS["agent-index-service"][1]],
    [REQUIREMENTS["agent-index-service"][0],
     'agent-index[store,server]>=9.0; extra == "native"'],
    [REQUIREMENTS["agent-index-service"][0]],
    [REQUIREMENTS["agent-index-service"][1]],
    ["agent-index", 'agent-index[store]; extra == "native"'],
    ["agent-index", 'agent-index[store,server,missing]; extra == "native"'],
    ["agent-index @ https://invalid.example/core.whl", REQUIREMENTS["agent-index-service"][1]],
    ["agent-index", "invalid requirement ???"],
    ["agent-index", "agent-index", REQUIREMENTS["agent-index-service"][1]],
    ["agent-index", "Agent_Index", REQUIREMENTS["agent-index-service"][1]],
])
def test_service_base_native_requirements(bundle, requirements):
    _make_wheel(bundle, "agent-index-service", VERSIONS["agent-index-service"], requirements)
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("requirements", [
    ["agent-zdd", "agent-procutil"],
    ["agent-zdd>=9.0", "agent-procutil", "agent-dropin-registry"],
    ["zdd", "agent-procutil", "agent-dropin-registry"],
    ['agent-zdd; sys_platform == "linux"', "agent-procutil", "agent-dropin-registry"],
    ["agent-zdd @ file:///not-a-bundled-wheel", "agent-procutil", "agent-dropin-registry"],
])
def test_core_shared_dependency_contract(bundle, requirements):
    _make_wheel(bundle, "agent-index", VERSIONS["agent-index"], requirements)
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("filename", [
    "../escape.whl", "/escape.whl", "C:\\escape.whl", "C:escape.whl",
    "\\\\server\\share\\escape.whl", "sub\\escape.whl", "sub/escape.whl",
    "file:///escape.whl", "escape.whl:stream", " escape.whl", "escape.whl\n",
])
def test_path_aliases_are_rejected(bundle, descriptor, filename):
    descriptor["artifacts"][0]["file"] = filename
    with pytest.raises(ReleaseError):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


def test_real_symlink_wheel(bundle, descriptor, tmp_path):
    path = _find(bundle, "agent-index")
    outside = tmp_path / path.name
    outside.write_bytes(path.read_bytes())
    path.unlink()
    try:
        path.symlink_to(outside)
    except OSError as exc:
        pytest.skip(f"host cannot create unprivileged symlinks: {exc}")
    with pytest.raises(ReleaseError, match="symlink"):
        build_descriptor(bundle, source_commit=COMMIT)
    with pytest.raises(ReleaseError, match="symlink"):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


@pytest.mark.parametrize("mode,attributes", [(stat.S_IFLNK, 0), (stat.S_IFREG, 0x400)])
def test_link_guard_rejects_before_open(bundle, descriptor, monkeypatch, mode, attributes):
    path = _find(bundle, "agent-index")
    real_stat = Path.lstat

    def lstat(candidate):
        if candidate == path:
            return SimpleNamespace(st_mode=mode, st_file_attributes=attributes)
        return real_stat(candidate)

    monkeypatch.setattr(Path, "lstat", lstat)
    with pytest.raises(ReleaseError, match="symlink"):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


@pytest.mark.parametrize("field,value", [
    ("schema", "unknown"), ("schema_version", True), ("schema_version", 2),
    ("config_schema_version", True), ("config_schema_version", 2),
    ("artifacts", {}), ("artifacts", None),
])
def test_strict_descriptor_schema(bundle, descriptor, field, value):
    descriptor[field] = value
    with pytest.raises(ReleaseError):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


@pytest.mark.parametrize("location", ["root", "artifact"])
def test_unknown_and_missing_keys(bundle, descriptor, location):
    target = descriptor if location == "root" else descriptor["artifacts"][0]
    target["unknown"] = "not accepted"
    with pytest.raises(ReleaseError):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)
    del target["unknown"]
    target.pop(next(iter(target)))
    with pytest.raises(ReleaseError):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


@pytest.mark.parametrize("replacement", [
    '{"schema": "agent-index-service.release", "schema": "agent-index-service.release",',
    '{"schema": NaN,', '{"schema": Infinity,', '{"schema": "agent-index-service.release",',
])
def test_malformed_or_duplicate_json(bundle, descriptor, replacement):
    path = _save(bundle, descriptor)
    text = path.read_text()
    path.write_text(text.replace('{"schema": "agent-index-service.release",', replacement, 1))
    if replacement == '{"schema": "agent-index-service.release",':
        path.write_text("{not valid json")
    with pytest.raises(ReleaseError):
        verify_descriptor(path, expected_source_commit=COMMIT)


def test_duplicate_nested_json_keys(bundle, descriptor):
    path = _save(bundle, descriptor)
    text = path.read_text()
    path.write_text(text.replace('"distribution":', '"version": "1.0", "distribution":', 1))
    with pytest.raises(ReleaseError, match="duplicate"):
        verify_descriptor(path, expected_source_commit=COMMIT)


def test_verify_does_not_adopt_unlisted_siblings(bundle, descriptor):
    path = _save(bundle, descriptor)
    _make_wheel(bundle, "unlisted-third-party", "1.0")
    assert verify_descriptor(path, expected_source_commit=COMMIT) == descriptor


def test_non_zip_wheel(bundle):
    _find(bundle, "agent-index").write_bytes(b"not a zip archive")
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)


def test_descriptor_limit(bundle):
    path = bundle / "release.json"
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ReleaseError, match="1 MiB"):
        verify_descriptor(path, expected_source_commit=COMMIT)


@pytest.mark.parametrize("kind", ["bundle", "descriptor"])
def test_real_symlink_roots_and_descriptor(bundle, descriptor, tmp_path, kind):
    original = bundle if kind == "bundle" else _save(bundle, descriptor)
    link = tmp_path / "alias"
    try:
        link.symlink_to(original, target_is_directory=kind == "bundle")
    except OSError as exc:
        pytest.skip(f"host cannot create unprivileged symlinks: {exc}")
    with pytest.raises(ReleaseError, match="symlink"):
        if kind == "bundle":
            build_descriptor(link, source_commit=COMMIT)
        else:
            verify_descriptor(link, expected_source_commit=COMMIT)


def test_header_encoding_error(bundle):
    path = _find(bundle, "agent-index-service")

    def invalid(entries):
        key = next(key for key in entries if key.endswith("/METADATA"))
        entries[key] = b"\xff"
        return entries

    _rewrite(path, invalid)
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)


def test_distribution_alias_normalization(bundle):
    _mutate_metadata(bundle, lambda text: text.replace(
        "Name: agent-index-service", "Name: Agent_Index_Service",
    ))
    assert build_descriptor(bundle, source_commit=COMMIT)["service_version"] == "0.1.1.dev1"


def test_metadata_requirements_rechecked_during_verification(bundle, descriptor):
    _make_wheel(bundle, "agent-index-service", VERSIONS["agent-index-service"], [
        "agent-index>=99", REQUIREMENTS["agent-index-service"][1],
    ])
    for artifact in descriptor["artifacts"]:
        if artifact["distribution"] == "agent-index-service":
            wheel_bytes = _find(bundle, artifact["distribution"]).read_bytes()
            artifact["sha256"] = hashlib.sha256(wheel_bytes).hexdigest()
    with pytest.raises(ReleaseError, match="incompatible"):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


@pytest.mark.parametrize("text", ["[]", "null", "true"])
def test_descriptor_requires_json_object(bundle, text):
    path = bundle / "release.json"
    path.write_text(text)
    with pytest.raises(ReleaseError):
        verify_descriptor(path, expected_source_commit=COMMIT)


@pytest.mark.parametrize("header,values", [
    ("Provides-Dist", ["example-one", "example-two"]),
    ("Obsoletes-Dist", ["example-old", "example-older"]),
    ("Import-Name", ["example_one", "example_two"]),
    ("Import-Namespace", ["example.one", "example.two"]),
])
def test_standard_repeatable_headers(bundle, header, values):
    _mutate_metadata(bundle, lambda text: text.replace(
        "Metadata-Version: 2.4",
        "Metadata-Version: 2.6\n" + "\n".join(f"{header}: {value}" for value in values),
    ))
    descriptor = build_descriptor(bundle, source_commit=COMMIT)
    actual = verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)
    assert actual == descriptor


def test_core_metadata_26(bundle):
    _mutate_metadata(bundle, lambda text: text.replace("Metadata-Version: 2.4",
                                                     "Metadata-Version: 2.6"))
    assert build_descriptor(bundle, source_commit=COMMIT)["service_version"] == "0.1.1.dev1"


def _change_record(bundle, change):
    def mutate(entries):
        name = next(name for name in entries if name.endswith("/RECORD"))
        rows = list(csv.reader(io.StringIO(entries[name].decode(), newline="")))
        rows = change(rows)
        output = io.StringIO(newline="")
        csv.writer(output).writerows(rows)
        entries[name] = output.getvalue().encode()
        return entries

    _rewrite(_find(bundle, "agent-index-service"), mutate, regenerate=False)


@pytest.mark.parametrize("kind", ["missing", "second", "wrong-directory", "duplicate-member"])
def test_record_location_and_uniqueness(bundle, kind):
    def mutate(entries):
        name = next(name for name in entries if name.endswith("/RECORD"))
        if kind == "missing":
            del entries[name]
        elif kind == "wrong-directory":
            entries["other.dist-info/RECORD"] = entries.pop(name)
        elif kind == "second":
            entries["other.dist-info/RECORD"] = entries[name]
        return entries

    path = _find(bundle, "agent-index-service")
    _rewrite(path, mutate, regenerate=False)
    if kind == "duplicate-member":
        with zipfile.ZipFile(path, "a") as archive:
            name = next(name for name in archive.namelist() if name.endswith("/RECORD"))
            data = archive.read(name)
            with pytest.warns(UserWarning, match="Duplicate name"):
                archive.writestr(name, data)
    with pytest.raises(ReleaseError, match="RECORD|duplicate"):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("change", [
    lambda rows: rows + [rows[0]],
    lambda rows: rows[1:],
    lambda rows: rows[:-1],
    lambda rows: rows + [["unlisted.py", "sha256=anything", "1"]],
    lambda rows: [rows[0] + ["extra"]] + rows[1:],
    lambda rows: [["../escape", *rows[0][1:]]] + rows[1:],
    lambda rows: [[rows[0][0], "", rows[0][2]]] + rows[1:],
    lambda rows: [[rows[0][0], rows[0][1], ""]] + rows[1:],
    lambda rows: [[rows[0][0], rows[0][1], "999"]] + rows[1:],
    lambda rows: [[rows[0][0], rows[0][1], "-1"]] + rows[1:],
    lambda rows: [[rows[0][0], rows[0][1], "1.0"]] + rows[1:],
    lambda rows: [*rows[:-1], [rows[-1][0], "sha256=invalid", ""]],
])
def test_invalid_record_rows_inventory_hash_size(bundle, change):
    _change_record(bundle, change)
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("digest", [
    "md5=abc", "sha1=abc", "md5-sha1=abc", "sha224=abc", "shake_256=abc", "unknown=abc",
    "sha256=not+urlsafe", "sha256=haspadding=", "sha256=short", "sha256=", "sha256",
])
def test_unsupported_weak_or_malformed_record_digest(bundle, digest):
    _change_record(bundle, lambda rows: [[rows[0][0], digest, rows[0][2]]] + rows[1:])
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("algorithm", ["sha256", "sha384", "sha512", "sha3_256", "blake2b"])
def test_supported_secure_record_hashes(bundle, algorithm):
    def mutate(entries):
        name = next(name for name in entries if name.endswith("/RECORD"))
        entries[name] = _record_bytes(entries, name, algorithm)
        return entries

    _rewrite(_find(bundle, "agent-index-service"), mutate, regenerate=False)
    assert build_descriptor(bundle, source_commit=COMMIT)["service_version"] == "0.1.1.dev1"


def test_payload_tamper_fails_even_with_new_outer_digest(bundle, descriptor):
    path = _find(bundle, "agent-index-service")

    def mutate(entries):
        name = next(name for name in entries if name.endswith("/__init__.py"))
        entries[name] = b"tampered"
        return entries

    _rewrite(path, mutate, regenerate=False)
    for artifact in descriptor["artifacts"]:
        if artifact["distribution"] == "agent-index-service":
            artifact["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ReleaseError, match="size|digest"):
        verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)


@pytest.mark.parametrize("kind", ["unlisted", "missing"])
def test_payload_inventory(bundle, kind):
    def mutate(entries):
        if kind == "unlisted":
            entries["unlisted.py"] = b"unlisted"
        else:
            del entries[next(name for name in entries if name.endswith("/__init__.py"))]
        return entries

    _rewrite(_find(bundle, "agent-index-service"), mutate, regenerate=False)
    with pytest.raises(ReleaseError, match="inventory"):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("name", [
    "../escape.py", "/absolute.py", "pkg/../escape.py", "pkg//alias.py",
    "pkg/./alias.py", "C:drive.py", "pkg\\backslash.py", "pkg/null\x00.py",
])
def test_unsafe_archive_paths(bundle, name):
    def mutate(entries):
        entries[name] = b"unsafe"
        return entries

    _rewrite(_find(bundle, "agent-index-service"), mutate)
    with pytest.raises(ReleaseError, match="unsafe|inventory"):
        build_descriptor(bundle, source_commit=COMMIT)


def test_archive_symlink_member(bundle):
    path = _find(bundle, "agent-index-service")
    with zipfile.ZipFile(path, "a") as archive:
        entry = zipfile.ZipInfo("link")
        entry.create_system = 3
        entry.external_attr = (stat.S_IFLNK | 0o777) << 16
        archive.writestr(entry, "target")
    with pytest.raises(ReleaseError, match="regular"):
        build_descriptor(bundle, source_commit=COMMIT)


def test_standard_signature_exceptions_and_directory_entries(bundle):
    def mutate(entries):
        record = next(name for name in entries if name.endswith("/RECORD"))
        entries[record + ".jws"] = b"legacy-signature"
        entries[record + ".p7s"] = b"legacy-signature"
        entries["package/"] = b""
        entries["package/unicode-\u00e9, space.txt"] = b"valid CSV quoting and UTF-8"
        return entries

    _rewrite(_find(bundle, "agent-index-service"), mutate)
    descriptor = build_descriptor(bundle, source_commit=COMMIT)
    actual = verify_descriptor(_save(bundle, descriptor), expected_source_commit=COMMIT)
    assert actual == descriptor


def test_signature_must_not_be_listed_in_record(bundle):
    def mutate(entries):
        record = next(name for name in entries if name.endswith("/RECORD"))
        entries[record + ".jws"] = b"signature"
        entries[record] += f"{record}.jws,,\n".encode()
        return entries

    _rewrite(_find(bundle, "agent-index-service"), mutate, regenerate=False)
    with pytest.raises(ReleaseError, match="inventory"):
        build_descriptor(bundle, source_commit=COMMIT)


@pytest.mark.parametrize("limit", ["member", "total", "count"])
def test_archive_resource_limits(bundle, monkeypatch, limit):
    if limit == "member":
        monkeypatch.setattr(release, "_MEMBER_LIMIT", 1024)
        _rewrite(_find(bundle, "agent-index-service"),
                 lambda entries: {**entries, "oversized.bin": b"x" * 1025})
    elif limit == "total":
        monkeypatch.setattr(release, "_TOTAL_LIMIT", 100)
    else:
        monkeypatch.setattr(release, "_ENTRY_LIMIT", 2)
    with pytest.raises(ReleaseError, match="limit"):
        build_descriptor(bundle, source_commit=COMMIT)


def test_record_csv_is_bounded(bundle, monkeypatch):
    monkeypatch.setattr(release, "_LIMIT", 128)
    with pytest.raises(ReleaseError, match="RECORD.*1 MiB"):
        build_descriptor(bundle, source_commit=COMMIT)


def test_malformed_record_csv(bundle):
    def mutate(entries):
        name = next(name for name in entries if name.endswith("/RECORD"))
        entries[name] = b'"unterminated'
        return entries

    _rewrite(_find(bundle, "agent-index-service"), mutate, regenerate=False)
    with pytest.raises(ReleaseError, match="CSV"):
        build_descriptor(bundle, source_commit=COMMIT)


def test_same_size_payload_tamper_is_digest_failure(bundle):
    def mutate(entries):
        name = next(name for name in entries if name.endswith("/__init__.py"))
        entries[name] = b"x" * len(entries[name])
        return entries

    _rewrite(_find(bundle, "agent-index-service"), mutate, regenerate=False)
    with pytest.raises(ReleaseError, match="RECORD digest mismatch"):
        build_descriptor(bundle, source_commit=COMMIT)


def test_signature_elsewhere_requires_normal_hash_and_size(bundle):
    def mutate(entries):
        entries["package/RECORD.jws"] = b"not a signature exemption"
        return entries

    _rewrite(_find(bundle, "agent-index-service"), mutate, regenerate=False)
    with pytest.raises(ReleaseError, match="inventory"):
        build_descriptor(bundle, source_commit=COMMIT)


def test_archive_compressed_size_is_bounded(bundle, monkeypatch):
    monkeypatch.setattr(release, "_ARCHIVE_LIMIT", 100)
    with pytest.raises(ReleaseError, match="archive limit"):
        build_descriptor(bundle, source_commit=COMMIT)


def test_streaming_member_limit_not_only_zip_header(bundle, monkeypatch):
    real_open = zipfile.ZipFile.open

    def expanded(archive, entry, *args, **kwargs):
        if isinstance(entry, zipfile.ZipInfo) and entry.filename.endswith("/__init__.py"):
            return io.BytesIO(b"x" * 1025)
        return real_open(archive, entry, *args, **kwargs)

    monkeypatch.setattr(release, "_MEMBER_LIMIT", 1024)
    monkeypatch.setattr(zipfile.ZipFile, "open", expanded)
    with pytest.raises(ReleaseError, match="streaming decompression limit"):
        build_descriptor(bundle, source_commit=COMMIT)


def test_zip_crc_failure_is_explicit(bundle):
    path = _find(bundle, "agent-index-service")
    with zipfile.ZipFile(path) as archive:
        entry = next(entry for entry in archive.infolist()
                     if entry.filename.endswith("/__init__.py"))
        offset = entry.header_offset
    data = bytearray(path.read_bytes())
    # ZIP local header's filename/extra lengths locate the compressed payload.
    filename_length = int.from_bytes(data[offset + 26:offset + 28], "little")
    extra_length = int.from_bytes(data[offset + 28:offset + 30], "little")
    payload = offset + 30 + filename_length + extra_length
    data[payload] ^= 0xFF
    path.write_bytes(data)
    with pytest.raises(ReleaseError):
        build_descriptor(bundle, source_commit=COMMIT)
