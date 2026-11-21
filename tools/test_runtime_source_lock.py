from __future__ import annotations

from dataclasses import replace
from contextlib import AbstractContextManager, contextmanager, nullcontext
from collections.abc import Callable, Iterable, Iterator
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
from types import SimpleNamespace
from typing import Any, Protocol
import zipfile

from packaging.markers import default_environment
from packaging.tags import Tag, sys_tags
from packaging.version import Version
import pytest

import runtime_profile_policy as planner
import runtime_source_lock as locks
import installer_dependency_policy as dependencies
from installer_dependency_policy import RetainedUVInstall
from packaging.utils import parse_wheel_filename
from runtime_profile import ProfileError
from runtime_profile_policy import InstallationProfilePlan
from runtime_source_lock import SourceLockCandidate, SourceWheelPin
from versioned_runtime_source import SourceIdentity
from test_installer_dependency_policy import (
    offline_environment as _offline_environment, source_project as _source_project,
)
from test_runtime_profile_policy_plan import adopter as adopter
from test_runtime_profile_policy import _Adopter

Adopter = _Adopter
Source = tuple[Path, SourceIdentity]
Target = dict[str, Any]
offline_environment: Callable[[Path], dict[str, str]] = _offline_environment


class SourceProject(Protocol):
    def __call__(
        self, path: Path, name: str = "fixture-source", sources: str = "",
        dependencies: str = "", module_code: str | None = None,
    ) -> Path: ...


source_project: SourceProject = _source_project


def _selected_identity(payload: Path) -> Source:
    from versioned_runtime_source import SOURCE_FILE, SourceIdentity, assembly_hash, runtime_version

    digest = assembly_hash(payload)
    selected = SourceIdentity(
        "fixture-source", "1.0.0", "a" * 40, "b" * 40, digest,
        runtime_version("1.0.0", "a" * 40, digest),
    )
    (payload / SOURCE_FILE).write_text(json.dumps(selected.document()), encoding="utf-8")
    return payload, selected


@pytest.fixture
def source(tmp_path: Path) -> Source:
    payload = source_project(tmp_path / "payload")
    manifest = payload / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('version="1.0"', 'version="1.0.0"'), encoding="utf-8")
    (payload / "plugin.json").write_text(json.dumps({"name": "fixture-source", "version": "1.0.0"}))
    return _selected_identity(payload)


@pytest.fixture
def target() -> Target:
    return {"environment": default_environment(), "tags": ["py3-none-any"], "device": "cpu"}


def plan(adopter: Adopter) -> InstallationProfilePlan:
    return planner.plan_installation_profile(
        installation=adopter[1], release_index=None, repository="example/plugins",
        signer_digest="", source_digest="", source_ref="", release={}, artifact=None,
        target={}, component="fixture", roots=[], max_subject_bytes=1,
    )


def candidate(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path, *,
    wheel_witness: bool = False, **changes: Any,
) -> AbstractContextManager[SourceLockCandidate]:
    arguments: dict[str, Any] = dict(
        payload=source[0], expected_source=source[1], target=target, component="fixture",
        uv="uv", python=Path(sys.executable), max_lock_bytes=4096,
        deadline=time.monotonic() + 60, env=offline_environment(tmp_path),
    )
    retain = locks.retain_source_wheel_lock if wheel_witness else locks.retain_source_lock
    return retain(plan(adopter), **(arguments | changes))


@pytest.mark.parametrize("failure", ["timeout", "success"])
def test_expired_outer_source_transaction_preserves_active_timeout_and_diagnostics(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    import installer_wheel_inputs as inputs

    deadline = time.monotonic() + 60
    clock = [time.monotonic()]
    monkeypatch.setattr(inputs, "time", SimpleNamespace(monotonic=lambda: clock[0]))
    timeout = subprocess.TimeoutExpired(["fixture-backend"], 60, output=b"output", stderr=b"diagnostics")
    expected = subprocess.TimeoutExpired if failure == "timeout" else inputs.WheelError
    with pytest.raises(expected) as observed:
        with candidate(adopter, source, target, tmp_path, deadline=deadline):
            clock[0] = deadline + 1
            if failure == "timeout":
                raise timeout
    if failure == "timeout":
        assert observed.value is timeout
        assert isinstance(observed.value, subprocess.TimeoutExpired)
        assert observed.value.output == b"output" and observed.value.stderr == b"diagnostics"


@pytest.mark.parametrize("wheel_witness,capture_diagnostics", [(False, False), (True, False), (True, True)])
def test_native_source_lock_binds_real_owner_plan_selected_source_and_empty_closure(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
    wheel_witness: bool, capture_diagnostics: bool, monkeypatch: pytest.MonkeyPatch,
) -> None:
    # This source has no third-party requirements; an empty third-party lock is real.
    with candidate(
        adopter, source, target, tmp_path, wheel_witness=wheel_witness,
        **({"max_diagnostic_bytes": 65536} if capture_diagnostics else {}),
    ) as selected:
        record = selected.record()
        assert record["source"] == source[1].document()
        assert record["trigger"] == "release-index-unavailable"
        assert record["mode"] == "source"
        assert record["target"] == target
        assert record["policy"] == plan(adopter).policy()
        assert record["pins"] == []
        assert "artifact" not in record and "approved" not in record
        assert record["requirements_sha256"] == "sha256:" + hashlib.sha256(selected.requirements).hexdigest()
        assert selected.lock_id == "sha256:" + hashlib.sha256(selected.canonical).hexdigest()
        if wheel_witness:
            assert record["files"] == []
            assert selected.native_lock is not None
            assert record["native_lock_sha256"] == "sha256:" + hashlib.sha256(selected.native_lock).hexdigest()
        else:
            assert selected.native_lock is None
        assert selected.payload != source[0]
        assert (selected.payload / "pyproject.toml").is_file()
        scans = []
        actual_fingerprint = locks.runtime.fingerprint_source

        def fingerprint(paths: Iterable[Path], **kwargs: Any) -> str:
            scans.append(tuple(paths))
            assert callable(kwargs["on_progress"])
            return actual_fingerprint(scans[-1], **kwargs)

        with monkeypatch.context() as patch:
            patch.setattr(locks.runtime, "fingerprint_source", fingerprint)
            selected.verify(deadline=time.monotonic() + 10)
        assert len(scans) == 1
        record["source"]["source_commit"] = "c" * 40
        target["device"] = "changed-after-selection"
        assert selected.record()["source"]["source_commit"] == "a" * 40
        assert selected.record()["target"]["device"] == "cpu"
        retained = selected.payload
    assert not retained.exists()


@pytest.mark.skipif(os.name != "nt", reason="Windows native interpreter path limit")
@pytest.mark.timeout(60)
def test_source_configuration_launches_a_real_long_path_interpreter(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
) -> None:
    import venv
    from build_toolchain_lock import _query_marker_environment

    root = tmp_path
    while len(str(root / "Scripts" / "python.exe")) <= 280:
        root /= "interpreter-" + "x" * 30
    venv.EnvBuilder(with_pip=False).create(locks.dependencies._native_retained_path(root))
    python = root / "Scripts" / "python.exe"
    assert _query_marker_environment(python, deadline=time.monotonic() + 10) == target["environment"]
    with candidate(adopter, source, target, tmp_path, python=python) as selected:
        assert selected.record()["target"] == target
        assert selected.record()["source"] == source[1].document()


@pytest.mark.parametrize("changed", ["origin", "retained", "custody"])
def test_source_owner_delegation_keeps_fresh_origin_retention_and_custody_checks(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    from versioned_runtime_source import SourceIdentityError

    actual_source = locks._source
    active = True

    def verify(payload: Path, identity: SourceIdentity) -> None:
        assert payload == source[0] and identity == source[1]
        if not active:
            raise ProfileError("fixture source owner custody closed")
        actual_source(payload, identity)

    message = {
        "origin": "bytes do not match the source identity",
        "retained": "retained source differs",
        "custody": "source owner custody closed",
    }[changed]
    with pytest.raises(SourceIdentityError if changed == "origin" else ProfileError, match=message):
        with candidate(adopter, source, target, tmp_path, source_verifier=verify) as selected:
            monkeypatch.setattr(
                locks, "_source",
                lambda *args: pytest.fail("delegated origin was independently rehashed"),
            )
            selected.verify(deadline=time.monotonic() + 10)
            if changed == "custody":
                active = False
            else:
                root = source[0] if changed == "origin" else selected.payload
                manifest = root / "pyproject.toml"
                manifest.write_text(manifest.read_text() + "\n# changed after selection\n")
            selected.verify(deadline=time.monotonic() + 10)


@pytest.mark.parametrize("as_extra", [False, True])
@pytest.mark.parametrize("wheel_witness,multiple,capture_diagnostics", [
    (False, False, False), (True, False, False), (True, True, False), (True, True, True),
])
def test_native_source_lock_binds_nonempty_dated_feed_output(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
    as_extra: bool, wheel_witness: bool, multiple: bool, capture_diagnostics: bool,
    *, as_provider: bool = False,
) -> None:
    filename = "fixture_registry-1.0-py3-none-any.whl"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as wheel:
        prefix = "fixture_registry-1.0.dist-info/"
        wheel.writestr(prefix + "METADATA", "Metadata-Version: 2.1\nName: fixture-registry\nVersion: 1.0\n")
        wheel.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(prefix + "RECORD", "")
    content = archive.getvalue()
    digest = hashlib.sha256(content).hexdigest()
    filenames = [filename, "fixture_registry-1.0-py2.py3-none-any.whl"] if multiple else [filename]
    files = dict.fromkeys(filenames, content)
    if capture_diagnostics:
        native_tag = str(next(sys_tags()))
        native_filename = filename.replace("py3-none-any", native_tag)
        native_archive = io.BytesIO()
        with zipfile.ZipFile(native_archive, "w") as wheel:
            wheel.writestr(prefix + "METADATA", "Metadata-Version: 2.1\nName: fixture-registry\nVersion: 1.0\n")
            wheel.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: false\nTag: " + native_tag + "\n")
            wheel.writestr(prefix + "RECORD", "")
        files[native_filename] = native_archive.getvalue()
        filename, digest = native_filename, hashlib.sha256(files[native_filename]).hexdigest()
        target["tags"].insert(0, native_tag)

    class Index(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            self.respond(head=False)

        def do_HEAD(self) -> None:
            self.respond(head=True)

        def respond(self, *, head: bool) -> None:
            if self.path.rstrip("/") == "/simple/fixture-registry":
                body = json.dumps({
                    "meta": {"api-version": "1.2"}, "name": "fixture-registry",
                    "files": [{"filename": name, "url": "/files/" + name,
                               "hashes": {"sha256": hashlib.sha256(data).hexdigest()},
                               "upload-time": "2000-01-01T00:00:00Z"} for name, data in files.items()],
                }).encode()
                kind = "application/vnd.pypi.simple.v1+json"
            elif self.path.removeprefix("/files/") in files:
                body, kind = files[self.path.removeprefix("/files/")], "application/octet-stream"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", kind)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            if not head:
                self.wfile.write(body)

    manifest = source[0] / "pyproject.toml"
    text = manifest.read_text()
    if as_provider:
        import installer_provider_roots

        installer_provider_roots.persist(
            Path(plan(adopter).policy()["owner"]["runtimeRoot"]), ["fixture-registry"],
        )
    elif as_extra:
        text += '\n[project.optional-dependencies]\nfeature=["fixture-registry>=1"]\n'
    else:
        text = text.replace("dependencies=[]", 'dependencies=["fixture-registry>=1"]')
    if capture_diagnostics:
        helper = source_project(source[0] / "libs" / "helper", name="fixture-helper")
        text = text.replace("dependencies=[", 'dependencies=["fixture-helper", ', 1)
        text += '\n[tool.uv.sources]\nfixture-helper={path="libs/helper"}\n'
        for path in (source[0], helper):
            (path / "fixture_backend.py").write_text("raise RuntimeError('metadata backend must not execute')\n")
    manifest.write_text(text)
    selected_source = _selected_identity(source[0])
    server = ThreadingHTTPServer(("127.0.0.1", 0), Index)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = offline_environment(tmp_path)
    environment.pop("UV_OFFLINE", None)
    configuration = tmp_path / "uv.toml"
    configuration.write_text(
        f'[[index]]\nurl="http://127.0.0.1:{server.server_port}/simple"\ndefault=true\n',
        encoding="utf-8",
    )
    environment["UV_CONFIG_FILE"] = str(configuration)
    try:
        for _ in range(2 if capture_diagnostics else 1):
            with (
                pytest.raises(ProfileError, match="one exact wheel")
                if multiple and not capture_diagnostics else nullcontext()
            ), candidate(
                adopter, selected_source, target, tmp_path, env=environment,
                extras=("feature",) if as_extra else (), wheel_witness=wheel_witness,
                **({"max_diagnostic_bytes": 65536} if capture_diagnostics else {}),
            ) as selected:
                assert selected.record()["pins"] == [
                    {"name": "fixture-registry", "version": "1.0", "hashes": ["sha256:" + digest]},
                ]
                assert b"fixture-registry==1.0" in selected.requirements
                assert selected.record()["source"] == selected_source[1].document()
                assert selected.record()["extras"] == (["feature"] if as_extra else [])
                if as_provider:
                    assert selected.record()["provider_roots"] == ["fixture-registry"]
                if wheel_witness:
                    assert selected.record()["files"] == [{
                        "name": "fixture-registry", "version": "1.0", "filename": filename,
                        "sha256": "sha256:" + digest,
                }]
                    assert selected.native_lock is not None
                    assert filename.encode() in selected.native_lock
                    assert b"127.0.0.1" not in selected.canonical
                    assert b"127.0.0.1" not in selected.requirements
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def test_source_reconstruction_resolves_persistent_provider_through_existing_feed(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
) -> None:
    test_native_source_lock_binds_nonempty_dated_feed_output(
        adopter, source, target, tmp_path, as_extra=False, wheel_witness=True,
        multiple=False, capture_diagnostics=False, as_provider=True,
    )


def test_source_reconstruction_refuses_changed_provider_desire(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
) -> None:
    import installer_provider_roots

    root = Path(plan(adopter).policy()["owner"]["runtimeRoot"])
    with pytest.raises(ProfileError, match="provider roots changed"):
        with candidate(adopter, source, target, tmp_path) as selected:
            installer_provider_roots.persist(root, ["example-provider"])
            selected.verify(deadline=time.monotonic() + 10)


@pytest.mark.parametrize("field,value", [
    ("source_commit", "c" * 40), ("source_tree", "c" * 40),
    ("assembly_hash", "c" * 64), ("runtime_version", "different"),
])
def test_independently_selected_source_cannot_be_replaced(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path, field: str, value: str,
) -> None:
    with pytest.raises((ProfileError, ValueError), match="source"):
        with candidate(adopter, source, target, tmp_path, expected_source=replace(source[1], **{field: value})):
            pytest.fail("unselected source reached a candidate")


def test_source_integrity_failure_is_not_an_availability_fallback(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
) -> None:
    (source[0] / "fixture_backend.py").write_text("changed")
    with pytest.raises(ValueError, match="bytes"):
        with candidate(adopter, source, target, tmp_path):
            pytest.fail("corrupt source reached resolution")


def test_transient_source_change_cannot_be_labeled_with_restored_origin(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual = dependencies.retain_uv_source_compile
    backend = source[0] / "fixture_backend.py"
    original = backend.read_bytes()
    snapshots = []

    @contextmanager
    def changed(uv: str, arguments: list[str], **options: Any) -> Iterator[RetainedUVInstall]:
        backend.write_bytes(original + b"\nTRANSIENT = True\n")
        with actual(uv, arguments, **options) as transaction:
            snapshots.append(transaction.sources[source[0]])
            backend.write_bytes(original)
            yield transaction

    monkeypatch.setattr(dependencies, "retain_uv_source_compile", changed)
    with pytest.raises(ProfileError, match="retained source differs"):
        with candidate(adopter, source, target, tmp_path):
            pytest.fail("restored origin mislabeled a changed retained snapshot")
    assert backend.read_bytes() == original
    assert snapshots and all(not path.exists() for path in snapshots)


def test_missing_retained_source_coverage_refuses(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual = dependencies.retain_uv_source_compile

    @contextmanager
    def incomplete(uv: str, arguments: list[str], **options: Any) -> Iterator[RetainedUVInstall]:
        with actual(uv, arguments, **options) as transaction:
            transaction.source_hashes.pop(source[0] / "fixture_backend.py")
            yield transaction

    monkeypatch.setattr(dependencies, "retain_uv_source_compile", incomplete)
    with pytest.raises(ProfileError, match="cover"):
        with candidate(adopter, source, target, tmp_path):
            pytest.fail("incomplete retained inventory reached compilation")


def test_retained_source_mutation_during_consumption_refuses(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
) -> None:
    with pytest.raises(ProfileError, match="during candidate consumption"):
        with candidate(adopter, source, target, tmp_path) as selected:
            retained = selected.payload
            (retained / "fixture_backend.py").write_text("changed", encoding="utf-8")
            with pytest.raises(ProfileError, match="retained source differs"):
                selected.verify(deadline=time.monotonic() + 10)
    assert not retained.exists()


@pytest.mark.parametrize("override", ["environment", "configuration", "universal"])
def test_native_platform_override_cannot_hide_an_applicable_dependency(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path, override: str,
) -> None:
    manifest = source[0] / "pyproject.toml"
    requirement = "fixture-unavailable==1.0; sys_platform == " + repr(sys.platform)
    manifest.write_text(manifest.read_text().replace("dependencies=[]", "dependencies=[" + json.dumps(requirement) + "]"))
    selected_source = _selected_identity(source[0])
    environment = offline_environment(tmp_path)
    other_platform = "x86_64-unknown-linux-gnu" if sys.platform == "win32" else "x86_64-pc-windows-msvc"
    if override == "environment":
        environment["UV_PYTHON_PLATFORM"] = other_platform
    else:
        configuration = tmp_path / "uv.toml"
        value = "universal=true" if override == "universal" else "python-platform=" + json.dumps(other_platform)
        configuration.write_text("[pip]\n" + value + "\n")
        environment["UV_CONFIG_FILE"] = str(configuration)
    with pytest.raises(dependencies.DependencyPolicyError, match="selected target"):
        with candidate(adopter, selected_source, target, tmp_path, env=environment):
            pytest.fail("cross-platform resolution omitted an applicable dependency")


def test_source_deadline_reaches_settings_probe_during_context_entry(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual = subprocess.run
    timeouts = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[Any]:
        if "--show-settings" in command:
            timeouts.append(options["timeout"])
            raise subprocess.TimeoutExpired(command, options["timeout"])
        return actual(command, **options)

    monkeypatch.setattr(locks.subprocess, "run", run)
    with pytest.raises(dependencies.DependencyPolicyError, match="timed out"):
        with candidate(adopter, source, target, tmp_path, deadline=time.monotonic() + 5):
            pytest.fail("expired preflight reached resolution")
    assert len(timeouts) == 1 and 0 < timeouts[0] <= 5


def test_target_must_match_actual_selected_interpreter(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
) -> None:
    target["environment"]["platform_machine"] = "unavailable-target"
    with pytest.raises(ProfileError, match="interpreter"):
        with candidate(adopter, source, target, tmp_path):
            pytest.fail("wrong target reached resolution")


def test_source_candidate_preserves_declared_extras_and_refuses_unknown_ones(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
) -> None:
    with pytest.raises(ProfileError, match="undeclared extra"):
        with candidate(adopter, source, target, tmp_path, extras=("missing",)):
            pytest.fail("unknown capability silently became base")
    manifest = source[0] / "pyproject.toml"
    manifest.write_text(manifest.read_text() + '\n[project.optional-dependencies]\nfeature=[]\n')
    selected_source = _selected_identity(source[0])
    with candidate(adopter, selected_source, target, tmp_path, extras=("feature",)) as selected:
        assert selected.record()["extras"] == ["feature"]


@pytest.mark.parametrize("content", [
    b"fixture>=1 --hash=sha256:" + b"a" * 64,
    b"fixture==1.* --hash=sha256:" + b"a" * 64,
    b"fixture==1.0",
    b"--index-url https://example.invalid/simple",
    b"fixture @ https://example.invalid/wheel.whl",
    b"fixture[extra]==1.0 --hash=sha256:" + b"a" * 64,
    b"fixture==1.0 ; sys_platform == 'never' --hash=sha256:" + b"a" * 64,
    b"fixture==1.0 --hash=sha512:" + b"a" * 64,
    b"fixture==1.0 --hash=sha256:" + b"a" * 64 + b"\nFixture==2.0 --hash=sha256:" + b"b" * 64,
])
def test_candidate_output_must_be_exact_applicable_and_hashed(content: bytes) -> None:
    with pytest.raises(ProfileError):
        locks._pins(content, default_environment(), time.monotonic() + 10)


def test_exact_candidate_pins_are_frozen_without_claiming_wheel_admission() -> None:
    content = (
        b"# Exact native compiler output\nfixture==1.0 \\\n --hash=sha256:" + b"a" * 64
        + b"\n\n# The following packages were excluded from the output:\n# fixture-source\n"
    )
    assert locks._pins(content, default_environment(), time.monotonic() + 10) == [
        {"name": "fixture", "version": "1.0", "hashes": ["sha256:" + "a" * 64]},
    ]


def _native_wheel_lock(**package_changes: Any) -> dict[str, Any]:
    package = {
        "name": "fixture", "version": "1.0",
        "wheels": [{"url": "https://example.invalid/files/fixture-1.0-py3-none-any.whl",
                    "hashes": {"sha256": "a" * 64}}],
    } | package_changes
    return package


def _wheel_pins(packages: list[dict[str, Any]], target: Target, **options: Any) -> list[SourceWheelPin]:
    # JSON shapes feed the real TOML parser without inventing a second lock format.
    prefix = 'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=3"\n'
    lines = [prefix]
    for package in packages:
        lines.append("[[packages]]")
        for key, value in package.items():
            if key == "wheels":
                entries = [
                    "{url=" + json.dumps(wheel["url"]) + ", hashes={sha256="
                    + json.dumps(wheel["hashes"]["sha256"]) + "}}"
                    for wheel in value
                ]
                lines.append("wheels=[" + ",".join(entries) + "]")
            else:
                lines.append(key + "=" + json.dumps(value))
    content = "\n".join(lines).encode()
    toml_parser: Callable[[], locks._TomlParser] = dependencies._toml_parser
    assert toml_parser().loads(content.decode())
    return locks._wheel_pins(content, target, time.monotonic() + 10, **options)


def test_native_exact_witness_carries_actual_filename_without_admitting_url(target: Target) -> None:
    assert _wheel_pins([_native_wheel_lock()], target) == [{
        "name": "fixture", "version": "1.0", "filename": "fixture-1.0-py3-none-any.whl",
        "sha256": "sha256:" + "a" * 64,
    }]


def test_native_local_witness_requires_the_exact_held_wheel(target: Target, tmp_path: Path) -> None:
    filename = "fixture-1.0-py3-none-any.whl"
    held = tmp_path / filename
    held.write_bytes(b"held")
    other = tmp_path / "other" / filename
    other.parent.mkdir()
    other.write_bytes(b"held")
    package = _native_wheel_lock()
    package["wheels"][0]["url"] = held.as_uri()
    with pytest.raises(ProfileError):
        _wheel_pins([package], target)
    assert _wheel_pins([package], target, local_wheels={filename: held})[0]["filename"] == filename
    for url in (other.as_uri(), held.as_uri() + "?query=1",
                "https://example.invalid/" + filename):
        package["wheels"][0]["url"] = url
        with pytest.raises(ProfileError):
            _wheel_pins([package], target, local_wheels={filename: held})


def test_native_witness_canonicalizes_duplicate_tag_alternatives_before_parsing(
    target: Target, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import runtime_profile

    filename = "fixture-1.0-" + "-".join(
        ".".join([part] * 128) for part in ("py3", "none", "any")
    ) + ".whl"
    package = _native_wheel_lock()
    package["wheels"][0]["url"] = "https://example.invalid/" + filename
    actual = parse_wheel_filename
    parsed = []

    def bounded_parse(name: str) -> tuple[str, Version, tuple[()] | tuple[int, str], frozenset[Tag]]:
        assert name == "fixture-1.0-py3-none-any.whl"
        parsed.append(name)
        return actual(name)

    monkeypatch.setattr(runtime_profile, "parse_wheel_filename", bounded_parse)
    assert _wheel_pins([package], target)[0]["filename"] == filename
    assert parsed == ["fixture-1.0-py3-none-any.whl"]


@pytest.mark.parametrize("kind", [
    "ambiguous", "source-only", "foreign-name", "foreign-version", "foreign-tag",
    "invalid-hash", "duplicate", "path", "encoded-path", "non-wheel", "marker", "unlocked",
])
def test_native_witness_refuses_ambiguity_or_unsupported_records(target: Target, kind: str) -> None:
    package = _native_wheel_lock()
    packages = [package]
    if kind == "ambiguous":
        package["wheels"] *= 2
    elif kind == "source-only":
        package["wheels"] = []
    elif kind in ("foreign-name", "foreign-version"):
        package["name" if kind == "foreign-name" else "version"] = "foreign" if kind == "foreign-name" else "2"
    elif kind == "foreign-tag":
        package["wheels"][0]["url"] = "https://example.invalid/fixture-1.0-cp399-cp399-never.whl"
    elif kind == "invalid-hash":
        package["wheels"][0]["hashes"]["sha256"] = "x" * 64
    elif kind == "duplicate":
        packages *= 2
    elif kind in ("path", "encoded-path", "non-wheel"):
        suffix = {"path": "fixture\\1.0-py3-none-any.whl",
                  "encoded-path": "fixture%2F1.0-py3-none-any.whl",
                  "non-wheel": "fixture-1.0.tar.gz"}[kind]
        package["wheels"][0]["url"] = "https://example.invalid/" + suffix
    elif kind == "marker":
        package["marker"] = "sys_platform == 'never'"
    elif kind == "unlocked":
        package.pop("version")
    with pytest.raises(ProfileError):
        _wheel_pins(packages, target)


@pytest.mark.parametrize("content", [
    b"", b"\xff", b"lock-version = true", b'lock-version = "1.0"\nlock-version = "1.0"',
    b'lock-version = "1.0"\ncreated-by = "uv"\nrequires-python = ">=399"',
])
def test_native_witness_refuses_malformed_or_incompatible_documents(content: bytes, target: Target) -> None:
    with pytest.raises(ProfileError):
        locks._wheel_pins(content, target, time.monotonic() + 10)


def test_native_source_only_third_party_is_refused_without_backend_execution(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
) -> None:
    calls = []

    class Index(BaseHTTPRequestHandler):
        def log_message(self, format: str, *args: object) -> None:
            pass

        def do_GET(self) -> None:
            calls.append(self.path)
            if self.path.rstrip("/") != "/simple/fixture-registry":
                self.send_error(500, "source archive must not be requested")
                return
            body = json.dumps({
                "meta": {"api-version": "1.2"}, "name": "fixture-registry",
                "files": [{"filename": "fixture_registry-1.0.tar.gz", "url": "/backend.tar.gz",
                           "hashes": {"sha256": "a" * 64}, "upload-time": "2000-01-01T00:00:00Z"}],
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/vnd.pypi.simple.v1+json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    manifest = source[0] / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace("dependencies=[]", 'dependencies=["fixture-registry>=1"]'))
    selected_source = _selected_identity(source[0])
    server = ThreadingHTTPServer(("127.0.0.1", 0), Index)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    environment = offline_environment(tmp_path)
    environment.pop("UV_OFFLINE", None)
    configuration = tmp_path / "uv.toml"
    configuration.write_text(
        f'[[index]]\nurl="http://127.0.0.1:{server.server_port}/simple"\ndefault=true\n',
        encoding="utf-8",
    )
    environment["UV_CONFIG_FILE"] = str(configuration)
    try:
        with pytest.raises(ProfileError, match="resolution failed"):
            with candidate(
                adopter, selected_source, target, tmp_path, env=environment, wheel_witness=True,
            ):
                pytest.fail("source-only dependency reached source consumption")
        assert calls and all(path.rstrip("/") == "/simple/fixture-registry" for path in calls)
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "cancel", "oversize", "source-change"])
def test_failed_export_releases_retained_inputs_without_a_candidate(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, failure: str,
) -> None:
    actual = subprocess.run
    retained = []
    outputs = []

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[Any]:
        if command[1:3] != ["pip", "compile"] or "--show-settings" in command:
            return actual(command, **options)
        retained.append(Path(options["cwd"]))
        output = Path(command[command.index("--output-file") + 1])
        outputs.append(output)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, 1)
        if failure == "cancel":
            raise KeyboardInterrupt
        if failure == "source-change":
            (source[0] / "fixture_backend.py").write_text("changed")
        output.write_bytes(b"x" * 4097 if failure == "oversize" else b"")
        return subprocess.CompletedProcess(command, 1 if failure == "nonzero" else 0)

    monkeypatch.setattr(locks.subprocess, "run", run)
    error = {"timeout": subprocess.TimeoutExpired, "cancel": KeyboardInterrupt}.get(failure, ValueError)
    with pytest.raises(error):
        with candidate(adopter, source, target, tmp_path):
            pytest.fail("failed resolution returned a candidate")
    assert retained and all(not path.exists() for path in retained)
    assert outputs and all(not path.parent.exists() for path in outputs)


def test_lock_byte_budget_is_inclusive(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    actual = subprocess.run
    content = b"fixture==1.0 --hash=sha256:" + b"a" * 64 + b"\n"

    def run(command: list[str], **options: Any) -> subprocess.CompletedProcess[Any]:
        if command[1:3] != ["pip", "compile"] or "--show-settings" in command:
            return actual(command, **options)
        Path(command[command.index("--output-file") + 1]).write_bytes(content)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(locks.subprocess, "run", run)
    with candidate(adopter, source, target, tmp_path, max_lock_bytes=len(content)) as selected:
        assert selected.requirements == content
        assert selected.record()["pins"][0]["name"] == "fixture"
    with pytest.raises(ProfileError, match="byte budget"):
        with candidate(adopter, source, target, tmp_path, max_lock_bytes=len(content) - 1):
            pytest.fail("one byte over the inclusive limit was admitted")


@pytest.mark.parametrize("selection", [
    {"max_lock_bytes": 0}, {"max_lock_bytes": True},
    {"extras": ("feature_one", "feature-one")}, {"extras": ["feature"]},
    {"component": "../foreign"}, {"python": Path("python")}, {"expected_source": None},
])
def test_invalid_source_lock_selection_refuses_before_children(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch, selection: dict[str, Any],
) -> None:
    monkeypatch.setattr(locks.subprocess, "run", lambda *args, **options: pytest.fail("invalid selection ran a child"))
    with pytest.raises(ProfileError):
        with candidate(adopter, source, target, tmp_path, **selection):
            pytest.fail("invalid selection returned a candidate")


def test_source_mode_requires_existing_authority_request(
    adopter: Adopter, source: Source, target: Target, tmp_path: Path,
) -> None:
    requested = replace(plan(adopter), source_trigger=None)
    with pytest.raises(ProfileError, match="authority"):
        with locks.retain_source_lock(
            requested, payload=source[0], expected_source=source[1], target=target,
            component="fixture", uv="uv", python=Path(sys.executable),
            max_lock_bytes=4096, deadline=time.monotonic() + 60,
        ):
            pytest.fail("unrequested source mode reached resolution")
