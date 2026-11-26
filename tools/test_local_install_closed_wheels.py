"""Contained Source preparation controls; no backend, environment or application runs."""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
import hashlib
import json
import os
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
def application(tmp_path: Path, request: pytest.FixtureRequest) -> dict[str, Any]:
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
    development_wheel = getattr(request, "param", "legacy.whl")
    metadata = metadata.replace(b'path="legacy.whl"', f'path="{development_wheel}"'.encode())
    (repo / "app" / "pyproject.toml").write_bytes(metadata)
    development = repo / "app" / development_wheel
    development.parent.mkdir(parents=True, exist_ok=True)
    development.write_bytes(b"development-only retained bytes")
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


@pytest.mark.parametrize("application", ["legacy.whl", "dist/legacy.whl", "build/legacy.whl"], indirect=True)
def test_closed_wheels_preserve_development_source_and_real_application_lock(
    application: dict[str, Any],
) -> None:
    original = application["repository"] / "app"
    expected = (original / "pyproject.toml").read_bytes()
    development_wheel = profiles.dependencies._toml(original / "pyproject.toml")["tool"]["uv"]["sources"]["helper"]["path"]
    prepared = closed.prepare(**application)
    selected = profiles.source_inputs.select_project(prepared, "demo-app")
    assert selected.closed_wheel_source
    assert (selected.payload / "pyproject.toml").read_bytes() == expected
    assert (selected.payload / development_wheel).read_bytes() == (original / development_wheel).read_bytes()
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
        assert (frozen.plugin / development_wheel).read_bytes() == (original / development_wheel).read_bytes()
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
            assert (held.plugin / development_wheel).read_bytes() == (original / development_wheel).read_bytes()
            profiles.engine._revalidate_input_content(held)
    with pytest.raises(profiles.dependencies.DependencyPolicyError, match="release age"):
        profiles.engine._package_roots(selected.payload)
    with pytest.raises(ValueError, match="already exists"):
        closed.prepare(**application)


def test_snapshot_retains_one_inspected_wheel_scope_through_real_lock(
    application: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
) -> None:
    original_inputs = profiles.source_inputs.project_inputs
    original_lock = profiles._lock_application
    entered = 0
    active = 0

    @contextmanager
    def project_inputs(*args: Any, **kwargs: Any) -> Iterator[Any]:
        nonlocal entered, active
        entered += 1
        with original_inputs(*args, **kwargs) as selected:
            active += 1
            try:
                yield selected
            finally:
                active -= 1

    def lock(*args: Any, **kwargs: Any) -> None:
        assert active == 1, "application emission must retain its inspected wheel custody"
        original_lock(*args, **kwargs)

    monkeypatch.setattr(profiles.source_inputs, "project_inputs", project_inputs)
    monkeypatch.setattr(profiles, "_lock_application", lock)
    prepared = closed.prepare(**application)
    assert entered == 1 and active == 0
    selected = profiles.source_inputs.select_project(prepared, "demo-app")
    assert (prepared / selected.project["application"] / "uv.lock").is_file()


@pytest.mark.parametrize("changed", ["profile", "wheel", "source"])
def test_snapshot_retained_scope_refuses_changes_during_real_lock(
    application: dict[str, Any], monkeypatch: pytest.MonkeyPatch, changed: str,
) -> None:
    original_lock = profiles._lock_application

    def lock(directory: Path, *, uv: str, deadline: float) -> None:
        original_lock(directory, uv=uv, deadline=deadline)
        prepared = directory.parent.parent
        if changed == "profile":
            path = prepared / "profiles" / "demo-app.json"
            record = json.loads(path.read_bytes())
            record["approval_source"] += "-changed"
            path.write_text(json.dumps(record))
        else:
            path = (
                prepared / "wheels" / "demo-app" / "helper-1.0.0-py3-none-any.whl"
                if changed == "wheel"
                else prepared / "source" / "app" / "src" / "demo_app" / "__init__.py"
            )
            before = path.stat()
            content = path.read_bytes()
            path.write_bytes(bytes((content[0] ^ 1,)) + content[1:])
            os.utime(path, ns=(before.st_atime_ns, before.st_mtime_ns))

    monkeypatch.setattr(profiles, "_lock_application", lock)
    with pytest.raises((ValueError, RuntimeError), match="changed|differs|identity|digest"):
        closed.prepare(**application)


@pytest.mark.parametrize("isolated_preparation", [False, True])
def test_closed_application_lock_survives_configuration_discovery_changes(
    application: dict[str, Any], monkeypatch: pytest.MonkeyPatch,
    isolated_preparation: bool,
) -> None:
    from source_preparation_process import run

    if isolated_preparation:
        monkeypatch.setenv("UV_NO_CONFIG", "1")
    else:
        monkeypatch.delenv("UV_NO_CONFIG", raising=False)
    prepared = closed.prepare(**application)
    selected = profiles.source_inputs.select_project(prepared, "demo-app")
    emitted = prepared / selected.project["application"]
    lock = (emitted / "uv.lock").read_bytes()
    monkeypatch.delenv("UV_NO_CONFIG", raising=False)
    result = run(
        [application["uv"], "lock", "--check", "--offline", "--python", sys.executable],
        cwd=emitted, env=dict(os.environ), capture_output=True, text=True,
        deadline=application["deadline"],
    )
    assert result.returncode == 0, result.stderr
    assert (emitted / "uv.lock").read_bytes() == lock
    assert not (emitted / ".venv").exists()


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


@pytest.mark.parametrize("selection", ["base", "missing", "foreign"])
def test_closed_wheel_associations_follow_selected_extras(
    application: dict[str, Any], monkeypatch: pytest.MonkeyPatch, selection: str,
) -> None:
    repository = application["repository"]
    metadata = repository / "app/pyproject.toml"
    content = metadata.read_text().replace(
        'legacy=["helper==0.5"]',
        'legacy=["helper==0.5"]\nfeature=["addon>=1"]',
    ) + '\n[tool.copilot-extensions.application-sources]\naddon="addon"\n'
    metadata.write_text(content)
    git = source.preparation._git
    git(repository, "add", ".")
    git(
        repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Optional associated application",
    )
    application["commit"] = git(repository, "rev-parse", "HEAD")
    app = application["archives"]["demo_app-1.0.0-py3-none-any.whl"]
    with zipfile.ZipFile(app) as archive:
        wheel_metadata = archive.read("demo_app-1.0.0.dist-info/METADATA")
        body = archive.read("demo_app/__init__.py")
        entry_points = archive.read("demo_app-1.0.0.dist-info/entry_points.txt")
    app.write_bytes(wheel_bytes(
        name="demo_app", prefix="demo_app-1.0.0.dist-info/",
        metadata=wheel_metadata.replace(
            b"Provides-Extra: legacy\n",
            b'Provides-Extra: legacy\nProvides-Extra: feature\n'
            b'Requires-Dist: addon>=1; extra == "feature"\n',
        ),
        extra={
            "demo_app/__init__.py": body,
            "demo_app-1.0.0.dist-info/entry_points.txt": entry_points,
        },
    ))
    application["digests"][app.name] = "sha256:" + hashlib.sha256(app.read_bytes()).hexdigest()
    if selection == "base":
        prepared = closed.prepare(**application)
        selected = profiles.source_inputs.select_project(prepared, "demo-app")
        assert (selected.payload / "pyproject.toml").read_text() == content
        emitted = prepared / selected.project["application"]
        assert (emitted / "uv.lock").is_file()
        assert "addon" not in profiles.dependencies._toml(emitted / "pyproject.toml")["tool"]["uv"]["sources"]
        return
    application["roots"] = ["demo-app[feature]==1.0.0"]
    if selection == "foreign":
        addon = app.parent / "addon-1.0.0-py3-none-any.whl"
        addon.write_bytes(wheel_bytes(
            name="addon", prefix="addon-1.0.0.dist-info/",
            metadata=b"Metadata-Version: 2.3\nName: addon\nVersion: 1.0.0\n\n",
        ))
        application["archives"][addon.name] = addon
        application["digests"][addon.name] = "sha256:" + hashlib.sha256(addon.read_bytes()).hexdigest()
    monkeypatch.setattr(
        profiles, "_write_snapshot",
        lambda *_a, **_kw: pytest.fail("unassociated selected application reached snapshot emission"),
    )
    message = "incomplete|missing" if selection == "missing" else "associated application source is missing or different"
    with pytest.raises(profiles.ProfileError, match=message):
        closed.prepare(**application)
    assert not (application["output"] / "snapshot").exists()


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


@pytest.mark.parametrize("spelling", ["demo_app", "Demo-App"])
def test_equivalent_distribution_names_keep_original_primary_command(
    application: dict[str, Any], spelling: str,
) -> None:
    repository = application["repository"]
    metadata = repository / "app/pyproject.toml"
    content = metadata.read_text().replace('name="demo-app"', f'name="{spelling}"').replace(
        'demo-app="demo_app:main"', 'aaa-probe="demo_app:main"\ndemo-app="demo_app:main"',
    )
    metadata.write_text(content)
    git = source.preparation._git
    git(repository, "add", ".")
    git(
        repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Equivalent distribution name",
    )
    application["commit"] = git(repository, "rev-parse", "HEAD")
    app = application["archives"]["demo_app-1.0.0-py3-none-any.whl"]
    with zipfile.ZipFile(app) as archive:
        wheel_metadata = archive.read("demo_app-1.0.0.dist-info/METADATA")
        body = archive.read("demo_app/__init__.py")
    app.write_bytes(wheel_bytes(
        name="demo_app", prefix="demo_app-1.0.0.dist-info/", metadata=wheel_metadata,
        extra={
            "demo_app/__init__.py": body,
            "demo_app-1.0.0.dist-info/entry_points.txt":
                b"[console_scripts]\naaa-probe = demo_app:main\ndemo-app = demo_app:main\n",
        },
    ))
    application["digests"][app.name] = "sha256:" + hashlib.sha256(app.read_bytes()).hexdigest()
    prepared = closed.prepare(**application)
    selected = profiles.source_inputs.select_project(prepared, "demo-app")
    assert (selected.payload / "pyproject.toml").read_text() == content
    declaration = json.loads((prepared / selected.project["application"] / "application.json").read_text())
    assert declaration["name"] == "aaa-probe"
    assert (prepared / selected.project["application"] / "uv.lock").is_file()


@pytest.mark.parametrize("sibling", ["unselected", "demo_app_extra"])
def test_original_flat_package_discovery_is_bound_to_the_actual_wheel(
    application: dict[str, Any], sibling: str,
) -> None:
    repository = application["repository"]
    project = repository / "app"
    (project / "src/demo_app").rename(project / "demo_app")
    extra = project / sibling
    extra.mkdir()
    (extra / "__init__.py").write_bytes(b"UNSELECTED = True\n")
    metadata = project / "pyproject.toml"
    content = metadata.read_text().replace('where=["src"]', 'include=["demo_app*"]')
    metadata.write_text(content)
    git = source.preparation._git
    git(repository, "add", "--all")
    git(
        repository, "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
        "commit", "--quiet", "-m", "Original flat package discovery",
    )
    application["commit"] = git(repository, "rev-parse", "HEAD")
    if sibling == "demo_app_extra":
        with pytest.raises(RuntimeError, match="Python member set differs"):
            closed.prepare(**application)
        return
    prepared = closed.prepare(**application)
    selected = profiles.source_inputs.select_project(prepared, "demo-app")
    assert (selected.payload / "pyproject.toml").read_text() == content
    assert (selected.payload / "demo_app/__init__.py").read_bytes() == (project / "demo_app/__init__.py").read_bytes()
    assert (selected.payload / sibling / "__init__.py").read_bytes() == b"UNSELECTED = True\n"
    assert (prepared / selected.project["application"] / "uv.lock").is_file()


@pytest.mark.parametrize("layout", ["runtime-input-provider", "runtime-source-tools"])
def test_packaged_provider_completes_preparation_without_checkout_imports(
    tmp_path: Path, layout: str, request: pytest.FixtureRequest,
) -> None:
    import materialize_launch_wrapper_assets as wrappers

    root = Path(closed.__file__).resolve().parents[1]
    destination = tmp_path / "packaged/worktree-manager"
    destination.mkdir(parents=True)
    report = wrappers.materialize_into(
        source_consumer_dir=root / "worktree-manager", dest_consumer_dir=destination,
        canonical_root=root, manifest_name="packaged-assets.json",
    )
    assert report and all(line.startswith("OK") for line in report), report
    tools = destination / "scripts" / layout / "tools"
    # Packaging is test setup, not part of the preparation invocation's budget.
    application = cast(dict[str, Any], request.getfixturevalue("application"))
    options = dict(application)
    options["repository"] = str(options["repository"])
    options["output"] = str(options["output"])
    options["archives"] = {name: str(path) for name, path in options["archives"].items()}
    code = (
        "import json,sys; from pathlib import Path; "
        "sys.path.insert(0,sys.argv[1]); import local_install_closed_wheels as owner; "
        "assert Path(owner.__file__).parent == Path(sys.argv[1]); "
        "options=json.loads(sys.argv[2]); "
        "options['repository']=Path(options['repository']); options['output']=Path(options['output']); "
        "options['archives']={name:Path(path) for name,path in options['archives'].items()}; "
        "snapshot=owner.prepare(**options); "
        "selected=owner.profiles.source_inputs.select_project(snapshot,'demo-app'); "
        "application=snapshot/selected.project['application']; "
        "assert (application/'uv.lock').is_file(); "
        "assert (application/'runtime/daemon_instance_runtime.pyz').is_file(); "
        "print(snapshot)"
    )
    result = source.preparation_process.run(
        [sys.executable, "-I", "-B", "-c", code, str(tools), json.dumps(options)],
        cwd=tmp_path, capture_output=True, text=True, deadline=application["deadline"],
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert Path(result.stdout.strip()).samefile(application["output"] / "snapshot")
