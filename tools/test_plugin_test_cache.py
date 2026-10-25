"""Dependency-body freshness and selected test-cache rebuild contracts."""
from pathlib import Path
import subprocess

import pytest

from test_run_plugin_tests import runner
from plugin_test_cache import FLEET_TEST_CONSUMERS


def _project(root: Path, name: str, sources: str = "") -> Path:
    root.mkdir(parents=True)
    (root / "pyproject.toml").write_text(
        f'[project]\nname = "{name}"\nversion = "1.0"\n'
        + (f"[tool.uv.sources]\n{sources}\n" if sources else ""),
        encoding="utf-8",
    )
    body = root / "module.py"
    body.write_text("VALUE = 1\n", encoding="utf-8")
    return body


@pytest.fixture
def graph(tmp_path, monkeypatch):
    plugin = tmp_path / "plugins" / "example"
    _project(plugin, "example", 'direct = {path = "../../libs/direct"}')
    direct = _project(
        tmp_path / "libs" / "direct", "direct", 'transitive = {path = "../transitive"}',
    )
    transitive = _project(tmp_path / "libs" / "transitive", "transitive")
    monkeypatch.setattr(runner, "REPO", tmp_path)
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: plugin)
    monkeypatch.setattr(runner, "VENV_ROOT", tmp_path / "venvs")
    return plugin, direct, transitive


@pytest.mark.parametrize("index", [1, 2])
@pytest.mark.parametrize("change", ["edit", "add", "delete"])
def test_same_version_local_dependency_body_invalidates_cache(graph, index, change):
    before = runner._dep_fingerprint("example")
    body = graph[index]
    if change == "edit":
        body.write_text("VALUE = 2\n", encoding="utf-8")
    elif change == "add":
        body.with_name("added.py").write_text("NEW = True\n", encoding="utf-8")
    else:
        body.unlink()
    assert runner._dep_fingerprint("example") != before


def test_generated_and_unrelated_files_do_not_invalidate_cache(graph):
    before = runner._dep_fingerprint("example")
    dependency = graph[1].parent
    for directory in ("__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"):
        target = dependency / directory
        target.mkdir()
        (target / "generated.txt").write_text("ignored", encoding="utf-8")
    (dependency / "module.pyc").write_bytes(b"ignored")
    _project(dependency.parent / "unrelated", "unrelated")
    assert runner._dep_fingerprint("example") == before


def test_missing_transitive_source_refuses_instead_of_reusing_stamp(graph):
    (graph[2].parent / "pyproject.toml").unlink()
    with pytest.raises(FileNotFoundError):
        runner._dep_fingerprint("example")


def test_script_only_target_has_a_stable_runner_only_cache_key(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, "_plugin_dir", lambda _name: tmp_path)
    before = runner._dep_fingerprint("script-only")
    (tmp_path / "script.py").write_text("VALUE = 1\n")
    assert runner._dep_fingerprint("script-only") == before
    monkeypatch.setattr(runner, "RUNNER_DEPENDENCIES", ("pytest-timeout>=2.4,<3",))
    assert runner._dep_fingerprint("script-only") != before


def test_explicit_clean_does_not_prepare_a_busy_environment(graph, monkeypatch, capsys):
    root = runner.VENV_ROOT / "example"
    root.mkdir(parents=True)
    marker = root / "preserved"
    marker.write_text("keep")
    monkeypatch.setattr(runner, "_has_suite", lambda _name: True)
    monkeypatch.setattr(runner.shutil, "which", lambda _name: "uv")

    def busy(*args):
        raise runner.AlreadyRunningError(root / "lock", 123)

    def unexpected(*args, **kwargs):
        pytest.fail("a busy clean must not prepare or remove the environment")

    monkeypatch.setattr(runner, "_acquire_admission", busy)
    monkeypatch.setattr(runner, "_ensure_venv", unexpected)
    assert runner.main(["example", "--reinstall", "--prepare-only"]) == 3
    assert "[BUSY]" in capsys.readouterr().err
    assert marker.read_text() == "keep"


@pytest.mark.parametrize("name", sorted(FLEET_TEST_CONSUMERS))
def test_retained_build_resource_body_invalidates_fleet_consumer(graph, name):
    repo = runner.REPO
    _project(repo / "fleet-manager", "fleet-manager")
    if name == "agent-dispatch":
        _project(repo / "plugins" / "agent-pull-requests", "agent-pull-requests")
    for resource in runner.standalone_build_resources("fleet-manager", source_root=repo):
        resource.mkdir(parents=True)
    body = repo / "libs" / "installer-engine" / "build.py"
    body.write_text("VALUE = 1\n")
    before = runner._dep_fingerprint(name)
    body.write_text("VALUE = 2\n")
    assert runner._dep_fingerprint(name) != before


@pytest.mark.parametrize("linked_parent", [False, True])
def test_linked_cache_root_refuses_without_touching_target(graph, monkeypatch, linked_parent):
    target = runner.VENV_ROOT / "real"
    target.mkdir(parents=True)
    marker = target / "preserved"
    marker.write_text("keep")
    alias = runner.VENV_ROOT / "alias"
    try:
        alias.symlink_to(target, target_is_directory=True)
    except OSError as exc:
        pytest.skip(f"native symlink creation unavailable: {exc}")
    monkeypatch.setattr(runner, "VENV_ROOT", alias if linked_parent else runner.VENV_ROOT)
    with pytest.raises(ValueError, match="not ordinary"):
        runner._ensure_venv("example" if linked_parent else "alias", "unused", reinstall=True)
    assert marker.read_text() == "keep"


def test_automatic_body_drift_rebuild_refreshes_uv_and_preserves_other_cache(
    graph, monkeypatch,
):
    root = runner.VENV_ROOT / "example"
    root.mkdir(parents=True)
    python = root / "fixture-python"
    python.touch()
    (root / ".dep-fingerprint").write_text(runner._dep_fingerprint("example"))
    stale = root / "stale-package"
    stale.mkdir()
    (stale / "old.py").write_text("old")
    other = runner.VENV_ROOT / "unrelated"
    other.mkdir()
    marker = other / "preserved"
    marker.write_text("keep")
    identity = (root.stat().st_dev, root.stat().st_ino)
    graph[2].write_text("VALUE = 2\n", encoding="utf-8")
    monkeypatch.setattr(runner, "_venv_python", lambda _name: python)
    calls = []

    def run(command, **kwargs):
        assert command[:3] == ["uv", "venv", "--allow-existing"]
        assert not stale.exists()
        python.touch()
        return subprocess.CompletedProcess(command, 0)

    def install(uv, arguments, **kwargs):
        calls.append(arguments)
        return subprocess.CompletedProcess([uv, *arguments], 0)

    monkeypatch.setattr(runner.subprocess, "run", run)
    monkeypatch.setattr(runner, "run_uv_install", install)
    assert runner._ensure_venv("example", "uv", reinstall=False) == python
    assert len(calls) == 1
    assert calls[0].count("--reinstall") == 1
    assert (root.stat().st_dev, root.stat().st_ino) == identity
    assert marker.read_text() == "keep"
    assert (root / ".dep-fingerprint").read_text() == runner._dep_fingerprint("example")
