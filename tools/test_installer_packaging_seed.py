"""Real native compiler, controlled TLS acquisition and immutable seed health."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

import pytest

import runtime_feed_configuration as configuration
import runtime_source_build as builds
import installer_engine_ref
from runtime_source_lock import retain_source_configuration
from test_installer_closed_wheels import engine as engine  # noqa: PLC0414
from test_installer_wheel_acquisition import CERTIFICATE, feeds as feeds, worker_procutil  # noqa: PLC0414
from test_runtime_feed_configuration import environment
from test_runtime_source_authentication import adopter as adopter, configured_source as configured_source  # noqa: PLC0414
from test_runtime_source_build import packaging_feed, source_with_process_owner
from test_runtime_source_lock import plan, target as target  # noqa: PLC0414

import installer_packaging_seed as seed
from installer_wheel_acquisition import AcquisitionLimits
from installer_wheel_inputs import WheelLimits
from wheel_input_fixture import wheel_bytes


@contextmanager
def selected_seed(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch, *,
    owner_options=(), dropped_option=None,
):
    source_with_process_owner(configured_source, build_only=True)
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    packaging_feed(state)
    env = environment(adopter, state, tmp_path)
    selected = plan(adopter)
    deadline = time.monotonic() + 100
    monkeypatch.setattr(seed, "engine", engine)
    monkeypatch.setattr(
        configuration, "_opaque_index_identity", lambda value: hashlib.sha256(value.encode()).hexdigest(),
    )
    if owner_options:
        native_compile = seed.dependencies.retain_uv_source_compile

        def retained_compile(uv, arguments, **options):
            if "installer-packaging-seed" not in Path(arguments[0]).read_text():
                arguments = [*arguments, *owner_options]
            elif dropped_option is not None:
                arguments = list(arguments)
                position = arguments.index(dropped_option)
                del arguments[position:position + 2]
            return native_compile(uv, arguments, **options)

        monkeypatch.setattr(seed.dependencies, "retain_uv_source_compile", retained_compile)
    with builds.retain_authenticated_source(
        selected, installation=adopter[1], deadline=deadline,
    ) as authenticated, retain_source_configuration(
        selected, payload=authenticated.payload, expected_source=authenticated.identity,
        target=target, component="fixture", uv=shutil.which("uv"), python=Path(sys.executable),
        deadline=deadline, env=env, wheel_witness=True,
        include_build_tools=True, installation=adopter[1],
    ) as captured:
        checks = []
        rejected = set()

        def verify(wheels):
            authenticated.verify()
            captured.verify()
            checks.append(wheels)
            if any(wheel["sha256"] in rejected for wheel in wheels):
                raise ValueError("selected seed input is revoked")
            return selected.policy()

        options = {
            "payload": authenticated.payload, "source": authenticated.identity,
            "policy": selected.policy(), "uv": Path(shutil.which("uv")),
            "python": Path(sys.executable), "verify_owner": verify,
            "opaque_identity": lambda value: hashlib.sha256(value.encode()).hexdigest(),
            "limits": seed.SeedLimits(16_384, 32 << 20, 1 << 20),
            "acquisition_limits": AcquisitionLimits(
                max_packages=1, max_requests=10, max_redirects=3, max_candidates_per_project=10,
                max_index_bytes=16_384, max_total_metadata_bytes=32_768,
                max_wheel_bytes=1 << 20, max_closure_bytes=1 << 20, max_temporary_bytes=4 << 20,
            ),
            "wheel_limits": WheelLimits(1 << 20, 4 << 20, 1 << 20, 64 << 10, 100, 10, 32 << 10),
            "deadline": deadline, "procutil": worker_procutil(),
            "transaction": captured.transaction, "projections": captured.projections,
        }
        yield state, options, checks, rejected


def test_real_seed_cold_health_reuse_and_policy_invalidation(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch,
):
    with selected_seed(adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch) as (
        state, options, checks, _rejected,
    ):
        assert state["requests"] == []
        root = Path(options["policy"]["owner"]["runtimeRoot"]) / ".source-build-tools"
        assert not root.exists()
        for reused in (False, True):
            with seed.retain_packaging_seed(**options) as ready:
                assert ready.reused is reused
                assert ready.local_packages == ()
                assert ready.slot.parent.parent == root
                assert engine.runtime.current_version(root) is None
                receipt = json.loads((ready.slot / ".runtime-input-origins.json").read_text())
                wheels = receipt["wheel_inputs"]
                assert set(wheels["distributions"]) == {"packaging"}
                assert wheels["binding"]["source"] == options["source"].document()
                assert wheels["binding"]["requirement"] == seed.REQUIREMENT
                assert wheels["acquisition"]["policy_id"] == wheels["binding"]["feed_policy"]
                python = ready.slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                result = subprocess.run(
                    [str(python), "-I", "-c",
                     "from packaging.requirements import Requirement;print(Requirement('sample>=1').name)"],
                    executable=seed.dependencies._native_retained_path(python),
                    capture_output=True, text=True, check=True,
                    timeout=options["deadline"] - time.monotonic(),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                )
                assert result.stdout.strip() == "sample"
            generation = ready.slot.name
            completed = engine.runtime.marker_path(root, generation).read_bytes()
        assert any(not item for item in checks) and any(item for item in checks)
        # A caller cannot swap its policy and reuse the completed generation
        # while the real installation owner still selects the previous one.
        options["policy"]["policyId"] += "-new"
        monkeypatch.setattr(seed, "_target", lambda *_a: pytest.fail("unselected interpreter probe"))
        with pytest.raises(ValueError, match="selected policy changed"):
            with seed.retain_packaging_seed(**options):
                pytest.fail("unselected policy reused completed tooling")
        assert engine.runtime.marker_path(root, generation).read_bytes() == completed
        assert engine.runtime.current_version(root) is None


@pytest.mark.parametrize("phase", ["selection", "health", "consumption"])
def test_owner_revocation_is_checked_before_build_and_through_custody(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch, phase,
):
    with selected_seed(adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch) as (
        state, options, _checks, rejected,
    ):
        item = json.loads(state["routes"]["/simple/packaging/"][2])["files"][0]
        digest = "sha256:" + item["hashes"]["sha256"]
        if phase == "selection":
            rejected.add(digest)
            monkeypatch.setattr(engine, "numbered_candidate", lambda *_a, **_k: pytest.fail("revoked build"))
        elif phase == "health":
            original = seed.inspect_seed

            def inspected(*args, **kwargs):
                facts = original(*args, **kwargs)
                rejected.add(digest)
                return facts

            monkeypatch.setattr(seed, "inspect_seed", inspected)
        with pytest.raises(ValueError, match="revoked"):
            with seed.retain_packaging_seed(**options):
                assert phase == "consumption"
                rejected.add(digest)
        root = Path(options["policy"]["owner"]["runtimeRoot"])
        assert not (root / "current-version").exists()


def test_public_native_feed_is_refused_before_resolution_or_any_http(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch,
):
    with selected_seed(adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch) as (
        state, options, _checks, _rejected,
    ):
        options["projections"] = tuple(
            value.replace("127.0.0.1", "pypi.org") for value in options["projections"]
        )
        monkeypatch.setattr(seed, "run_owned", lambda *_a, **_k: pytest.fail("unadmitted resolver"))
        with pytest.raises(ValueError, match="affirmative source configuration"):
            with seed.retain_packaging_seed(**options):
                pytest.fail("public native feed admitted")
        assert state["requests"] == []


@pytest.mark.parametrize("dropped", [None, "--resolution", "--exclude-newer"])
def test_owner_cli_selection_and_frozen_cutoffs_cannot_be_dropped(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch, dropped,
):
    with selected_seed(
        adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch,
        owner_options=("--resolution", "lowest"), dropped_option=dropped,
    ) as (state, options, _checks, _rejected):
        original = seed.run_owned
        commands = []

        def resolved(argv, **settings):
            assert dropped is None, "changed owner settings reached resolution"
            position = argv.index("--resolution")
            assert argv[position + 1] == "lowest"
            commands.append(argv)
            return original(argv, **settings)

        monkeypatch.setattr(seed, "run_owned", resolved)
        if dropped is None:
            with seed.retain_packaging_seed(**options):
                assert len(commands) == 1
        else:
            with pytest.raises(ValueError, match="differ from the held installation owner"):
                with seed.retain_packaging_seed(**options):
                    pytest.fail("changed owner settings were admitted")
            assert not commands and not state["requests"]


@pytest.mark.skipif(os.name == "nt", reason="native POSIX interpreter-link contract")
def test_interpreter_link_is_resolved_and_cannot_retarget_during_custody(
    adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch,
):
    with selected_seed(adopter, configured_source, target, feeds, tmp_path, engine, monkeypatch) as (
        _state, options, _checks, _rejected,
    ):
        link = tmp_path / "selected-python"
        link.symlink_to(options["python"])
        options["python"] = link
        with pytest.raises(ValueError, match="interpreter or selected policy changed"):
            with seed.retain_packaging_seed(**options):
                replacement = tmp_path / "not-an-interpreter"
                replacement.write_bytes(b"never executed")
                link.unlink()
                link.symlink_to(replacement)


def test_first_touch_uses_only_shipped_stdlib_helpers_without_ambient_packaging(tmp_path):
    root = Path(__file__).resolve().parents[1]
    shipped = tmp_path / "standalone"
    shipped.mkdir()
    for name in (*installer_engine_ref.PYTHON_FILES, *installer_engine_ref.SUPPORT_FILES):
        shutil.copy2(root / "libs" / "installer-engine" / name, shipped / name)
    for path in (root / "libs" / "versioned-runtime").glob("versioned_runtime*.py"):
        shutil.copy2(path, shipped / path.name)
    wheel = tmp_path / "packaging-25.0-py3-none-any.whl"
    wheel.write_bytes(wheel_bytes(
        name="packaging", prefix="packaging-25.0.dist-info/",
        metadata=b"Metadata-Version: 2.4\nName: packaging\nVersion: 25.0\n\n",
    ))
    poison = tmp_path / "poison"
    poison.mkdir()
    (poison / "packaging.py").write_text("raise RuntimeError('ambient Packaging executed')\n")
    script = """
import sys
sys.path.insert(0, sys.argv[1])
import installer_packaging_seed as seed
from installer_wheel_inputs import WheelLimits
import time
with open(sys.argv[2], "rb") as archive:
    facts = seed.inspect_seed(
        archive, {"version": "25.0"}, limits=WheelLimits(
            100000, 100000, 20000, 10000, 30, 100, 10000,
        ), deadline=time.monotonic() + 5,
    )
assert facts.name == "packaging"
assert "packaging" not in sys.modules
assert not any(name.startswith(("runtime_profile", "runtime_source_", "build_toolchain")) for name in sys.modules)
print("stdlib-seed-first-touch")
"""
    result = subprocess.run(
        [sys.executable, "-I", "-S", "-B", "-c", script, str(shipped), str(wheel)],
        cwd=poison, env={**os.environ, "PYTHONPATH": str(poison)},
        capture_output=True, text=True, timeout=20,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.strip() == "stdlib-seed-first-touch"
