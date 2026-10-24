from __future__ import annotations

from dataclasses import replace
from contextlib import contextmanager
import hashlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import io
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
import zipfile

from packaging.markers import default_environment
import pytest

import runtime_profile_policy as planner
import runtime_source_lock as locks
from runtime_profile import ProfileError
from test_installer_dependency_policy import offline_environment, source_project
from test_runtime_profile_policy_plan import adopter as adopter


def _selected_identity(payload):
    from versioned_runtime_source import SOURCE_FILE, SourceIdentity, assembly_hash, runtime_version

    digest = assembly_hash(payload)
    selected = SourceIdentity(
        "fixture-source", "1.0.0", "a" * 40, "b" * 40, digest,
        runtime_version("1.0.0", "a" * 40, digest),
    )
    (payload / SOURCE_FILE).write_text(json.dumps(selected.document()), encoding="utf-8")
    return payload, selected


@pytest.fixture
def source(tmp_path):
    payload = source_project(tmp_path / "payload")
    manifest = payload / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace('version="1.0"', 'version="1.0.0"'), encoding="utf-8")
    (payload / "plugin.json").write_text(json.dumps({"name": "fixture-source", "version": "1.0.0"}))
    return _selected_identity(payload)


@pytest.fixture
def target():
    return {"environment": default_environment(), "tags": ["py3-none-any"], "device": "cpu"}


def plan(adopter):
    return planner.plan_installation_profile(
        installation=adopter[1], release_index=None, repository="example/plugins",
        signer_digest="", source_digest="", source_ref="", release={}, artifact=None,
        target={}, component="fixture", roots=[], max_subject_bytes=1,
    )


def candidate(adopter, source, target, tmp_path, **changes):
    arguments = dict(
        payload=source[0], expected_source=source[1], target=target, component="fixture",
        uv="uv", python=Path(sys.executable), max_lock_bytes=4096,
        deadline=time.monotonic() + 60, env=offline_environment(tmp_path),
    )
    return locks.retain_source_lock(plan(adopter), **(arguments | changes))


def test_native_source_lock_binds_real_owner_plan_selected_source_and_empty_closure(
    adopter, source, target, tmp_path,
):
    # This source has no third-party requirements; an empty third-party lock is real.
    with candidate(adopter, source, target, tmp_path) as selected:
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
        assert selected.payload != source[0]
        assert (selected.payload / "pyproject.toml").is_file()
        record["source"]["source_commit"] = "c" * 40
        target["device"] = "changed-after-selection"
        assert selected.record()["source"]["source_commit"] == "a" * 40
        assert selected.record()["target"]["device"] == "cpu"
        retained = selected.payload
    assert not retained.exists()


@pytest.mark.parametrize("as_extra", [False, True])
def test_native_source_lock_binds_nonempty_dated_feed_output(adopter, source, target, tmp_path, as_extra):
    filename = "fixture_registry-1.0-py3-none-any.whl"
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as wheel:
        prefix = "fixture_registry-1.0.dist-info/"
        wheel.writestr(prefix + "METADATA", "Metadata-Version: 2.1\nName: fixture-registry\nVersion: 1.0\n")
        wheel.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(prefix + "RECORD", "")
    content = archive.getvalue()
    digest = hashlib.sha256(content).hexdigest()

    class Index(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_GET(self):
            self.respond(head=False)

        def do_HEAD(self):
            self.respond(head=True)

        def respond(self, *, head):
            if self.path.rstrip("/") == "/simple/fixture-registry":
                body = json.dumps({
                    "meta": {"api-version": "1.2"}, "name": "fixture-registry",
                    "files": [{"filename": filename, "url": "/files/" + filename,
                               "hashes": {"sha256": digest}, "upload-time": "2000-01-01T00:00:00Z"}],
                }).encode()
                kind = "application/vnd.pypi.simple.v1+json"
            elif self.path == "/files/" + filename:
                body, kind = content, "application/octet-stream"
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
    if as_extra:
        text += '\n[project.optional-dependencies]\nfeature=["fixture-registry>=1"]\n'
    else:
        text = text.replace("dependencies=[]", 'dependencies=["fixture-registry>=1"]')
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
        with candidate(
            adopter, selected_source, target, tmp_path, env=environment,
            extras=("feature",) if as_extra else (),
        ) as selected:
            assert selected.record()["pins"] == [
                {"name": "fixture-registry", "version": "1.0", "hashes": ["sha256:" + digest]},
            ]
            assert b"fixture-registry==1.0" in selected.requirements
            assert selected.record()["source"] == selected_source[1].document()
            assert selected.record()["extras"] == (["feature"] if as_extra else [])
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


@pytest.mark.parametrize("field,value", [
    ("source_commit", "c" * 40), ("source_tree", "c" * 40),
    ("assembly_hash", "c" * 64), ("runtime_version", "different"),
])
def test_independently_selected_source_cannot_be_replaced(adopter, source, target, tmp_path, field, value):
    with pytest.raises((ProfileError, ValueError), match="source"):
        with candidate(adopter, source, target, tmp_path, expected_source=replace(source[1], **{field: value})):
            pytest.fail("unselected source reached a candidate")


def test_source_integrity_failure_is_not_an_availability_fallback(adopter, source, target, tmp_path):
    (source[0] / "fixture_backend.py").write_text("changed")
    with pytest.raises(ValueError, match="bytes"):
        with candidate(adopter, source, target, tmp_path):
            pytest.fail("corrupt source reached resolution")


def test_transient_source_change_cannot_be_labeled_with_restored_origin(
    adopter, source, target, tmp_path, monkeypatch,
):
    actual = locks.dependencies.retain_uv_source_compile
    backend = source[0] / "fixture_backend.py"
    original = backend.read_bytes()
    snapshots = []

    @contextmanager
    def changed(uv, arguments, **options):
        backend.write_bytes(original + b"\nTRANSIENT = True\n")
        with actual(uv, arguments, **options) as transaction:
            snapshots.append(transaction.sources[source[0]])
            backend.write_bytes(original)
            yield transaction

    monkeypatch.setattr(locks.dependencies, "retain_uv_source_compile", changed)
    with pytest.raises(ProfileError, match="retained source differs"):
        with candidate(adopter, source, target, tmp_path):
            pytest.fail("restored origin mislabeled a changed retained snapshot")
    assert backend.read_bytes() == original
    assert snapshots and all(not path.exists() for path in snapshots)


def test_missing_retained_source_coverage_refuses(adopter, source, target, tmp_path, monkeypatch):
    actual = locks.dependencies.retain_uv_source_compile

    @contextmanager
    def incomplete(uv, arguments, **options):
        with actual(uv, arguments, **options) as transaction:
            transaction.source_hashes.pop(source[0] / "fixture_backend.py")
            yield transaction

    monkeypatch.setattr(locks.dependencies, "retain_uv_source_compile", incomplete)
    with pytest.raises(ProfileError, match="cover"):
        with candidate(adopter, source, target, tmp_path):
            pytest.fail("incomplete retained inventory reached compilation")


@pytest.mark.parametrize("override", ["environment", "configuration", "universal"])
def test_native_platform_override_cannot_hide_an_applicable_dependency(
    adopter, source, target, tmp_path, override,
):
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
    with pytest.raises(locks.dependencies.DependencyPolicyError, match="selected target"):
        with candidate(adopter, selected_source, target, tmp_path, env=environment):
            pytest.fail("cross-platform resolution omitted an applicable dependency")


def test_source_deadline_reaches_settings_probe_during_context_entry(
    adopter, source, target, tmp_path, monkeypatch,
):
    actual = subprocess.run
    timeouts = []

    def run(command, **options):
        if "--show-settings" in command:
            timeouts.append(options["timeout"])
            raise subprocess.TimeoutExpired(command, options["timeout"])
        return actual(command, **options)

    monkeypatch.setattr(locks.subprocess, "run", run)
    with pytest.raises(locks.dependencies.DependencyPolicyError, match="timed out"):
        with candidate(adopter, source, target, tmp_path, deadline=time.monotonic() + 5):
            pytest.fail("expired preflight reached resolution")
    assert len(timeouts) == 1 and 0 < timeouts[0] <= 5


def test_target_must_match_actual_selected_interpreter(adopter, source, target, tmp_path):
    target["environment"]["platform_machine"] = "unavailable-target"
    with pytest.raises(ProfileError, match="interpreter"):
        with candidate(adopter, source, target, tmp_path):
            pytest.fail("wrong target reached resolution")


def test_source_candidate_preserves_declared_extras_and_refuses_unknown_ones(adopter, source, target, tmp_path):
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
def test_candidate_output_must_be_exact_applicable_and_hashed(content):
    with pytest.raises(ProfileError):
        locks._pins(content, default_environment(), time.monotonic() + 10)


def test_exact_candidate_pins_are_frozen_without_claiming_wheel_admission():
    content = b"fixture==1.0 \\\n --hash=sha256:" + b"a" * 64 + b"\n"
    assert locks._pins(content, default_environment(), time.monotonic() + 10) == [
        {"name": "fixture", "version": "1.0", "hashes": ["sha256:" + "a" * 64]},
    ]


@pytest.mark.parametrize("failure", ["nonzero", "timeout", "cancel", "oversize", "source-change"])
def test_failed_export_releases_retained_inputs_without_a_candidate(
    adopter, source, target, tmp_path, monkeypatch, failure,
):
    actual = subprocess.run
    retained = []
    outputs = []

    def run(command, **options):
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


def test_lock_byte_budget_is_inclusive(adopter, source, target, tmp_path, monkeypatch):
    actual = subprocess.run
    content = b"fixture==1.0 --hash=sha256:" + b"a" * 64 + b"\n"

    def run(command, **options):
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
    adopter, source, target, tmp_path, monkeypatch, selection,
):
    monkeypatch.setattr(locks.subprocess, "run", lambda *args, **options: pytest.fail("invalid selection ran a child"))
    with pytest.raises(ProfileError):
        with candidate(adopter, source, target, tmp_path, **selection):
            pytest.fail("invalid selection returned a candidate")


def test_source_mode_requires_existing_authority_request(adopter, source, target, tmp_path):
    requested = replace(plan(adopter), source_trigger=None)
    with pytest.raises(ProfileError, match="authority"):
        with locks.retain_source_lock(
            requested, payload=source[0], expected_source=source[1], target=target,
            component="fixture", uv="uv", python=Path(sys.executable),
            max_lock_bytes=4096, deadline=time.monotonic() + 60,
        ):
            pytest.fail("unrequested source mode reached resolution")
