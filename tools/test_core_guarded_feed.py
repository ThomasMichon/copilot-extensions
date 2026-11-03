"""Fresh core selection must not give the resolver direct feed access."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import threading
import time
import zipfile
from pathlib import Path
from types import SimpleNamespace

import pytest
from runtime_profile import ProfileError
from test_core_native_delivery import (
    _configure_native_tls,
    _fixture_git,
    _offline_target,
    local_tls_feed,  # noqa: F401
    tls_source,  # noqa: F401
)
from test_installer_wheel_acquisition import CERTIFICATE, FILENAME, listed, project
from test_promotion_python_artifacts import _wheel_bytes


def _resolve(tmp_path, producer, wheels, manifest, *, uv, download_origins=(), name="guarded-delivery.zip"):
    deadline = time.monotonic() + 180
    destination = tmp_path / name
    result = producer._build_from_snapshot(
        tmp_path, {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40},
        json.dumps(manifest).encode(), wheels, _offline_target(producer, deadline),
        uv=uv, download_origins=download_origins, materials={}, material_paths={}, destination=destination,
        deadline=deadline, max_bytes=4 << 20, guarded_feed=True,
    )
    return result, destination


def _newer_fixture(state, wheel, digest, *, metadata, **changes):
    newer = _wheel_bytes("fixture", "2.0", members={"fixture-2.0.dist-info/METADATA": metadata})
    filename = "fixture-2.0-py3-none-any.whl"
    newer_digest = hashlib.sha256(newer).hexdigest()
    project(state, [
        listed(hashes={"sha256": digest}, size=len(wheel)),
        listed(filename=filename, url="/files/" + filename,
               hashes={"sha256": newer_digest}, size=len(newer), **changes),
    ])
    state["routes"]["/files/" + FILENAME] = (200, {}, wheel)
    state["routes"]["/files/" + filename] = (200, {}, newer)
    return filename, newer_digest


def test_guarded_feed_selects_the_latest_native_candidate(tmp_path, monkeypatch, tls_source):  # noqa: F811
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, wheel, digest = tls_source
    newer_name, newer_digest = _newer_fixture(
        state, wheel, digest, metadata=(
            b"Metadata-Version: 2.4\nName: fixture\nVersion: 2.0\nRequires-Python: >=3.9\n"
            b"Requires-Dist: dep>=1\n"
        ),
    )
    dependency = _wheel_bytes(
        "dep", "1.0", members={"dep-1.0.dist-info/METADATA": (
            b"Metadata-Version: 2.4\nName: dep\nVersion: 1.0\nRequires-Python: >=3.9\n"
        )},
    )
    dependency_name = "dep-1.0-py3-none-any.whl"
    dependency_digest = hashlib.sha256(dependency).hexdigest()
    index = json.dumps({
        "meta": {"api-version": "1.1"}, "name": "dep",
        "files": [listed(filename=dependency_name, url="/files/" + dependency_name,
                         hashes={"sha256": dependency_digest}, size=len(dependency))],
    }).encode()
    state["routes"]["/simple/dep/"] = (200, {"Content-Type": "application/vnd.pypi.simple.v1+json"}, index)
    state["routes"]["/files/" + dependency_name] = (200, {}, dependency)
    _configure_native_tls(tmp_path, monkeypatch, state)

    result, destination = _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    assert result["selection"] == "guarded-native-feed"
    assert result["approval"] == "unattested"
    with zipfile.ZipFile(destination) as delivery:
        profile = json.loads(delivery.read("profile.json"))
        assert b"127.0.0.1" not in delivery.read("profile.json")
        assert b"127.0.0.1" not in delivery.read("delivery.json")
    selected = next(row for row in profile["wheels"] if row["name"] == "fixture")
    assert (selected["version"], selected["sha256"]) == ("2.0", "sha256:" + newer_digest)
    assert next(row for row in profile["wheels"] if row["name"] == "dep")["sha256"] == "sha256:" + dependency_digest
    paths = [path for path, _headers in state["requests"]]
    assert "/simple/fixture/" in paths and "/files/" + newer_name in paths
    assert "/simple/dep/" in paths and "/files/" + dependency_name in paths


def test_guarded_feed_does_not_bind_the_ephemeral_loopback_endpoint(
    tmp_path, monkeypatch, tls_source,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    _configure_native_tls(tmp_path, monkeypatch, state)

    first, first_path = _resolve(tmp_path, producer, wheels, manifest, uv=uv, name="first.zip")
    second, second_path = _resolve(tmp_path, producer, wheels, manifest, uv=uv, name="second.zip")

    assert first["profile_sha256"] == second["profile_sha256"]
    assert first["sha256"] == second["sha256"] and first_path.read_bytes() == second_path.read_bytes()


@pytest.mark.parametrize("reason", ["young", "yanked", "yanked-empty", "withdrawn", "package-cutoff"])
def test_guarded_feed_preserves_native_candidate_admission(
    tmp_path, monkeypatch, tls_source, reason,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, wheel, digest = tls_source
    changes = {
        "young": {"upload-time": "2999-01-01T00:00:00Z"},
        "yanked": {"yanked": True},
        "yanked-empty": {"yanked": ""},
        "withdrawn": {"withdrawn": True},
        "package-cutoff": {"upload-time": "2025-01-01T00:00:00Z"},
    }[reason]
    newer_name, _ = _newer_fixture(
        state, wheel, digest, metadata=(
            b"Metadata-Version: 2.4\nName: fixture\nVersion: 2.0\nRequires-Python: >=3.9\n"
        ), **changes,
    )
    _configure_native_tls(tmp_path, monkeypatch, state)
    if reason == "package-cutoff":
        config = tmp_path / "operator" / "uv.toml"
        config.write_text(
            'exclude-newer-package = { fixture = "2021-01-01T00:00:00Z" }\n' + config.read_text(),
        )

    _result, destination = _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    with zipfile.ZipFile(destination) as delivery:
        profile = json.loads(delivery.read("profile.json"))
    selected = next(row for row in profile["wheels"] if row["name"] == "fixture")
    assert (selected["version"], selected["sha256"]) == ("1.0", "sha256:" + digest)
    assert "/files/" + newer_name not in [path for path, _headers in state["requests"]]


@pytest.mark.parametrize("stage", ["index", "wheel"])
def test_guarded_feed_refuses_an_unapproved_redirect(
    tmp_path, monkeypatch, tls_source, local_tls_feed, stage,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, wheel, _digest = tls_source
    foreign = local_tls_feed(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    foreign["routes"]["/foreign.whl"] = (200, {}, wheel)
    requested = "/simple/fixture/" if stage == "index" else "/files/" + FILENAME
    state["routes"][requested] = (
        302, {"Location": foreign["origin"] + "/foreign.whl"}, b"",
    )
    _configure_native_tls(tmp_path, monkeypatch, state)

    with pytest.raises((ProfileError, ValueError)) as failure:
        _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    assert requested in [path for path, _headers in state["requests"]]
    assert not foreign["requests"]
    assert foreign["origin"] not in str(failure.value)
    assert not (tmp_path / "guarded-delivery.zip").exists()


def test_guarded_feed_keeps_native_credentials_at_the_feed(
    tmp_path, monkeypatch, tls_source,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    _configure_native_tls(tmp_path, monkeypatch, state)
    config = tmp_path / "operator" / "uv.toml"
    config.write_text(config.read_text().replace('authenticate = "never"', 'authenticate = "always"'))
    monkeypatch.setenv("UV_INDEX_FIXTURE_USERNAME", "fixture-user")
    monkeypatch.setenv("UV_INDEX_FIXTURE_PASSWORD", "fixture-password")
    state["authorize"] = lambda headers: bool(headers.get("Authorization"))

    result, destination = _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    assert result["selection"] == "guarded-native-feed"
    assert destination.exists() and state["requests"]
    assert all(headers.get("Authorization") for _path, headers in state["requests"])


def test_guarded_feed_does_not_forward_credentials_to_an_allowed_download_origin(
    tmp_path, monkeypatch, tls_source, local_tls_feed,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, wheel, _digest = tls_source
    foreign = local_tls_feed(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    foreign["routes"]["/foreign.whl"] = (200, {}, wheel)
    state["routes"]["/files/" + FILENAME] = (
        302, {"Location": foreign["origin"] + "/foreign.whl"}, b"",
    )
    _configure_native_tls(tmp_path, monkeypatch, state)
    config = tmp_path / "operator" / "uv.toml"
    config.write_text(config.read_text().replace('authenticate = "never"', 'authenticate = "always"'))
    monkeypatch.setenv("UV_INDEX_FIXTURE_USERNAME", "fixture-user")
    monkeypatch.setenv("UV_INDEX_FIXTURE_PASSWORD", "fixture-password")
    state["authorize"] = lambda headers: bool(headers.get("Authorization"))

    result, destination = _resolve(
        tmp_path, producer, wheels, manifest, uv=uv, download_origins=(foreign["origin"],),
    )

    assert result["selection"] == "guarded-native-feed" and destination.exists()
    assert foreign["requests"]
    assert all("Authorization" not in headers for _path, headers in foreign["requests"])


@pytest.mark.parametrize("source", ["flat-index", "extra-index"])
def test_guarded_feed_rejects_ambient_sources_before_network(
    tmp_path, monkeypatch, tls_source, local_tls_feed, source,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    foreign = local_tls_feed(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    _configure_native_tls(tmp_path, monkeypatch, state)
    if source == "flat-index":
        directory = tmp_path / "foreign-index"
        directory.mkdir()
        monkeypatch.setenv("UV_FIND_LINKS", str(directory))
    else:
        config = tmp_path / "operator" / "uv.toml"
        config.write_text(
            config.read_text() + '\n[[index]]\nname="other"\nurl="'
            + foreign["origin"] + '/simple"\nauthenticate="never"\n',
        )

    with pytest.raises((RuntimeError, ValueError)):
        _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    assert not state["requests"] and not foreign["requests"]
    assert not (tmp_path / "guarded-delivery.zip").exists()


@pytest.mark.parametrize("kind", ["remote", "local"])
def test_guarded_feed_rejects_direct_wheel_dependencies_before_uv(
    tmp_path, monkeypatch, tls_source, local_tls_feed, kind,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    foreign = local_tls_feed(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    foreign["routes"]["/foreign.whl"] = (200, {}, _wheel_bytes("foreign", "1.0"))
    outside = tmp_path / "foreign-1.0-py3-none-any.whl"
    outside.write_bytes(_wheel_bytes("foreign", "1.0"))
    location = foreign["origin"] + "/foreign.whl" if kind == "remote" else outside.as_uri()
    metadata = (
        "Metadata-Version: 2.4\nName: fixture\nVersion: 1.0\nRequires-Python: >=3.9\n"
        + "Requires-Dist: foreign @ " + location + " ; python_version < '0'\n"
    ).encode()
    wheel = _wheel_bytes("fixture", "1.0", members={"fixture-1.0.dist-info/METADATA": metadata})
    digest = hashlib.sha256(wheel).hexdigest()
    project(state, [listed(hashes={"sha256": digest}, size=len(wheel))])
    state["routes"]["/files/" + FILENAME] = (200, {}, wheel)
    _configure_native_tls(tmp_path, monkeypatch, state)

    with pytest.raises((ProfileError, ValueError)) as failure:
        _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    assert "/files/" + FILENAME in [path for path, _headers in state["requests"]]
    assert not foreign["requests"]
    assert location not in str(failure.value)
    assert not (tmp_path / "guarded-delivery.zip").exists()


def test_guarded_feed_never_queries_a_first_party_package(
    tmp_path, monkeypatch, tls_source,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    metadata = (
        b"Metadata-Version: 2.4\nName: fixture\nVersion: 1.0\nRequires-Python: >=3.9\n"
        b"Requires-Dist: agent-sample>=1\n"
    )
    wheel = _wheel_bytes("fixture", "1.0", members={"fixture-1.0.dist-info/METADATA": metadata})
    digest = hashlib.sha256(wheel).hexdigest()
    project(state, [listed(hashes={"sha256": digest}, size=len(wheel))])
    state["routes"]["/files/" + FILENAME] = (200, {}, wheel)
    _configure_native_tls(tmp_path, monkeypatch, state)

    with pytest.raises((ProfileError, ValueError)):
        _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    paths = [path for path, _headers in state["requests"]]
    assert "/files/" + FILENAME in paths
    assert "/simple/agent-sample/" not in paths
    assert not (tmp_path / "guarded-delivery.zip").exists()


def test_guarded_feed_refuses_case_ambiguous_wheel_names(
    tmp_path, monkeypatch, tls_source,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, wheel, digest = tls_source
    project(state, [
        listed(hashes={"sha256": digest}, size=len(wheel)),
        listed(filename=FILENAME.upper().replace(".WHL", ".whl"),
               hashes={"sha256": digest}, size=len(wheel)),
    ])
    _configure_native_tls(tmp_path, monkeypatch, state)

    with pytest.raises((ProfileError, ValueError)):
        _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    assert [path for path, _headers in state["requests"]] == ["/simple/fixture/"]
    assert not (tmp_path / "guarded-delivery.zip").exists()


@pytest.mark.parametrize("stable_calls", [1, 2], ids=["during-resolution", "after-acquisition"])
def test_guarded_feed_refuses_a_changed_index(
    tmp_path, monkeypatch, tls_source, stable_calls,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, wheel, digest = tls_source
    original = project(state, [listed(hashes={"sha256": digest}, size=len(wheel))])
    changed = json.loads(original)
    changed["files"][0]["upload-time"] = "2019-01-01T00:00:00Z"
    changed = json.dumps(changed).encode()
    state["routes"]["/files/" + FILENAME] = (200, {}, wheel)
    calls = []

    def changing(handler):
        calls.append(True)
        body = original if len(calls) <= stable_calls else changed
        handler.send_response(200)
        handler.send_header("Content-Type", "application/vnd.pypi.simple.v1+json")
        handler.send_header("Content-Length", str(len(body)))
        handler.end_headers()
        handler.wfile.write(body)

    state["routes"]["/simple/fixture/"] = changing
    _configure_native_tls(tmp_path, monkeypatch, state)

    with pytest.raises((ProfileError, ValueError)):
        _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    assert len(calls) > stable_calls
    assert not (tmp_path / "guarded-delivery.zip").exists()


def test_guarded_feed_rechecks_index_after_final_source_validation(
    tmp_path, monkeypatch, tls_source,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    status, headers, body = state["routes"]["/simple/fixture/"]
    changed = json.loads(body)
    changed["files"][0]["upload-time"] = "2019-01-01T00:00:00Z"
    original = producer.project_core_inputs
    calls = []

    def mutate(*args, **kwargs):
        result = original(*args, **kwargs)
        calls.append(True)
        if len(calls) == 2:
            state["routes"]["/simple/fixture/"] = (status, headers, json.dumps(changed).encode())
        return result

    monkeypatch.setattr(producer, "project_core_inputs", mutate)
    _configure_native_tls(tmp_path, monkeypatch, state)

    with pytest.raises((ProfileError, ValueError)):
        _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    assert len(calls) == 2
    assert not (tmp_path / "guarded-delivery.zip").exists()


def test_guarded_feed_rechecks_source_after_final_index_validation(
    tmp_path, monkeypatch, tls_source,  # noqa: F811
):
    from promotion_python_artifacts import PromotionArtifactError

    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    source = tmp_path / "plugins" / "agent-sample" / "src" / "agent_sample" / "__init__.py"
    original = producer.verify_guarded_projects

    def mutate(*args, **kwargs):
        original(*args, **kwargs)
        source.write_text("VALUE = 2\n", encoding="utf-8")

    monkeypatch.setattr(producer, "verify_guarded_projects", mutate)
    _configure_native_tls(tmp_path, monkeypatch, state)

    with pytest.raises(PromotionArtifactError, match="payload hash differs"):
        _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    assert not (tmp_path / "guarded-delivery.zip").exists()


def test_guarded_feed_shares_the_request_budget_with_acquisition(
    tmp_path, monkeypatch, tls_source,  # noqa: F811
):
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    original = producer.AcquisitionLimits
    monkeypatch.setattr(
        producer, "AcquisitionLimits",
        lambda **values: original(**(values | {"max_requests": 4})),
    )
    _configure_native_tls(tmp_path, monkeypatch, state)

    with pytest.raises((ProfileError, ValueError)):
        _resolve(tmp_path, producer, wheels, manifest, uv=uv)

    paths = [path for path, _headers in state["requests"]]
    assert len(paths) == 4 and paths[-1] == "/files/" + FILENAME
    assert "/files/" + FILENAME in paths
    assert not (tmp_path / "guarded-delivery.zip").exists()


def test_guarded_compile_inputs_remain_held_until_verification(tmp_path):
    from core_native_feed_resolution import hold_compile_inputs

    config = tmp_path / "uv.toml"
    requirements = tmp_path / "requirements.in"
    constraints = tmp_path / "constraints.in"
    for path in (config, requirements, constraints):
        path.write_text("original\n")
    transaction = SimpleNamespace(
        sources={}, environment={"UV_CONFIG_FILE": str(config)},
        argv=("uv", "pip", "compile", str(requirements), "--constraints", str(constraints)),
    )
    if os.name == "nt":
        with hold_compile_inputs(transaction, time.monotonic() + 30), pytest.raises(OSError):
            requirements.write_text("changed\n")
        assert requirements.read_text() == "original\n"
    else:
        with pytest.raises(ValueError), hold_compile_inputs(transaction, time.monotonic() + 30):
            requirements.write_text("changed\n")


@pytest.mark.parametrize("other", ["native-lock", "offline-pool", "invalid-flag"])
def test_guarded_selection_is_mutually_exclusive_before_source_read(tmp_path, other):
    import core_native_delivery as producer

    options = {
        "native-lock": {"native_lock": tmp_path / "lock", "guarded_feed": True},
        "offline-pool": {"offline_wheel_pool": tmp_path / "pool", "guarded_feed": True},
        "invalid-flag": {"guarded_feed": "true"},
    }[other]
    with pytest.raises(ProfileError, match="exactly one"):
        producer.prepare_core_delivery(
            tmp_path, "a" * 40, "a" * 40, tmp_path / "missing-manifest", tmp_path,
            materials={}, material_paths={}, destination=tmp_path / "delivery.zip", uv="unused",
            download_origins=(), deadline=time.monotonic() + 10, max_bytes=4 << 20, **options,
        )


def test_guarded_feed_actual_cli(tmp_path, monkeypatch, tls_source):  # noqa: F811
    uv, git = shutil.which("uv"), shutil.which("git")
    if uv is None or git is None:
        pytest.skip("native uv and git are required")
    producer, wheels, manifest, state, _wheel, digest = tls_source
    _fixture_git(tmp_path, git, "init", "-q")
    _fixture_git(tmp_path, git, "add", "-A")
    _fixture_git(tmp_path, git, "commit", "-qm", "fixture source")
    commit = _fixture_git(tmp_path, git, "rev-parse", "HEAD")
    manifest_path = tmp_path / "selected-core.json"
    manifest_path.write_bytes(json.dumps(manifest).encode())
    _configure_native_tls(tmp_path, monkeypatch, state)
    home = tmp_path / "isolated-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.setenv("LOCALAPPDATA", str(home / "local"))
    materials = tmp_path / "materials.json"
    materials.write_text("{}", encoding="utf-8")
    destination = tmp_path / "cli-delivery.zip"
    command = [
        sys.executable, "-I", str(Path(producer.__file__)),
        "--source-checkout", str(tmp_path), "--source-commit", commit, "--main-head", commit,
        "--core-manifest", str(manifest_path), "--wheel-dir", str(wheels),
        "--materials", str(materials), "--material-dir", str(tmp_path),
        "--guarded-feed", "--uv", uv, "--output", str(destination),
        "--timeout", "180", "--max-bytes", str(4 << 20),
    ]
    result = subprocess.run(
        command, env=dict(os.environ), capture_output=True, text=True, timeout=200, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["selection"] == "guarded-native-feed"
    with zipfile.ZipFile(destination) as delivery:
        profile = json.loads(delivery.read("profile.json"))
    assert next(row for row in profile["wheels"] if row["name"] == "fixture")["sha256"] == "sha256:" + digest


@pytest.mark.skipif(os.name != "nt", reason="Windows owned Job cleanup")
def test_guarded_feed_timeout_settles_its_worker(tmp_path, monkeypatch, tls_source):  # noqa: F811
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("native uv is required")
    producer, wheels, manifest, state, _wheel, _digest = tls_source
    entered, release = threading.Event(), threading.Event()

    def stall(_handler):
        entered.set()
        release.wait(timeout=25)

    state["routes"]["/simple/fixture/"] = stall
    _configure_native_tls(tmp_path, monkeypatch, state)
    procutil = producer._producer_procutil()
    original = procutil.spawn_sync_in_kill_on_close_job
    processes = []

    def capture(*args, **kwargs):
        process, job = original(*args, **kwargs)
        processes.append(process)
        return process, job

    monkeypatch.setattr(procutil, "spawn_sync_in_kill_on_close_job", capture)
    monkeypatch.setattr(producer, "_producer_procutil", lambda: procutil)
    deadline = time.monotonic() + 12
    try:
        with pytest.raises((ProfileError, ValueError)):
            producer._build_from_snapshot(
                tmp_path, {"source_commit": "a" * 40, "source_tree": "b" * 40, "main_head": "c" * 40},
                json.dumps(manifest).encode(), wheels, _offline_target(producer, deadline),
                uv=uv, download_origins=(), materials={}, material_paths={},
                destination=tmp_path / "guarded-delivery.zip", deadline=deadline,
                max_bytes=4 << 20, guarded_feed=True,
            )
    finally:
        release.set()

    assert entered.is_set() and processes
    assert all(process.poll() is not None for process in processes)
    assert not (tmp_path / "guarded-delivery.zip").exists()
