"""Real retained-wheel context controls; no image daemon or feed access."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import sys
import time

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "tools"))
from pr_service_image_inputs import prepare_image_inputs
from runtime_profile import ProfileError, SCHEMA, validate_profile
from installer_wheel_inputs import WheelError, WheelLimits
from installer_wheel_snapshot import retain_wheel_inputs
from wheel_input_fixture import wheel_bytes


BASE = "python@sha256:" + "a" * 64
LIMITS = WheelLimits(1 << 20, 1 << 20, 1 << 20, 1 << 16, 100, 100, 1 << 16)
NAMES = ["agent-pull-requests-service", "agent-pull-requests", "fleet-manager", "agent-worktrees"]


def fixture_inputs(tmp_path, *, omit=None, platform="linux"):
    environment = {
        "implementation_name": "cpython", "implementation_version": "3.11.9",
        "os_name": "posix", "platform_machine": "x86_64", "platform_release": "6.0",
        "platform_system": "Linux", "platform_version": "6.0",
        "python_full_version": "3.11.9", "platform_python_implementation": "CPython",
        "python_version": "3.11", "sys_platform": platform,
    }
    archives, wheels = {}, []
    for name in NAMES:
        if name == omit:
            continue
        prefix = name.replace("-", "_")
        filename = prefix + "-1.0-py3-none-any.whl"
        data = wheel_bytes(
            name=prefix, prefix=prefix + "-1.0.dist-info/",
            metadata=f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0\n\n".encode(),
        )
        path = tmp_path / filename
        path.write_bytes(data)
        archives[filename] = path
        wheels.append({
            "name": name, "version": "1.0", "filename": filename,
            "sha256": "sha256:" + hashlib.sha256(data).hexdigest(),
            "requires_python": "", "provides_extras": [], "selected_extras": [],
            "requires_dist": [],
        })
    record = {
        "schema": SCHEMA, "version": 1,
        "release": {"identity": "synthetic-release", "sha256": "sha256:" + "b" * 64},
        "mode": "source", "artifact": None, "component": "pr-service", "backend": "cpu",
        "approval_source": "synthetic-authority", "acquisition_policy": "synthetic-policy",
        "target": {"device": "cpu", "environment": environment, "tags": ["py3-none-any"]},
        "roots": [wheel["name"] + "==1.0" for wheel in wheels], "wheels": wheels,
    }
    return validate_profile(record), archives


@contextmanager
def retained(tmp_path, **changes):
    profile, archives = fixture_inputs(tmp_path, **changes)
    deadline = time.monotonic() + 60
    with retain_wheel_inputs(
        archives, {wheel["filename"]: wheel["sha256"] for wheel in profile.record()["wheels"]},
        max_wheels=10, max_archive_bytes=LIMITS.max_archive_bytes,
        max_total_bytes=LIMITS.max_archive_bytes, max_snapshot_bytes=LIMITS.max_archive_bytes,
        deadline=deadline,
    ) as snapshot:
        yield profile, snapshot, deadline


def prepare(profile, snapshot, deadline, **changes):
    return prepare_image_inputs(
        profile, snapshot, **dict(python_base=BASE, limits=LIMITS, deadline=deadline) | changes,
    )


def test_context_contains_only_recipe_complete_wheels_and_exact_lock(tmp_path):
    secret = tmp_path / "credential.txt"
    secret.write_text("must-not-enter-context")
    with retained(tmp_path) as (profile, snapshot, deadline):
        with prepare(profile, snapshot, deadline) as result:
            context = result.path
            assert result.python_base == BASE
            assert result.lock_id == profile.lock_id
            assert {path.name for path in context.iterdir()} == {"Dockerfile", "requirements.lock", "wheels"}
            assert {path.name for path in (context / "wheels").iterdir()} == set(snapshot.paths)
            for filename, source in snapshot.paths.items():
                assert (context / "wheels" / filename).read_bytes() == source.read_bytes()
            lock = (context / "requirements.lock").read_text().splitlines()
            assert lock == [
                wheel["name"] + "==1.0 --hash=" + wheel["sha256"]
                for wheel in sorted(profile.record()["wheels"], key=lambda wheel: wheel["name"])
            ]
            recipe = (context / "Dockerfile").read_text()
            assert "--no-index" in recipe and "--require-hashes" in recipe
            assert "USER 65532:65532" in recipe
        assert not context.exists()


@pytest.mark.parametrize("base", ["python:3.11", "https://example.test/python", "python@sha256:abc", None])
def test_unpinned_or_invalid_base_refuses(tmp_path, base):
    with retained(tmp_path) as (profile, snapshot, deadline):
        with pytest.raises(ProfileError, match="pinned"):
            with prepare(profile, snapshot, deadline, python_base=base):
                pytest.fail("invalid base admitted")


@pytest.mark.parametrize("name", NAMES)
def test_required_distributions_cannot_be_omitted(tmp_path, name):
    with retained(tmp_path, omit=name) as (profile, snapshot, deadline):
        with pytest.raises(ProfileError, match="required"):
            with prepare(profile, snapshot, deadline):
                pytest.fail("incomplete PR closure admitted")


def test_foreign_platform_refuses(tmp_path):
    with retained(tmp_path, platform="win32") as (profile, snapshot, deadline):
        with pytest.raises(ProfileError, match="Linux"):
            with prepare(profile, snapshot, deadline):
                pytest.fail("foreign target admitted")


@pytest.mark.parametrize("deadline", [float("inf"), float("nan"), 0, True])
def test_no_unbounded_or_expired_deadline(tmp_path, deadline):
    with retained(tmp_path) as (profile, snapshot, _):
        with pytest.raises(ProfileError, match="deadline"):
            with prepare(profile, snapshot, deadline):
                pytest.fail("unbounded operation admitted")


def test_builder_failure_cleans_owned_context_and_preserves_wheel_custody(tmp_path):
    with retained(tmp_path) as (profile, snapshot, deadline):
        with pytest.raises(RuntimeError, match="builder failed"):
            with prepare(profile, snapshot, deadline) as result:
                context = result.path
                raise RuntimeError("builder failed")
        assert not context.exists()
        snapshot.verify()


def test_closed_snapshot_cannot_supply_context(tmp_path):
    with retained(tmp_path) as (profile, snapshot, deadline):
        pass
    with pytest.raises((WheelError, ValueError, OSError)):
        with prepare(profile, snapshot, deadline):
            pytest.fail("released wheel custody admitted")


def test_missing_retained_wheel_refuses_without_reopening_original(tmp_path):
    with retained(tmp_path) as (profile, snapshot, deadline):
        from dataclasses import replace

        missing = replace(snapshot, archives=dict(list(snapshot.archives.items())[1:]))
        with pytest.raises(ProfileError, match="wheel set"):
            with prepare(profile, missing, deadline):
                pytest.fail("missing retained archive admitted")


def test_corrupt_wheel_bytes_refuse_even_with_profile_matching_digest(tmp_path):
    profile, archives = fixture_inputs(tmp_path)
    record = profile.record()
    wheel = record["wheels"][0]
    archives[wheel["filename"]].write_bytes(b"not a wheel")
    wheel["sha256"] = "sha256:" + hashlib.sha256(b"not a wheel").hexdigest()
    profile = validate_profile(record)
    deadline = time.monotonic() + 60
    with retain_wheel_inputs(
        archives, {item["filename"]: item["sha256"] for item in record["wheels"]},
        max_wheels=10, max_archive_bytes=LIMITS.max_archive_bytes,
        max_total_bytes=LIMITS.max_archive_bytes, max_snapshot_bytes=LIMITS.max_archive_bytes,
        deadline=deadline,
    ) as snapshot:
        with pytest.raises(ProfileError):
            with prepare(profile, snapshot, deadline):
                pytest.fail("matching digest substituted for actual wheel inspection")


def test_generated_context_installs_offline_into_fresh_environment(tmp_path):
    from agent_procutil.owned_process import run_caller_owned

    environment = tmp_path / "installed"
    created = run_caller_owned(
        [sys.executable, "-I", "-m", "venv", str(environment)],
        timeout=60, max_stdout_bytes=65536, max_stderr_bytes=65536,
    )
    assert created.returncode == 0, created.stderr
    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    with retained(tmp_path) as (profile, snapshot, deadline):
        with prepare(profile, snapshot, deadline) as result:
            command = [
                str(python), "-I", "-m", "pip", "install", "--no-index",
                "--find-links", str(result.path / "wheels"), "--only-binary=:all:",
                "--require-hashes", "--no-cache-dir", "--disable-pip-version-check",
                "-r", str(result.path / "requirements.lock"),
            ]
            installed = run_caller_owned(
                command, cwd=tmp_path, timeout=30,
                max_stdout_bytes=65536, max_stderr_bytes=65536,
            )
            assert installed.returncode == 0, installed.stderr
            imported = run_caller_owned(
                [str(python), "-I", "-c",
                 "import agent_pull_requests_service,agent_pull_requests,fleet_manager,agent_worktrees"],
                cwd=tmp_path, timeout=10, max_stdout_bytes=65536, max_stderr_bytes=65536,
            )
            assert imported.returncode == 0, imported.stderr
            with (result.path / "wheels" / next(iter(snapshot.paths))).open("ab") as stream:
                stream.write(b"tampered")
            refused = run_caller_owned(
                [*command, "--force-reinstall"], cwd=tmp_path, timeout=30,
                max_stdout_bytes=65536, max_stderr_bytes=65536,
            )
            assert refused.returncode != 0
            assert "HASHES" in refused.stderr.upper()
