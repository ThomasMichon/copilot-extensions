"""Pure source-profile derivation controls; native construction has its own packet."""
from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import subprocess
import time
from types import SimpleNamespace

import pytest

import runtime_source_wheels as source
from promotion_unit_tests.test_runtime_profile_wheels import _ENVIRONMENT


@pytest.fixture(autouse=True)
def _synthetic_host_defaults(monkeypatch):
    monkeypatch.setattr("packaging.markers.default_environment", lambda: dict(_ENVIRONMENT))


def test_source_closure_propagates_transitive_extras_and_marker_cycles():
    wheels = {
        "app": {"requires_dist": ["helper[metrics]>=1", "unused; python_version < '2'"]},
        "helper": {"requires_dist": ["leaf; extra == 'metrics'", "app"]},
        "leaf": {"requires_dist": []},
    }
    assert source._extras(wheels, ["app"], dict(_ENVIRONMENT), time.monotonic() + 5) == {
        "app": set(), "helper": {"metrics"}, "leaf": set(),
    }


@pytest.mark.parametrize("requirement", ["missing", "other @ https://example.invalid/package.whl"])
def test_source_closure_never_resolves_missing_or_direct_dependencies(requirement):
    with pytest.raises(source.ProfileError):
        source._extras({"app": {"requires_dist": [requirement]}}, ["app"],
                       dict(_ENVIRONMENT), time.monotonic() + 5)


def test_source_build_selection_keeps_later_extra_expansion_and_excludes_unused_source(tmp_path):
    projects = []
    for name, dependencies, extras in (
        ("app", ["helper", "helper[metrics]"], {}),
        ("helper", [], {"metrics": ["leaf"]}),
        ("leaf", [], {}),
        ("unused", [], {}),
    ):
        directory = tmp_path / name
        directory.mkdir()
        (directory / "pyproject.toml").write_text(
            "[project]\nname=" + json.dumps(name) + '\nversion="1"\ndependencies='
            + json.dumps(dependencies) + "\n[project.optional-dependencies]\n"
            + "".join(key + "=" + json.dumps(value) + "\n" for key, value in extras.items()),
            encoding="utf-8",
        )
        projects.append(directory)
    selected = source._selected_projects(
        tuple(projects), "app", (), dict(_ENVIRONMENT), time.monotonic() + 5,
    )
    assert {path.name for path in selected} == {"app", "helper", "leaf"}


def test_invalid_budget_refuses_before_build_or_source_read(monkeypatch):
    from build_toolchain_lock import ToolchainLock
    from runtime_source_lock import SourceLockCandidate

    monkeypatch.setattr(source, "build_wheel", lambda *args, **kwargs: pytest.fail("no build allowed"))
    with pytest.raises(source.ProfileError, match="positive"):
        with source.retain_source_profile(
            SourceLockCandidate(b"{}", b"", Path("absent")), third_party={},
            uv="unused", toolchain=ToolchainLock(Path("unused"), {}),
            limits=source.WheelLimits(1, 1, 1, 1, 1, 1, 1),
            max_wheels=0, max_total_bytes=1, max_snapshot_bytes=1,
            deadline=time.monotonic() + 5,
        ):
            pytest.fail("invalid source construction admitted")


def test_source_build_deadline_and_exact_uv_cover_all_child_boundaries(tmp_path, monkeypatch):
    import build_python_artifacts as builder
    import build_toolchain_lock as locks

    (tmp_path / "pyproject.toml").write_text("[build-system]\nrequires=[]\n", encoding="utf-8")
    toolchain = locks.ToolchainLock(tmp_path / "python", {"setuptools": "0"})
    calls = []
    uv = str(tmp_path / "selected-uv")

    def run(command, **options):
        calls.append((command, options))
        if command[0] == uv:
            (Path(command[command.index("-o") + 1]) / "demo-1.0-py3-none-any.whl").write_bytes(b"wheel")
            return subprocess.CompletedProcess(command, 0, "", "")
        value = dict(_ENVIRONMENT) if command[-1] == locks._MARKER_ENV_QUERY_SCRIPT else {"ok": True}
        return subprocess.CompletedProcess(command, 0, json.dumps(value), "")

    monkeypatch.setattr(builder.subprocess, "run", run)
    def build_child(command, environment, deadline):
        return run(command, env=environment, timeout=deadline - time.monotonic())
    monkeypatch.setattr(builder, "_build_child", build_child)
    wheel = builder.build_wheel(
        tmp_path, tmp_path / "out", toolchain=toolchain,
        uv=uv, deadline=time.monotonic() + 5,
    )
    assert wheel.read_bytes() == b"wheel"
    assert len(calls) == 3 and calls[-1][0][0] == uv
    assert all(0 < options["timeout"] <= 5 for _, options in calls)
    assert all("creationflags" in options for _, options in calls[:-1])


@pytest.mark.parametrize("fault", ["source-after-health", "interpreter-before-construction"])
def test_source_and_target_fences_refuse_before_exposing_candidate(tmp_path, monkeypatch, fault):
    from runtime_source_lock import SourceLockCandidate

    payload = tmp_path / "source"
    payload.mkdir()
    manifest = payload / "pyproject.toml"
    manifest.write_text('[project]\nname="app"\nversion="1"\n', encoding="utf-8")
    record = {
        "policy": {"owner": {"runtimeRoot": str(tmp_path / "runtime")}},
        "python": str(tmp_path / "python"), "source": {"runtime_version": "1"},
        "target": {"environment": dict(_ENVIRONMENT)},
        "extras": [], "requirements_sha256": "sha256:" + hashlib.sha256(b"").hexdigest(),
        "retained_source_sha256": "sha256:" + source.runtime.fingerprint_source((manifest,)),
    }
    candidate = SourceLockCandidate(json.dumps(record).encode(), b"", payload)

    @contextmanager
    def inputs(*args, **kwargs):
        yield SimpleNamespace(local_packages=("app",), wheels=object())

    @contextmanager
    def constructed(*args, **kwargs):
        assert fault == "source-after-health", "wrong target must refuse before construction"
        manifest.write_text('[project]\nname="changed"\nversion="1"\n', encoding="utf-8")
        kwargs["validate_candidate"](tmp_path / "candidate-slot")
        yield object()

    monkeypatch.setattr(source, "retain_source_profile", inputs)
    monkeypatch.setattr(source, "numbered_candidate", constructed)
    monkeypatch.setattr(source, "_select_uv_executable", lambda _: str(tmp_path / "selected-uv"))
    monkeypatch.setattr(source.shutil, "which", lambda _: str(manifest))
    monkeypatch.setattr(
        source, "_query_marker_environment",
        lambda *_a, **_k: dict(_ENVIRONMENT) if fault == "source-after-health" else {},
    )
    with pytest.raises(source.ProfileError, match="retained source differs|selected target"):
        with source.source_candidate(
            candidate, third_party={}, uv="unused", toolchain=object(),
            health_module="app", limits=source.WheelLimits(1, 1, 1, 1, 1, 1, 1),
            max_wheels=1, max_total_bytes=1, max_snapshot_bytes=1,
            deadline=time.monotonic() + 5,
        ):
            pytest.fail("changed source was exposed to a lifecycle owner")


def test_owned_build_root_is_canonical_before_creating_wheel_paths(tmp_path, monkeypatch):
    from build_toolchain_lock import ToolchainLock
    from runtime_source_lock import SourceLockCandidate

    payload = tmp_path / "source"
    payload.mkdir()
    manifest = payload / "pyproject.toml"
    manifest.write_text('[project]\nname="app"\nversion="1"\n', encoding="utf-8")
    record = {
        "requirements_sha256": "sha256:" + hashlib.sha256(b"").hexdigest(),
        "retained_source_sha256": "sha256:" + source.runtime.fingerprint_source((manifest,)),
        "extras": [], "pins": [], "target": {"environment": dict(_ENVIRONMENT)},
    }
    candidate = SourceLockCandidate(json.dumps(record).encode(), b"", payload)
    canonical = tmp_path / "owned"
    canonical.mkdir()

    class OwnedAlias:
        def resolve(self, *, strict):
            assert strict is True
            return canonical

        def __truediv__(self, name):
            pytest.fail("owned temporary root was not canonicalized")

    @contextmanager
    def temporary(_parent):
        yield str(canonical)

    def build(_project, output, **_options):
        assert output == canonical / "built"
        raise RuntimeError("canonical build path observed")

    monkeypatch.setattr(source, "_temporary_inputs", temporary)
    monkeypatch.setattr(source, "_private_retained_root", lambda _: OwnedAlias())
    monkeypatch.setattr(source, "build_wheel", build)
    toolchain = ToolchainLock(
        tmp_path / "python", {"setuptools": "0", "wheel": "0", "packaging": "0"},
        provenance={"source_policy": "governed", "python": "fixture",
                    "validated_index_identity": "fixture-no-acquisition"},
    )
    with pytest.raises(RuntimeError, match="canonical build path observed"):
        with source.retain_source_profile(
            candidate, third_party={}, uv="unused", toolchain=toolchain,
            limits=source.WheelLimits(1, 1, 1, 1, 1, 1, 1),
            max_wheels=1, max_total_bytes=1, max_snapshot_bytes=1,
            deadline=time.monotonic() + 5,
        ):
            pytest.fail("test builder must stop before construction")


def test_compressed_filename_tags_refuse_before_packaging_expansion(monkeypatch):
    import runtime_profile
    from installer_wheel_inputs import WheelError

    monkeypatch.setattr(
        runtime_profile, "parse_wheel_filename",
        lambda *_args: pytest.fail("unbounded Cartesian expansion was reached"),
    )
    tags = ".".join("t" + str(index) for index in range(8))
    with pytest.raises(WheelError, match="tag expansion"):
        runtime_profile._wheel_filename(
            f"app-1.0-{tags}-{tags}-{tags}.whl", 100, time.monotonic() + 5,
        )


def test_source_digest_refuses_nonordinary_input_before_open(tmp_path, monkeypatch):
    from installer_wheel_inputs import WheelError

    path = tmp_path / "directory-1.0-py3-none-any.whl"
    path.mkdir()
    monkeypatch.setattr(source, "_open", lambda *_args: pytest.fail("nonordinary input opened"))
    with pytest.raises(WheelError, match="ordinary"):
        source._digest(path, 100, time.monotonic() + 5)


@pytest.mark.parametrize("returncode", [0, 2])
def test_deadline_build_uses_existing_installer_supervision(monkeypatch, returncode):
    import os
    import sys
    import build_python_artifacts as builder

    calls = []
    def owned(command, **options):
        assert os.fstat(options["construction_fd"])
        calls.append((command, options))
        if returncode:
            raise subprocess.CalledProcessError(returncode, command, output="built", stderr="failed")
        return subprocess.CompletedProcess(command, 0, "built", "")
    procutil = SimpleNamespace()
    monkeypatch.setitem(sys.modules, "agent_procutil", procutil)
    monkeypatch.setitem(sys.modules, "installer_engine_process", SimpleNamespace(run_owned=owned))
    monkeypatch.setattr(builder.subprocess, "run", lambda *_a, **_k: pytest.fail("unsupervised build"))
    result = builder._build_child(["selected-uv", "build"], {"SAFE": "value"}, time.monotonic() + 5)
    assert result.stdout == "built"
    assert result.returncode == returncode
    assert calls[0][0] == ["selected-uv", "build"]
    assert 0 < calls[0][1]["timeout"] <= 5
    assert calls[0][1]["env"] == {"SAFE": "value"}
    assert calls[0][1]["procutil"] is procutil
    assert calls[0][1]["deadline"] > time.monotonic()


def test_marker_probe_disables_site_initialization(monkeypatch):
    import build_toolchain_lock as locks

    calls = []
    def run(command, **options):
        calls.append((command, options))
        return subprocess.CompletedProcess(command, 0, json.dumps(_ENVIRONMENT), "")
    monkeypatch.setattr(locks.subprocess, "run", run)
    assert locks._query_marker_environment(Path("selected-python"), deadline=time.monotonic() + 5) == _ENVIRONMENT
    assert calls[0][0][1:4] == ["-I", "-S", "-c"]
    assert 0 < calls[0][1]["timeout"] <= 5
