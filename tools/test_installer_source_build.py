"""Ordinary source candidates reuse locally built wheels across fresh retention."""
from __future__ import annotations

import hashlib
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "versioned-runtime"))

from test_installer_closed_wheels import engine as engine  # noqa: PLC0414
from test_installer_wheel_acquisition import CERTIFICATE, feeds as feeds  # noqa: PLC0414
from wheel_input_fixture import wheel_bytes

import installer_source_build as builds


@pytest.fixture
def ordinary(tmp_path, monkeypatch, feeds, engine):
    source = tmp_path / "source"
    source.mkdir()
    fixture = Path(__file__).with_name("fixtures") / "source-runtime-backend.py"
    backend = fixture.read_text()
    filename = "setuptools-83.0.0-py3-none-any.whl"
    data = wheel_bytes(
        name="setuptools", prefix="setuptools-83.0.0.dist-info/",
        metadata=b"Metadata-Version: 2.4\nName: setuptools\nVersion: 83.0.0\n\n",
        extra={"setuptools/__init__.py": b"", "setuptools/build_meta.py": backend.encode()},
    )
    upload_time, cutoff = "2020-01-01T00:00:00Z", "2021-01-01T00:00:00Z"
    if os.environ.get("TEST_SOURCE_WHEEL_REAL_BACKEND"):
        filename = "setuptools-84.0.0-py3-none-any.whl"
        data = Path(os.environ["TEST_SOURCE_WHEEL_REAL_BACKEND"]).read_bytes()
        assert hashlib.sha256(data).hexdigest() == (
            "51a52592b3b99e102b609654876bd65f19f999935166d1352678931132b0c670"
        )
        upload_time, cutoff = "2026-08-08T18:27:56Z", "2026-10-01T00:00:00Z"
    state = feeds(certificate=CERTIFICATE.with_name("wheel-feed-localhost-leaf.pem"))
    state["routes"]["/simple/setuptools/"] = (
        200, {"Content-Type": "application/vnd.pypi.simple.v1+json"},
        json.dumps({
            "meta": {"api-version": "1.1"}, "name": "setuptools",
            "files": [{
                "filename": filename, "url": "/files/" + filename,
                "hashes": {"sha256": hashlib.sha256(data).hexdigest()},
                "upload-time": upload_time,
            }],
        }).encode(),
    )
    state["routes"]["/files/" + filename] = (200, {}, data)
    config = tmp_path / "uv.toml"
    config.write_text(
        'exclude-newer=' + json.dumps(cutoff) + "\n"
        '[[index]]\nname="fixture"\ndefault=true\nurl='
        + json.dumps(state["origin"] + "/simple") + "\n",
    )
    for key in list(os.environ):
        if key.upper().startswith(("UV_", "PIP_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("UV_CONFIG_FILE", str(config))
    monkeypatch.setenv("UV_CACHE_DIR", str(tmp_path / "uv-cache"))
    monkeypatch.setenv("SSL_CERT_FILE", str(CERTIFICATE))
    monkeypatch.setenv("SOURCE_BUILD_COUNTER", str(tmp_path / "builds"))
    child = engine._child

    def trace(argv, **options):
        if argv[1:2] == ["build"]:
            with (tmp_path / "builds").open("a") as stream:
                stream.write(str(argv[-1]) + "\n")
        return child(argv, **options)

    monkeypatch.setattr(engine, "_child", trace)
    for directory, name, dependencies, code in (
        ("app", "example-app", ["example-leaf", "agent-procutil"], "from example_leaf import VALUE\n"),
        ("leaf", "example-leaf", [], "VALUE=1\n"),
        ("procutil", "agent-procutil", [], None),
    ):
        package = source / directory
        package.mkdir()
        (package / "pyproject.toml").write_text(
            '[build-system]\nrequires=["setuptools>=83"]\nbuild-backend="setuptools.build_meta"\n'
            f'[project]\nname="{name}"\nversion="1.0.0"\ndependencies={json.dumps(dependencies)}\n'
            + ('[tool.uv.sources]\nexample-leaf={path="../leaf"}\nagent-procutil={path="../procutil"}\n'
               if directory == "app" else ""),
        )
        if code is None:
            shutil.copytree(
                Path(__file__).resolve().parents[1] / "libs" / "agent-procutil" / "src",
                package / "src",
            )
        else:
            (package / "src").mkdir()
            (package / "src" / name.replace("-", "_")).mkdir()
            (package / "src" / name.replace("-", "_") / "__init__.py").write_text(code)
    return source / "app", Path(shutil.which("uv")), tmp_path / "builds", state


def _candidate(engine, root, ordinary, version, **options):
    plugin, uv, _counter, _state = ordinary
    return engine.numbered_candidate(
        root, version, plugin=plugin, uv=uv, base_python=Path(sys.executable),
        health_module="example_app", child_timeout=120, **options,
    )


def _value(engine, ready):
    python = engine.runtime.slot_python(ready.slot.parent.parent, ready.slot.name)
    result = subprocess.run(
        [str(python), "-I", "-B", "-c", "import example_app;print(example_app.VALUE)"],
        capture_output=True, text=True, timeout=15, check=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return int(result.stdout.strip())


def test_normal_fresh_candidates_hit_per_project_and_body_edit_rebuilds_only_leaf(
    engine, ordinary, tmp_path, monkeypatch,
):
    root = tmp_path / "installation"
    plugin, _uv, counter, _state = ordinary
    contexts = []
    retain = engine._retained_inputs

    from contextlib import contextmanager

    @contextmanager
    def trace(*args, **kwargs):
        with retain(*args, **kwargs) as inputs:
            contexts.append(inputs.plugin)
            yield inputs

    monkeypatch.setattr(engine, "_retained_inputs", trace)
    compact = builds._compact
    installed_modules = []
    origins = []
    for index in range(2):
        if index:
            monkeypatch.setattr(
                builds, "_compact",
                lambda *_args, **_kwargs: pytest.fail("warm wheel hit copied a source tree"),
            )
        with _candidate(engine, root, ordinary, f"1.0.0+fresh{index}") as ready:
            assert not ready.reused and _value(engine, ready) == 1
            receipt = json.loads((ready.slot / ".runtime-input-origins.json").read_text())
            origins.append(receipt["packages"])
            installed_modules.append(next(ready.slot.rglob("example_leaf/__init__.py")))
            assert set(receipt["source_wheels"]) == {"example-app", "example-leaf", "agent-procutil"}
            assert all("#sha256=" in value for value in receipt["packages"].values())
            assert len(counter.read_text().splitlines()) == 3
    monkeypatch.setattr(builds, "_compact", compact)
    assert origins[0] == origins[1]
    assert os.path.samefile(*installed_modules)
    assert installed_modules[0].stat().st_nlink >= 3
    assert len(set(contexts)) == 2 and all(not path.exists() for path in contexts)
    (plugin.parent / "leaf" / "src" / "example_leaf" / "__init__.py").write_text("VALUE=42\n")
    with _candidate(engine, root, ordinary, "1.0.0+changed") as changed:
        assert _value(engine, changed) == 42
        assert len(counter.read_text().splitlines()) == 4
    cached_module = next(
        path for path in (tmp_path / "uv-cache").rglob("__init__.py")
        if os.path.samefile(path, installed_modules[0])
    )
    expected = installed_modules[0].read_bytes()
    cached_module.unlink()
    assert all(path.read_bytes() == expected for path in installed_modules)
    assert engine.runtime.current_version(root) is None


def test_source_change_between_cache_key_and_build_refuses_publication(
    engine, ordinary, tmp_path, monkeypatch,
):
    compact = builds._compact

    def mutate(package, *args, **options):
        path = next((package / "src").rglob("*.py"))
        path.chmod(path.stat().st_mode | 0o200)
        path.write_bytes(path.read_bytes() + b"\n# changed after source binding\n")
        return compact(package, *args, **options)

    monkeypatch.setattr(builds, "_compact", mutate)
    root = tmp_path / "installation"
    with pytest.raises(ValueError, match="source changed before its wheel build"):
        with _candidate(engine, root, ordinary, "1.0.0"):
            pytest.fail("changed inputs reached a completed candidate")
    assert not ordinary[2].exists()
    assert not engine.runtime.marker_path(root, "1.0.0").exists()
    assert not list((root / ".source-wheel-cache" / "inputs").glob("*/wheel.json"))


@pytest.mark.parametrize("cache_source", ["environment", "configuration"])
def test_offline_builds_keep_native_cache_selection_and_caller_relative_interpreter(
    engine, ordinary, tmp_path, monkeypatch, cache_source,
):
    child = engine._child
    expected_python = Path(sys.executable).resolve()
    selected_cache = tmp_path / "uv-cache"
    if cache_source == "configuration":
        monkeypatch.delenv("UV_CACHE_DIR")
        config = Path(os.environ["UV_CONFIG_FILE"])
        config.write_text('cache-dir=' + json.dumps(str(selected_cache)) + "\n" + config.read_text())
    monkeypatch.chdir(expected_python.parent)
    observed = []

    def trace(argv, **options):
        if argv[1:5] == ["-I", "-m", "venv", "--without-pip"]:
            assert argv[0] == str(expected_python)
        if argv[1:2] == ["build"]:
            assert Path(options["environment"]["UV_CACHE_DIR"]).resolve() == selected_cache.resolve()
            assert options["environment"]["UV_NO_CONFIG"] == "1"
            assert options["environment"]["UV_OFFLINE"] == "1"
            observed.append(argv[-1])
        return child(argv, **options)

    monkeypatch.setattr(engine, "_child", trace)
    with engine.numbered_candidate(
        tmp_path / "installation", "1.0.0", plugin=ordinary[0], uv=ordinary[1],
        base_python=Path(expected_python.name), health_module="example_app", child_timeout=120,
    ) as ready:
        assert _value(engine, ready) == 1
    assert len(observed) == 3


@pytest.mark.parametrize("change", [
    "dynamic", "custom-backend", "setup-script", "setup-extra-code", "build-settings", "script-files",
])
def test_unsupported_contract_explicitly_retains_original_transaction(
    engine, ordinary, tmp_path, monkeypatch, change, capsys,
):
    plugin, uv, counter, _state = ordinary
    for package in engine._package_roots(plugin):
        if change == "setup-script":
            (package / "setup.py").write_text("raise RuntimeError('must not execute')\n")
            continue
        if change == "setup-extra-code":
            (package / "setup.py").write_text(
                "from setuptools import setup\nsetup()\nraise RuntimeError('must not execute')\n",
            )
            continue
        manifest = package / "pyproject.toml"
        content = manifest.read_text()
        if change == "dynamic":
            content = content.replace('[project]\n', '[project]\ndynamic=["description"]\n')
        elif change == "custom-backend":
            content = content.replace('build-backend="setuptools.build_meta"', 'build-backend="another.backend"')
        elif change == "script-files":
            content += '\n[tool.setuptools]\nscript-files=["example-script"]\n'
        else:
            content += '\n[tool.uv.config-settings]\nexample="selected"\n'
        manifest.write_text(content)
    root = tmp_path / "installation"
    packages = engine._package_roots(plugin)
    inputs = engine.FrozenInputs(
        plugin, packages, engine._construction_hash(engine._source_files(packages), ()),
    )
    with engine._retained_inputs(inputs, uv, tmp_path / "absent-python", refresh=False) as retained:
        with builds.retained_source_wheels(
            retained, root, uv, Path(sys.executable),
            run=lambda *_args, **_kwargs: pytest.fail("unsupported contract executed a builder"),
            timeout=30,
        ) as admitted:
            assert admitted is retained
    assert not root.exists() and not counter.exists()
    diagnostic = capsys.readouterr()
    assert diagnostic.out == "" and "Source wheel cache miss:" in diagnostic.err


def test_backend_reported_build_dependencies_retain_the_isolated_source_transaction(
    engine, ordinary, tmp_path, monkeypatch, capsys,
):
    if os.environ.get("TEST_SOURCE_WHEEL_REAL_BACKEND"):
        pytest.skip("Synthetic backend reports the older Setuptools wheel dependency")
    monkeypatch.setenv("TEST_SOURCE_BACKEND_REQUIRES_WHEEL", "1")

    def observe(inputs, *_args, **_kwargs):
        assert inputs.source_wheels is None
        assert str(inputs.plugin / "pyproject.toml") in inputs.transaction.argv
        assert str(inputs.plugin) in inputs.transaction.argv
        raise RuntimeError("isolated-source-fallback-observed")

    monkeypatch.setattr(engine, "_construct", observe)
    root = tmp_path / "installation"
    with pytest.raises(RuntimeError, match="isolated-source-fallback-observed"):
        with _candidate(engine, root, ordinary, "1.0.0"):
            pytest.fail("unsupported backend requirements reached a completed candidate")
    assert "backend-additional-build-requirements" in capsys.readouterr().err
    assert not ordinary[2].exists()
    assert not engine.runtime.marker_path(root, "1.0.0").exists()
    assert not list((root / ".source-wheel-cache" / "inputs").glob("*/wheel.json"))


def test_canonical_setuptools_setup_boilerplate_keeps_the_graph_cacheable(engine, ordinary, tmp_path):
    plugin, _uv, counter, _state = ordinary
    (plugin / "setup.py").write_text("from setuptools import setup\nsetup()\n")
    root = tmp_path / "installation"
    for index in range(2):
        with _candidate(engine, root, ordinary, f"1.0.0+boilerplate{index}") as ready:
            assert _value(engine, ready) == 1
            receipt = json.loads((ready.slot / ".runtime-input-origins.json").read_text())
            assert set(receipt["source_wheels"]) == {"example-app", "example-leaf", "agent-procutil"}
            assert "#sha256=" in receipt["packages"]["example-app"]
            assert "#sha256=" in receipt["packages"]["example-leaf"]
            assert len(counter.read_text().splitlines()) == 3


@pytest.mark.parametrize("change", ["bundled-wheel", "conflicting-build-tools"])
def test_unsupported_source_or_build_graph_preserves_the_held_transaction(
    engine, ordinary, tmp_path, change, capsys,
):
    plugin, uv, counter, _state = ordinary
    manifest = plugin / "pyproject.toml"
    content = manifest.read_text()
    if change == "bundled-wheel":
        content = content.replace('name="example-app"', 'name="copilot-extensions-worktree-manager"')
        content += '\ndbus-next={path="vendor/dbus_next-0.2.3-py3-none-any.whl"}\n'
        wheel = plugin / "vendor" / "dbus_next-0.2.3-py3-none-any.whl"
        wheel.parent.mkdir()
        shutil.copyfile(
            Path(__file__).resolve().parents[1] / "worktree-manager" / "vendor" / wheel.name, wheel,
        )
        reason = "non-project-source-mapping"
    else:
        content = content.replace('requires=["setuptools>=83"]', 'requires=["setuptools==83.0.0"]')
        leaf = plugin.parent / "leaf" / "pyproject.toml"
        leaf.write_text(leaf.read_text().replace(
            'requires=["setuptools>=83"]', 'requires=["setuptools==84.0.0"]',
        ))
        reason = "distinct-build-requirements"
    manifest.write_text(content)
    packages = engine._package_roots(plugin)
    inputs = engine.FrozenInputs(
        plugin, packages, engine._construction_hash(engine._source_files(packages), ()),
    )
    root = tmp_path / "installation"
    with engine._retained_inputs(inputs, uv, tmp_path / "absent-python", refresh=False) as retained:
        arguments = retained.transaction.argv
        if change == "bundled-wheel":
            import tomllib

            mapping = tomllib.loads((retained.plugin / "pyproject.toml").read_text())["tool"]["uv"]["sources"]
            admitted_wheel = retained.plugin / mapping["dbus-next"]["path"]
            assert admitted_wheel.read_bytes() == wheel.read_bytes()
        with builds.retained_source_wheels(
            retained, root, uv, Path(sys.executable),
            run=lambda *_args, **_kwargs: pytest.fail("unsupported graph provisioned combined build tools"),
            timeout=30,
        ) as admitted:
            assert admitted is retained
            assert admitted.transaction.argv == arguments
            assert str(retained.plugin / "pyproject.toml") in arguments
            assert admitted.source_wheels is None
    assert not root.exists() and not counter.exists()
    assert reason in capsys.readouterr().err


@pytest.mark.parametrize("shared_executable", ["uv", "python", "helper"])
def test_shared_executables_keep_the_original_transaction_before_cache_effects(
    engine, ordinary, tmp_path, shared_executable, capsys, monkeypatch,
):
    plugin, uv, counter, _state = ordinary
    executable = tmp_path / "shared-executable"
    executable.write_bytes(b"fixture-executable")
    os.link(executable, tmp_path / "other-executable-link")
    if shared_executable == "helper":
        monkeypatch.setattr(builds, "__file__", str(executable))
    packages = engine._package_roots(plugin)
    inputs = engine.FrozenInputs(
        plugin, packages, engine._construction_hash(engine._source_files(packages), ()),
    )
    root = tmp_path / "installation"
    with engine._retained_inputs(inputs, uv, tmp_path / "absent-python", refresh=False) as retained:
        if shared_executable == "uv":
            retained = replace(retained, transaction=retained.transaction._replace(
                argv=(str(executable), *retained.transaction.argv[1:]),
            ))
        with builds.retained_source_wheels(
            retained, root, uv,
            executable if shared_executable == "python" else Path(sys.executable),
            run=lambda *_args, **_kwargs: pytest.fail("shared executable entered cache construction"),
            timeout=30,
        ) as admitted:
            assert admitted is retained
    assert not root.exists() and not counter.exists()
    assert "shared-build-identity" in capsys.readouterr().err


def test_changed_retained_archive_refuses_before_complete_stamp(engine, ordinary, tmp_path, monkeypatch):
    from contextlib import contextmanager

    retain = engine.retained_source_wheels

    @contextmanager
    def corrupt(*args, **options):
        with retain(*args, **options) as inputs:
            archive = inputs.source_wheels.archives[0]
            archive.write_bytes(b"corrupt")
            yield inputs

    monkeypatch.setattr(engine, "retained_source_wheels", corrupt)
    root = tmp_path / "installation"
    with pytest.raises(ValueError, match="source wheel changed"):
        with _candidate(engine, root, ordinary, "1.0.0"):
            pytest.fail("changed wheel was admitted")
    assert not engine.runtime.marker_path(root, "1.0.0").exists()


def test_installed_missing_content_refuses_before_preparation_and_complete_stamp(
    engine, ordinary, tmp_path, monkeypatch,
):
    construct = engine._construct

    def missing(*args, **options):
        python = construct(*args, **options)
        slot = args[1]
        for path in slot.rglob("example_leaf/__init__.py"):
            path.unlink()
        return python

    monkeypatch.setattr(engine, "_construct", missing)
    root = tmp_path / "installation"
    with pytest.raises(subprocess.CalledProcessError):
        with _candidate(
            engine, root, ordinary, "1.0.0",
            construction_callable="example_app:must_not_be_called",
        ):
            pytest.fail("missing installed file reached preparation")
    assert not engine.runtime.marker_path(root, "1.0.0").exists()


@pytest.mark.parametrize("mutation", ["source-body", "source-mode", "tool-body"])
def test_backend_mutation_cannot_publish_cache_or_completion_and_retry_is_clean(
    engine, ordinary, tmp_path, monkeypatch, mutation,
):
    if mutation == "source-mode" and os.name == "nt":
        pytest.skip("actual executable-mode changes require POSIX")
    child = engine._child
    mutated = False

    def mutate(argv, **options):
        nonlocal mutated
        result = child(argv, **options)
        if argv[1:2] == ["build"] and not mutated:
            mutated = True
            if mutation == "tool-body":
                python = Path(argv[argv.index("--python") + 1])
                tooling = python.parent.parent
                path = next(tooling.rglob("setuptools/build_meta.py"))
                path.write_bytes(path.read_bytes() + b"\n# modified installed backend\n")
            else:
                path = next((Path(argv[-1]) / "src").rglob("*.py"))
                if mutation == "source-body":
                    path.write_bytes(path.read_bytes() + b"\nVALUE=999\n")
                else:
                    path.chmod(path.stat().st_mode ^ 0o100)
        return result

    monkeypatch.setattr(engine, "_child", mutate)
    root = tmp_path / "installation"
    with pytest.raises(ValueError, match="changed before wheel cache publication"):
        with _candidate(engine, root, ordinary, "1.0.0"):
            pytest.fail("mutated build inputs reached a completed candidate")
    assert mutated and not engine.runtime.marker_path(root, "1.0.0").exists()
    assert not list((root / ".source-wheel-cache" / "inputs").glob("*/wheel.json"))
    with _candidate(engine, root, ordinary, "1.0.0") as retry:
        assert _value(engine, retry) == 1
    assert len(ordinary[2].read_text().splitlines()) == 4


@pytest.mark.parametrize("project_name", ["example-app", "Example_App"])
@pytest.mark.parametrize("script_files", [False, True])
def test_completed_source_wheel_candidate_reuses_without_reentering_builder(
    engine, ordinary, tmp_path, monkeypatch, project_name, script_files,
):
    plugin = ordinary[0]
    manifest = plugin / "pyproject.toml"
    content = manifest.read_text().replace('name="example-app"', f'name="{project_name}"')
    if script_files:
        content += '\n[tool.setuptools]\nscript-files=["example-script"]\n'
        (plugin / "example-script").write_text("print('example')\n")
    manifest.write_text(content)
    root = tmp_path / "installation"
    with _candidate(engine, root, ordinary, "1.0.0") as built:
        assert not built.reused
        receipt = json.loads((built.slot / ".runtime-input-origins.json").read_text())
        assert ("source_wheels" in receipt) is not script_files
        assert ("example-app" if not script_files else project_name) in receipt["packages"]
    marker = engine.runtime.marker_path(root, "1.0.0").read_bytes()
    monkeypatch.setattr(
        engine, "retained_source_wheels",
        lambda *_args, **_options: pytest.fail("completed generation entered source construction"),
    )
    with _candidate(engine, root, ordinary, "1.0.0") as reused:
        assert reused.reused and _value(engine, reused) == 1
    assert engine.runtime.marker_path(root, "1.0.0").read_bytes() == marker


def test_source_wheel_verification_uses_a_fresh_bounded_io_budget(tmp_path, monkeypatch):
    wheel = tmp_path / "demo_pkg-1.0-py3-none-any.whl"
    wheel.write_bytes(wheel_bytes())
    record = {
        "demo-pkg": {"filename": wheel.name, "sha256": "sha256:" + hashlib.sha256(wheel.read_bytes()).hexdigest()},
    }
    inputs = builds.SourceWheelInputs(builds._canonical(record), (wheel,), 1)
    for now in (1000, 2000):
        monkeypatch.setattr(builds.time, "monotonic", lambda: now)
        inputs.verify()
