"""Contained Source preparation controls; no backend, environment or application runs."""
from __future__ import annotations

from collections.abc import Callable
import hashlib
import json
from pathlib import Path
import shutil
import sys
import time
from typing import Any, cast
import zipfile

from packaging.markers import default_environment
from packaging.tags import sys_tags
import pytest

import local_install_closed_wheels as closed
import local_install_profile as profiles
import local_install_source as source
from wheel_input_fixture import wheel_bytes as _wheel_bytes

wheel_bytes = cast(Callable[..., bytes], _wheel_bytes)


@pytest.fixture
def application(tmp_path: Path) -> dict[str, Any]:
    repo = tmp_path / "repository"
    package = repo / "app" / "src" / "demo_app"
    package.mkdir(parents=True)
    body = b"VALUE = 1\n\ndef main():\n    return 0\n"
    (package / "__init__.py").write_bytes(body)
    (repo / "pyproject.toml").write_text(
        '[tool.copilot-extensions.python-projects]\ndemo-app="app"\n',
    )
    metadata = (
        '[project]\nname="demo-app"\nversion="1.0.0"\nrequires-python=">=3.10"\n'
        'dependencies=["helper>=1"]\n'
        '[project.scripts]\ndemo-app="demo_app:main"\n'
        '[project.optional-dependencies]\nlegacy=["helper==0.5"]\n'
        '[build-system]\nrequires=["setuptools>=77"]\n'
        'build-backend="setuptools.build_meta"\n'
        '[tool.setuptools.packages.find]\nwhere=["src"]\n'
        '[tool.uv.sources]\nhelper={path="legacy.whl"}\n'
    ).encode()
    (repo / "app" / "pyproject.toml").write_bytes(metadata)
    (repo / "app" / "legacy.whl").write_bytes(b"development-only retained bytes")
    git = source.preparation._git
    git(repo, "init", "--quiet")
    git(repo, "add", ".")
    git(
        repo, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Application source",
    )
    pool = tmp_path / "wheels"
    pool.mkdir()
    archives = {}
    for name in ("demo_app", "helper"):
        filename = name + "-1.0.0-py3-none-any.whl"
        path = pool / filename
        path.write_bytes(wheel_bytes(
            name=name, prefix=name + "-1.0.0.dist-info/",
            metadata=(
                f"Metadata-Version: 2.3\nName: {name}\nVersion: 1.0.0\n"
                "Requires-Python: >=3.10\n"
                + ("Requires-Dist: helper>=1\nProvides-Extra: legacy\n"
                   'Requires-Dist: helper==0.5; extra == "legacy"\n' if name == "demo_app" else "")
                + "\n"
            ).encode(),
            extra={
                "demo_app/__init__.py": body,
                "demo_app-1.0.0.dist-info/entry_points.txt":
                    b"[console_scripts]\ndemo-app = demo_app:main\n",
            } if name == "demo_app" else {},
        ))
        archives[filename] = path
    uv = shutil.which("uv")
    assert uv is not None, "existing UV is required; this fixture never provisions it"
    return {
        "repository": repo, "commit": git(repo, "rev-parse", "HEAD"),
        "component": "demo-app", "output": tmp_path / "prepared",
        "archives": archives,
        "digests": {name: "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
                    for name, path in archives.items()},
        "target": {
            "environment": default_environment(), "tags": [str(tag) for tag in sys_tags()],
            "device": "cpu",
        },
        "roots": ["demo-app==1.0.0"], "artifact_provenance": {"producer": "synthetic-source-fixture"},
        "policy_id": "fixture-source-admission", "acquisition_id": "fixture-offline-inputs",
        "uv": uv, "deadline": time.monotonic() + 30,
    }


def test_closed_wheels_preserve_development_source_and_real_application_lock(
    application: dict[str, Any],
) -> None:
    original = application["repository"] / "app"
    expected = (original / "pyproject.toml").read_bytes()
    prepared = closed.prepare(**application)
    selected = profiles.source_inputs.select_project(prepared, "demo-app")
    assert selected.closed_wheel_source
    assert (selected.payload / "pyproject.toml").read_bytes() == expected
    assert (selected.payload / "legacy.whl").read_bytes() == (original / "legacy.whl").read_bytes()
    assert (original / "pyproject.toml").read_bytes() == expected
    emitted = prepared / selected.project["application"]
    project = profiles.dependencies._toml(emitted / "pyproject.toml")
    assert project["project"]["dependencies"] == ["demo-app==1.0.0", "helper==1.0.0"]
    lock = (emitted / "uv.lock").read_text()
    assert "helper" in lock and "legacy.whl" not in lock
    with profiles.source_inputs.project_inputs(prepared, "demo-app") as (observed, inputs):
        construction = profiles.source_inputs.application_inputs(observed, inputs)
        assert json.loads((emitted / "application.json").read_text())["construction"] == construction
        assert construction["local_packages"] == ["demo-app"]
        frozen = profiles.engine.freeze_inputs(
            selected.payload, prepared.parent / "independent-frozen",
            closed_wheel_source=True, deadline=application["deadline"],
        )
        assert (frozen.plugin / "pyproject.toml").read_bytes() == expected
        assert frozen.packages == (frozen.plugin,)
        from installer_source_build import retained_input_graph

        source_inputs = profiles.engine.FrozenInputs(
            selected.payload, (selected.payload,), construction["payload_hash"],
            local_packages=("demo-app",), wheel_inputs=inputs, application=emitted,
            closed_wheel_source=True,
        )
        with retained_input_graph(
            source_inputs, Path(application["uv"]), Path(sys.executable),
            refresh=False, on_progress=lambda: None, engine=profiles.engine,
        ) as held:
            assert held.closed_wheel_source and held.plugin != selected.payload
            assert (held.plugin / "pyproject.toml").read_bytes() == expected
            profiles.engine._revalidate_input_content(held)
    with pytest.raises(profiles.dependencies.DependencyPolicyError, match="release age"):
        profiles.engine._package_roots(selected.payload)
    with pytest.raises(ValueError, match="already exists"):
        closed.prepare(**application)


@pytest.mark.parametrize("fault", [
    "digest", "missing", "extra", "metadata", "payload", "target", "environment", "unselected-extra",
])
def test_closed_wheel_refusals_never_build_install_or_invoke(
    application: dict[str, Any], monkeypatch: pytest.MonkeyPatch, fault: str,
) -> None:
    app = application["archives"]["demo_app-1.0.0-py3-none-any.whl"]
    if fault == "digest":
        application["digests"][app.name] = "sha256:" + "0" * 64
    elif fault == "missing":
        del application["archives"]["helper-1.0.0-py3-none-any.whl"]
        del application["digests"]["helper-1.0.0-py3-none-any.whl"]
    elif fault == "extra":
        application["roots"] = ["helper==1.0.0"]
    elif fault in ("metadata", "payload"):
        with zipfile.ZipFile(app) as archive:
            metadata = archive.read("demo_app-1.0.0.dist-info/METADATA")
            body = archive.read("demo_app/__init__.py")
            entry_points = archive.read("demo_app-1.0.0.dist-info/entry_points.txt")
        app.write_bytes(wheel_bytes(
            name="demo_app", prefix="demo_app-1.0.0.dist-info/",
            metadata=metadata.replace(b"helper>=1", b"helper>=2") if fault == "metadata" else metadata,
            extra={
                "demo_app/__init__.py": b"VALUE = 99\n" if fault == "payload" else body,
                "demo_app-1.0.0.dist-info/entry_points.txt": entry_points,
            },
        ))
        application["digests"][app.name] = "sha256:" + hashlib.sha256(app.read_bytes()).hexdigest()
    elif fault == "target":
        application["target"]["tags"] = ["cp310-cp310-impossible_platform"]
    elif fault == "environment":
        application["target"]["environment"]["sys_platform"] = "foreign"
    else:
        application["roots"] = ["demo-app[legacy]==1.0.0"]
    monkeypatch.setattr(
        profiles, "_lock_application",
        lambda *_a, **_kw: pytest.fail("invalid inputs reached application lock execution"),
    )
    messages = {
        "digest": "digest|hash", "missing": "incomplete|missing",
        "extra": "unselected", "metadata": "runtime dependencies",
        "payload": "payload differs", "target": "target tags disagree",
        "environment": "selected native interpreter",
        "unselected-extra": "violates|specifier|version",
    }
    with pytest.raises((ValueError, RuntimeError), match=messages[fault]):
        closed.prepare(**application)
    assert not (application["output"] / "snapshot" / "applications").exists()


def test_closed_source_mode_requires_complete_wheel_construction_before_root_creation(tmp_path: Path) -> None:
    root = tmp_path / "never-created"
    with pytest.raises(ValueError, match="immutable inspected wheels"):
        with profiles.engine.numbered_candidate(
            root, "1.0", plugin=tmp_path, uv=Path(sys.executable),
            base_python=Path(sys.executable), health_module="demo_app",
            closed_wheel_source=True,
        ):
            pytest.fail("closed-wheel source fell through to source building")
    assert not root.exists()


@pytest.mark.parametrize("fault", ["input-mode", "wheel-binding", "source-bytes", "held-association"])
def test_prepared_source_readers_reject_changed_bindings(
    application: dict[str, Any], fault: str,
) -> None:
    prepared = closed.prepare(**application)
    selected = profiles.source_inputs.select_project(prepared, "demo-app")
    manifest = prepared / source.MANIFEST
    record = json.loads(manifest.read_text())
    project = record["projects"]["demo-app"]
    if fault == "input-mode":
        project["input_mode"] = "unknown"
    elif fault == "wheel-binding":
        project["source_artifact"]["wheel"]["sha256"] = "sha256:" + "0" * 64
    elif fault == "source-bytes":
        (selected.payload / "src/demo_app/__init__.py").write_bytes(b"changed")
    else:
        with pytest.raises(ValueError, match="association changed"):
            with profiles.source_inputs.project_inputs(prepared, "demo-app"):
                project["source_artifact"]["provenance"]["producer"] = "changed"
                manifest.write_text(json.dumps(record))
        return
    manifest.write_text(json.dumps(record))
    with pytest.raises(ValueError):
        with profiles.source_inputs.project_inputs(prepared, "demo-app") as (observed, inputs):
            profiles.source_inputs.native_selection(observed, inputs)


def test_preparation_deadline_and_unknown_lock_outcome_do_not_replay(
    application: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    deadline = application["deadline"]
    application["deadline"] = time.monotonic() - 1
    with pytest.raises(ValueError, match="deadline"):
        closed.prepare(**application)
    assert not application["output"].exists()
    application["deadline"] = deadline
    calls = []

    def unknown(application: Path, **_options: Any) -> None:
        calls.append(application)
        (application / "partial-lock-evidence").write_bytes(b"unknown child outcome")
        raise TimeoutError("owned lock operation did not complete")

    monkeypatch.setattr(profiles, "_lock_application", unknown)
    with pytest.raises(TimeoutError, match="did not complete"):
        closed.prepare(**application)
    assert len(calls) == 1
    evidence = calls[0] / "partial-lock-evidence"
    assert evidence.read_bytes() == b"unknown child outcome"
    with pytest.raises(ValueError, match="already exists"):
        closed.prepare(**application)
    assert len(calls) == 1 and evidence.is_file()


def test_source_preparation_never_overwrites_reserved_identity(application: dict[str, Any]) -> None:
    from versioned_runtime_source import SOURCE_FILE

    repository = application["repository"]
    reserved = repository / "app" / SOURCE_FILE
    reserved.write_bytes(b"retained original metadata")
    git = source.preparation._git
    git(repository, "add", ".")
    git(
        repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Reserved deployment metadata",
    )
    application["commit"] = git(repository, "rev-parse", "HEAD")
    with pytest.raises(ValueError, match="reserved deployment metadata"):
        closed.prepare(**application)
    assert reserved.read_bytes() == b"retained original metadata"
    assert (application["output"] / "source/app" / reserved.name).read_bytes() == reserved.read_bytes()


def test_isolated_preparation_provider_uses_its_own_siblings(tmp_path: Path) -> None:
    (tmp_path / "local_install_source.py").write_text(
        'raise AssertionError("caller must not supply preparation code")\n',
    )
    result = source.preparation_process.run(
        [sys.executable, "-I", "-B", str(Path(closed.__file__).resolve())],
        cwd=tmp_path, capture_output=True, text=True, deadline=time.monotonic() + 15,
    )
    assert result.returncode == 0, result.stdout + result.stderr
