"""Isolated installed-content and health verification for runtime construction."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import subprocess

from installer_dependency_policy import DependencyPolicyError
from installer_engine_process import ChildLaunchError, ChildTimeoutExpired
from installer_source_build import SourceWheelInputs
from installer_wheel_inputs import _ordinary
from installer_wheel_snapshot import WheelInstallInputs, WheelReuseInputs, _digest, _open, _unchanged


_SOURCE_WHEEL_CONTENT_CHECK = """
for name, wheel in (source_wheels or {}).items():
    distribution = importlib.metadata.distribution(name)
    files = wheel["files"]
    assert isinstance(files, dict) and files, f"{name} has no inspected source wheel contents"
    for member, digest in files.items():
        relative = pathlib.PurePosixPath(member)
        assert not relative.is_absolute() and ".." not in relative.parts and \
            chr(92) not in member, "unsafe source wheel member"
        installed_file = pathlib.Path(distribution.locate_file(member)).resolve()
        assert installed_file.is_relative_to(slot) and installed_file.stat().st_size <= 64 << 20 and \
            hashlib.sha256(installed_file.read_bytes()).hexdigest() == digest, \
            f"{name} installed content differs from its source wheel: {member}"
"""

class HealthRepairRequired(RuntimeError):
    """Published health needs a distinct generation, never an in-place rebuild."""

def verify_installed_inputs(
    slot: Path, python: Path | None, module: str, *,
    origins: dict[str, str], extras: tuple[str, ...],
    local_packages: tuple[str, ...] | None, excluded_packages: list[str],
    wheel_inputs: WheelInstallInputs | WheelReuseInputs | None,
    source_wheels: SourceWheelInputs | None = None,
    health_callable: str | None = None, require_frozen_origin: bool = True,
    confirm_source_contents: bool = False, run: Callable,
) -> None:
    """Verify installed construction facts without inventing a source project."""
    if python is None:
        raise HealthRepairRequired(
            f"Runtime {slot.name} has no installed interpreter; "
            "use distinct-generation health repair, not an in-place rebuild."
        )
    nested = python.parent.parent.name == ".venv"
    if nested and wheel_inputs is not None:
        verify_application_wheels(wheel_inputs, slot)
    wheel_receipt = wheel_inputs.receipt() if wheel_inputs is not None else None
    origins_argument = json.dumps(origins)
    wheel_argument = ""
    source_argument = (
        hashlib.sha256(source_wheels.canonical).hexdigest()
        if source_wheels is not None else ""
    )
    if wheel_receipt is not None:
        origins_argument = hashlib.sha256(json.dumps(
            origins, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
        wheel_argument = hashlib.sha256(json.dumps(
            wheel_receipt, sort_keys=True, separators=(",", ":"),
        ).encode("utf-8")).hexdigest()
    probe = """
import hashlib, importlib, importlib.metadata, importlib.util, json, pathlib, re, sys
from urllib.parse import unquote, urlsplit
slot = pathlib.Path(sys.argv[1]).resolve()
nested = (slot / ".venv").is_dir() and pathlib.Path(sys.prefix).samefile(slot / ".venv")
if nested:
    spec = importlib.util.spec_from_file_location(
        "_installed_runtime_paths", slot / "runtime" / "versioned_runtime.py",
    )
    paths = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(paths)
    native_path = paths._runtime_component("installer_dependency_config").native_retained_path
    def resolved(path):
        return pathlib.Path(paths._normalize_windows_extended_path(str(pathlib.Path(path).resolve())))
    slot = resolved(slot)
else:
    resolved = lambda path: pathlib.Path(path).resolve()
assert sys.version_info >= (3, 11), "installer runtime requires Python 3.11+"
wheel_binding = sys.argv[9]
expected_origins = None if wheel_binding else json.loads(sys.argv[3])
expected_extras = json.loads(sys.argv[6])
expected_selection = json.loads(sys.argv[7])
excluded_packages = json.loads(sys.argv[8])
expected_wheels = None
source_wheels = None
proof = slot / ".runtime-input-origins.json"
assert not proof.is_symlink(), "frozen origin evidence is a symlink"
if sys.argv[5] == "fresh" or expected_extras or expected_selection is not None or wheel_binding:
    assert proof.is_file(), "fresh runtime has no frozen origin evidence"
recorded_origins = None
if proof.exists():
    receipt = json.loads(proof.read_text(encoding="utf-8"))
    assert receipt["schema"] == "installer-engine.frozen-input-origins" and \
        type(receipt["version"]) is int and receipt["version"] == (2 if wheel_binding else 1), \
        "frozen origin evidence has an unsupported schema"
    recorded_origins = receipt["packages"]
    source_wheels = receipt.get("source_wheels")
    if sys.argv[10]:
        assert hashlib.sha256(json.dumps(source_wheels, sort_keys=True, separators=(",", ":")).encode()).hexdigest() \
            == sys.argv[10], "source wheel evidence differs from construction"
    if source_wheels is not None:
        assert not wheel_binding and isinstance(source_wheels, dict) and \
            source_wheels and set(source_wheels) == set(recorded_origins), \
            "source wheel evidence differs from its origins"
        expected_origins = {
            re.sub(r"[-_.]+", "-", name).lower(): origin
            for name, origin in expected_origins.items()
        }
    if wheel_binding:
        expected_wheels = receipt["wheel_inputs"]
        assert hashlib.sha256(json.dumps(expected_wheels, sort_keys=True, separators=(",", ":")).encode()).hexdigest() \
            == wheel_binding, "frozen origin evidence differs from the selected wheel inputs"
        assert isinstance(recorded_origins, dict) and set(recorded_origins) == set(expected_wheels["distributions"]), \
            "frozen origin evidence does not cover the complete wheel closure"
        if sys.argv[5] == "fresh":
            assert hashlib.sha256(json.dumps(recorded_origins, sort_keys=True, separators=(",", ":")).encode()).hexdigest() \
                == sys.argv[3], "frozen origin evidence differs from construction"
        expected_origins = recorded_origins
    else:
        assert receipt.get("wheel_inputs") is None, \
            "source origin evidence unexpectedly contains wheel inputs"
    assert receipt.get("extras", []) == expected_extras, \
        "frozen origin evidence differs from the selected dependency profile"
    assert receipt.get("local_packages") == expected_selection, \
        "frozen origin evidence differs from the selected local packages"
    assert isinstance(recorded_origins, dict) and set(recorded_origins) == set(expected_origins), \
        "frozen origin evidence does not cover the dependency graph"
    if sys.argv[5] == "fresh":
        assert recorded_origins == expected_origins, "frozen origin evidence differs from construction"
if expected_wheels is not None:
    import re
    installed = [
        re.sub(r"[-_.]+", "-", distribution.metadata["Name"]).lower()
        for distribution in importlib.metadata.distributions()
    ]
    assert len(installed) == len(set(installed)) and set(installed) == set(expected_origins), \
        "installed distribution set differs from the complete wheel closure"
for name, expected in expected_origins.items():
    distribution = importlib.metadata.distribution(name)
    assert resolved(distribution.locate_file("")).is_relative_to(slot), \
        f"{name} metadata is outside the candidate runtime"
    origin = json.loads(distribution.read_text("direct_url.json") or "{}")
    if expected_wheels is not None or source_wheels is not None:
        wheel = expected_wheels["distributions"][name] if expected_wheels is not None else source_wheels[name]
        assert distribution.version == wheel["version"], f"{name} installed version differs from inspected metadata"
        archive = origin.get("archive_info")
        digest = wheel["sha256"].removeprefix("sha256:")
        assert "dir_info" not in origin and isinstance(archive, dict) and \
            set(archive) <= {"hash", "hashes"} and \
            archive.get("hash", "sha256=" + digest) == "sha256=" + digest and \
            (archive == {} or archive.get("hashes") == {"sha256": digest}) and \
            (nested or str(origin.get("url", "")).endswith("#sha256=" + digest)), \
            f"{name} was not installed from the inspected wheel archive"
    else:
        assert origin.get("dir_info") == {"editable": False} or \
            origin.get("dir_info") == {}, f"{name} is not a non-editable local installation"
    if nested and expected_wheels is not None:
        actual = urlsplit(origin.get("url", ""))
        copied = urlsplit((slot / "wheels" / wheel["filename"]).as_uri())
        assert actual.scheme == copied.scheme and actual.netloc == copied.netloc and \
            unquote(actual.path) == unquote(copied.path) and not actual.query and not actual.fragment, \
            f"{name} was not installed from its pinned slot wheel"
        if name in (expected_selection or []):
            import base64, csv, io, zipfile
            with zipfile.ZipFile(slot / "wheels" / wheel["filename"]) as bundle:
                records = [
                    member for member in bundle.namelist()
                    if member.count("/") == 1 and member.endswith(".dist-info/RECORD")
                ]
                assert len(records) == 1, f"{name} has ambiguous installed wheel RECORD"
                data = bundle.read(records[0])
                assert len(data) <= 4 << 20, f"{name} wheel RECORD exceeds its bound"
                for member, recorded_hash, recorded_size in csv.reader(io.StringIO(data.decode("utf-8"))):
                    if member == records[0]:
                        continue
                    path = resolved(distribution.locate_file(member))
                    native = pathlib.Path(native_path(path))
                    assert path.is_relative_to(slot) and native.stat().st_size == int(recorded_size), \
                        f"{name} installed member differs from its wheel: {member}"
                    algorithm, encoded = recorded_hash.split("=", 1)
                    digest = hashlib.new(algorithm)
                    with native.open("rb") as stream:
                        for chunk in iter(lambda: stream.read(1 << 16), b""):
                            digest.update(chunk)
                    assert base64.urlsafe_b64encode(digest.digest()).rstrip(b"=").decode() == encoded, \
                        f"{name} installed content differs from its wheel: {member}"
    elif sys.argv[5] == "fresh":
        assert origin.get("url") == expected, f"{name} was not installed from frozen input"
    elif recorded_origins is not None:
        assert origin.get("url") == recorded_origins[name], \
            f"{name} differs from its recorded frozen installation origin"
    else:
        staging = (slot.parent.parent / ".installer-inputs").as_uri() + "/frozen-"
        assert str(origin.get("url", "")).startswith(staging), \
            f"{name} has no attributable frozen installation origin"
for name in excluded_packages:
    try:
        importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        continue
    raise AssertionError(f"excluded local package is installed: {name}")
"""
    if confirm_source_contents:
        probe += _SOURCE_WHEEL_CONTENT_CHECK
    probe = "import sys\ntry:\n" + "".join(
        "    " + line + "\n" for line in probe.splitlines()
    ) + """except Exception:
    if sys.argv[5] == "receipt-reuse":
        print("Published runtime input evidence differs from installed inputs", file=sys.stderr)
        raise SystemExit(74)
    raise
module = importlib.import_module(sys.argv[2])
assert module.__file__ and resolved(module.__file__).is_relative_to(slot), \
    "health module is outside the candidate runtime"
if sys.argv[4]:
    getattr(module, sys.argv[4])()
"""
    try:
        run(
            [str(python), "-I", "-B", "-c", probe, str(slot), module, origins_argument,
             health_callable or "", "fresh" if require_frozen_origin else (
                 "receipt-reuse" if isinstance(wheel_inputs, WheelReuseInputs) else "reuse"
             ),
             json.dumps(extras), json.dumps(local_packages), json.dumps(excluded_packages),
             wheel_argument, source_argument],
        )
        if nested and wheel_inputs is not None:
            verify_application_wheels(wheel_inputs, slot)
    except (subprocess.CalledProcessError, ChildLaunchError, ChildTimeoutExpired) as error:
        if isinstance(wheel_inputs, WheelReuseInputs) and getattr(error, "returncode", None) == 74:
            raise DependencyPolicyError("Published runtime input evidence differs from installed inputs") from error
        if require_frozen_origin:
            raise
        raise HealthRepairRequired(
            f"Published runtime {slot.name} failed isolated installed health; "
            f"use distinct-generation repair.\n{getattr(error, 'stderr', None) or error}"
        ) from error

def verify_application_wheels(
    wheel_inputs: WheelInstallInputs | WheelReuseInputs, slot: Path,
) -> None:
    expected = {
        filename: digest for _name, _version, filename, digest in wheel_inputs.distributions
    }
    directory = slot / "wheels"
    if {path.name for path in directory.iterdir()} != set(expected):
        raise RuntimeError("Application wheel set differs from its retained construction inputs")
    for filename, digest in expected.items():
        path = directory / filename
        before = _ordinary(path)
        with _open(path) as stream:
            opened = _unchanged(path, before, stream)
            if _digest(stream, before.st_size, wheel_inputs.deadline)[0] != digest:
                raise RuntimeError("Application wheel differs from its retained construction inputs")
            _unchanged(path, before, stream, opened=opened)
