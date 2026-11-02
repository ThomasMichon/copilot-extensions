"""Real configured-source selection and retained native TLS feed custody."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import io
import json
from pathlib import Path
import sys
import time
import zipfile

import pytest

import runtime_feed_configuration as configuration
from runtime_profile import ProfileError
from runtime_source_lock import retain_source_wheel_lock
from test_installer_wheel_acquisition import CERTIFICATE, feeds as feeds, limits, worker_procutil
from test_runtime_feed_configuration import environment
from test_runtime_source_authentication import adopter as adopter, configured_source as configured_source, git, retain
from test_runtime_source_lock import plan, target as target
from installer_wheel_acquisition import acquire_wheel_inputs


def dependency(state):
    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w") as wheel:
        prefix = "fixture_registry-1.0.dist-info/"
        wheel.writestr(prefix + "METADATA", "Metadata-Version: 2.1\nName: fixture-registry\nVersion: 1.0\n")
        wheel.writestr(prefix + "WHEEL", "Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n")
        wheel.writestr(prefix + "RECORD", "")
        wheel.writestr("fixture_registry.py", "VALUE = 1\n")
    content = archive.getvalue()
    filename = "fixture_registry-1.0-py3-none-any.whl"
    metadata = {
        "meta": {"api-version": "1.2"}, "name": "fixture-registry", "files": [{
            "filename": filename, "url": "/files/" + filename,
            "hashes": {"sha256": hashlib.sha256(content).hexdigest()}, "upload-time": "2000-01-01T00:00:00Z",
        }],
    }
    state["routes"]["/simple/fixture-registry/"] = (
        200, {"Content-Type": "application/vnd.pypi.simple.v1+json"}, json.dumps(metadata).encode(),
    )
    state["routes"]["/files/" + filename] = 200, {}, content
    return filename, content


def select_dependency(configured_source):
    from worktree_manager import runtime_source_config

    root, repository, _, _ = configured_source
    manifest = repository / "plugins" / "agent-example" / "pyproject.toml"
    manifest.write_text(manifest.read_text().replace("dependencies=[]", 'dependencies=["fixture-registry>=1"]'))
    git(repository, "add", ".")
    git(repository, "-c", "user.name=Example", "-c", "user.email=example@example.invalid",
        "-c", "commit.gpgsign=false", "commit", "-qm", "Select dependency")
    commit = git(repository, "rev-parse", "HEAD")
    runtime_source_config.set_source(
        runtime_source_config.RuntimeSource("exact", str(repository), commit), root=root,
    )


def test_source_mode_reuses_native_owner_configuration_without_a_release_selection(
    adopter, configured_source, target, feeds, tmp_path, monkeypatch,
):
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    filename, content = dependency(state)
    select_dependency(configured_source)
    monkeypatch.setattr(configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest())
    env = environment(adopter, state, tmp_path)
    deadline = time.monotonic() + 90
    selected_plan = plan(adopter)
    with retain(adopter, deadline=deadline) as authenticated, retain_source_wheel_lock(
        selected_plan, payload=authenticated.payload, expected_source=authenticated.identity,
        target=target, component="fixture", uv="uv", python=Path(sys.executable),
        max_lock_bytes=8192, deadline=deadline, env=env,
        installation=adopter[1],
    ) as locked:
        assert selected_plan.release_selection is None
        assert locked.record()["files"][0]["filename"] == filename
        resolved_requests = len(state["requests"])

        def unexpected(*args, **options):
            pytest.fail("feed capture must reuse the held source compiler, not run another resolver")

        with monkeypatch.context() as capture_only:
            capture_only.setattr(configuration.subprocess, "run", unexpected)
            with configuration.retain_source_feed_inputs(
                selected_plan, locked, installation=adopter[1], deadline=deadline,
            ) as inputs:
                assert inputs.profile is None
                assert len(state["requests"]) == resolved_requests
                assert inputs.feeds[0].index_url == state["origin"] + "/simple"
                capture_only.undo()
                with acquire_wheel_inputs(
                    locked.record()["files"], feeds=inputs.feeds, download_origins=inputs.download_origins,
                    cutoffs=inputs.cutoffs, policy_id=inputs.policy_id, limits=limits(),
                    deadline=deadline, ssl_context=inputs.context, proxies=inputs.proxies,
                    procutil=worker_procutil(),
                ) as acquired:
                    assert acquired.snapshot.archives[filename].read() == content
                    assert acquired.evidence()["policy_id"] == inputs.policy_id
                    assert "artifact" not in locked.record()
                    paths = tuple(acquired.snapshot.paths.values())
        assert len(state["requests"]) == resolved_requests + 2
    assert all(not path.exists() for path in paths)


@pytest.mark.parametrize("changed", ["native_lock", "requirements"])
def test_changed_native_source_witness_refuses_before_feed_capture(
    adopter, configured_source, target, tmp_path, monkeypatch, changed,
):
    from test_installer_dependency_policy import offline_environment

    selected_plan = plan(adopter)
    with retain(adopter) as authenticated, retain_source_wheel_lock(
        selected_plan, payload=authenticated.payload, expected_source=authenticated.identity,
        target=target, component="fixture", uv="uv", python=Path(sys.executable),
        max_lock_bytes=8192, deadline=time.monotonic() + 60, env=offline_environment(tmp_path),
    ) as locked:
        altered = replace(locked, **{changed: b"changed"})
        monkeypatch.setattr(configuration, "_capture", lambda *args, **options: pytest.fail("changed input captured"))
        with pytest.raises(ProfileError, match="retained lock"):
            with configuration.retain_source_feed_inputs(
                selected_plan, altered, installation=adopter[1], deadline=time.monotonic() + 10,
            ):
                pytest.fail("changed witness reached acquisition")
