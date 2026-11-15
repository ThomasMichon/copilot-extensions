"""Real Git containment controls for the source-only composition adapter."""
from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
import json
import os
from pathlib import Path
import shlex
import subprocess
import sys
import threading
import time

import pytest

import local_runtime_promotion as local
import test_promote_release as support

repo = support.repo


@pytest.mark.parametrize("selected", ["example", "demo-plugin"])
def test_main_seeded_application_export_retains_root_declaration_and_project_roster(
    repo, tmp_path, selected,
):
    import local_install_source as installer_source
    from promotion_python_artifacts import candidate_projects

    declaration = '[tool.copilot-extensions.python-projects]\nexample = "apps/example"\n'
    (repo / "pyproject.toml").write_text(declaration)
    for relative, name in (("apps/example", "example"), ("plugins/demo-plugin", "demo-plugin")):
        directory = repo / relative
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "src" / name.replace("-", "_")).mkdir(parents=True)
        (directory / "src" / name.replace("-", "_") / "__init__.py").write_text("VALUE = 1\n")
        (directory / "pyproject.toml").write_text(
            '[project]\n' + f'name = "{name}"\nversion = "0.1.0-dev1"\n',
        )
    main = support._commit(repo, "Declared and builtin application source")
    (repo / "README.md").write_text("new development content\n")
    source = support._commit(repo, "Development selection")
    output = tmp_path / "prepared"
    record = installer_source.compose(
        repo, source, main, output, projects=(selected,), deadline=time.monotonic() + 120,
    )
    assert record["composition"]["main_seed"] == main
    assert (output / "source/pyproject.toml").read_text() == declaration
    assert candidate_projects(output / "source") == {
        "demo-plugin": "0.1.0-dev1", "example": "0.1.0-dev1",
    }
    assert (output / "source" / ("apps/example" if selected == "example" else "plugins/demo-plugin")
            / "src" / selected.replace("-", "_") / "__init__.py").read_text() == "VALUE = 1\n"


def test_concurrent_compositions_keep_changefile_readers_in_their_own_scratch(
    repo: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import changefile

    main = support._git(["rev-parse", "main"], repo)
    support._write_changefile(repo, "pending.json", "demo-plugin", "patch")
    source = support._commit(repo, "pending source change")
    original_changefile = changefile
    load = local.promotion._load_module
    lock = threading.Lock()
    events = [threading.Event() for _ in range(3)]
    arrivals = []

    def interleaved_load(path, name):
        if path.name != "accumulate_bumps.py":
            return load(path, name)
        with lock:
            order = len(arrivals)
            arrivals.append(path)
        if order == 0:
            events[0].set()
            assert events[1].wait(10)
            module = load(path, name)
            events[2].set()
            assert events[0].wait(10)
            return module
        assert events[0].wait(10)
        events[0].clear()
        events[1].set()
        assert events[2].wait(10)
        module = load(path, name)
        events[0].set()
        return module

    monkeypatch.setattr(local.promotion, "_load_module", interleaved_load)
    worktrees = support._git(["worktree", "list", "--porcelain"], repo)
    with ThreadPoolExecutor(max_workers=2) as workers:
        futures = [
            workers.submit(
                local.compose, repo, source_commit=source, main_seed=main,
                deadline=time.monotonic() + 60,
            ) for _ in range(2)
        ]
        results = [future.result(timeout=65) for future in futures]

    assert len(arrivals) == 2 and results[0].tree == results[1].tree
    assert ".changefiles/pending.json" not in support._git(
        ["ls-tree", "-r", "--name-only", results[0].tree], repo,
    )
    assert (repo / ".changefiles/pending.json").is_file()
    assert sys.modules.get("changefile") is original_changefile
    assert support._git(["worktree", "list", "--porcelain"], repo) == worktrees


@pytest.mark.parametrize("hooks_path", ["relative", "absolute"])
def test_composition_cannot_execute_selected_checkout_hooks(repo, tmp_path, hooks_path):
    main = support._git(["rev-parse", "main"], repo)
    marker = tmp_path / "selected-hook-executed"
    hook = repo / "tools/hooks/post-checkout"
    hook.parent.mkdir()
    hook.write_text(
        "#!/bin/sh\nprintf 'selected-hook\\n' >> " + shlex.quote(marker.as_posix()) + "\n",
        encoding="utf-8",
    )
    hook.chmod(0o755)
    support._git(["add", "tools/hooks/post-checkout"], repo)
    support._git(["update-index", "--chmod=+x", "tools/hooks/post-checkout"], repo)
    support._git(["commit", "-m", "selected checkout hook fixture"], repo)
    source = support._git(["rev-parse", "HEAD"], repo)
    support._git([
        "config", "core.hooksPath",
        "tools/hooks" if hooks_path == "relative" else str(hook.parent),
    ], repo)
    canary = tmp_path / "unprotected-canary"
    support._git(["worktree", "add", "--detach", str(canary), source], repo)
    try:
        assert marker.read_bytes() == b"selected-hook\n"
    finally:
        support._git(["worktree", "remove", str(canary)], repo)
    configuration = (repo / ".git/config").read_bytes()
    worktrees = support._git(["worktree", "list", "--porcelain"], repo)

    composed = local.compose(
        repo, source_commit=source, main_seed=main, deadline=time.monotonic() + 60,
    )

    assert composed.source_commit == source
    assert marker.read_bytes() == b"selected-hook\n"
    assert (repo / ".git/config").read_bytes() == configuration
    assert support._git(["worktree", "list", "--porcelain"], repo) == worktrees


@pytest.mark.parametrize("monitor_path", ["relative", "absolute"])
def test_composition_cannot_execute_repository_filesystem_monitor(repo, tmp_path, monitor_path):
    main = support._git(["rev-parse", "main"], repo)
    marker = tmp_path / "filesystem-monitor-executed"
    monitor = repo / "tools/fixture-monitor"
    monitor.write_text(
        "#!/bin/sh\nprintf 'monitor\\n' >> " + shlex.quote(marker.as_posix())
        + "\nprintf 'fixture-token\\000'\n",
        encoding="utf-8",
    )
    monitor.chmod(0o755)
    support._git(["add", "tools/fixture-monitor"], repo)
    support._git(["update-index", "--chmod=+x", "tools/fixture-monitor"], repo)
    support._git(["commit", "-m", "selected filesystem monitor fixture"], repo)
    source = support._git(["rev-parse", "HEAD"], repo)
    support._git([
        "config", "core.fsmonitor",
        "tools/fixture-monitor" if monitor_path == "relative" else shlex.quote(monitor.as_posix()),
    ], repo)
    support._git(["config", "core.fsmonitorHookVersion", "2"], repo)
    probe = subprocess.run(
        ["git", "status", "--porcelain"], cwd=repo, capture_output=True, text=True,
        env=local.promotion.git_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert probe.returncode == 0 and marker.is_file(), probe.stderr
    executed = marker.read_bytes()
    assert executed
    configuration = (repo / ".git/config").read_bytes()

    composed = local.compose(
        repo, source_commit=source, main_seed=main, deadline=time.monotonic() + 60,
    )

    assert composed.source_commit == source
    assert marker.read_bytes() == executed
    assert (repo / ".git/config").read_bytes() == configuration


@pytest.mark.skipif(os.name == "nt", reason="requires Git's native symlink checkout")
@pytest.mark.parametrize("target", [
    "manifest", "plugin-parent", "standalone", "fallback", "template", "marketplace",
    "changefile-directory", "changefile-file",
])
def test_linked_composition_inputs_cannot_write_or_remove_external_files(
    repo: Path, tmp_path: Path, target: str,
) -> None:
    support._write_plugin(repo, "demo-plugin", "0.9.0-dev8")
    main = support._commit(repo, "shipped plugin")
    support._git(["checkout", "-q", "dev"], repo)
    external = tmp_path / "external"
    external.mkdir()
    sentinel = external / "keep.json"
    sentinel.write_text('{"version":"0.1.0-dev1","changes":[]}\n', encoding="utf-8")
    if target == "manifest":
        link, destination = repo / "plugins/demo-plugin/plugin.json", sentinel
        link.unlink()
    elif target == "plugin-parent":
        directory = external / "demo-plugin"
        directory.mkdir()
        (directory / "plugin.json").write_bytes(sentinel.read_bytes())
        link, destination = repo / "plugins/demo-plugin", directory
        (link / "plugin.json").unlink()
        link.rmdir()
    elif target == "standalone":
        support._git(["checkout", "-q", "main"], repo)
        manager = repo / "services/index"
        manager.mkdir(parents=True)
        (manager / "pyproject.toml").write_text(
            '[project]\nname="example-index"\nversion="0.9.0-dev8"\n', encoding="utf-8",
        )
        main = support._commit(repo, "shipped standalone")
        support._git(["checkout", "-q", "dev"], repo)
        link = repo / "services/index/pyproject.toml"
        link.parent.mkdir(parents=True)
        destination = external / "pyproject.toml"
        destination.write_text(
            '[project]\nname="example-index"\nversion="0.1.0-dev1"\n', encoding="utf-8",
        )
    elif target == "fallback":
        destination = external / "__init__.py"
        destination.write_text('__version__ = "0.1.0-dev1"\n', encoding="utf-8")
        link = repo / "plugins/demo-plugin/src/example/__init__.py"
        link.parent.mkdir(parents=True)
    elif target == "template":
        root = repo / "plugins/demo-plugin"
        (root / "instruction-projections.json").write_text(json.dumps({
            "projections": [{"template": "template.md"}],
        }), encoding="utf-8")
        destination = external / "template.md"
        destination.write_text("[owner: demo-plugin@0.1.0-dev1]\n", encoding="utf-8")
        link = root / "template.md"
    elif target == "marketplace":
        link = repo / ".github/plugin/marketplace.json"
        destination = external / "marketplace.json"
        destination.write_bytes(link.read_bytes())
        link.unlink()
    else:
        link, destination = repo / ".changefiles", external
        if target == "changefile-file":
            link.mkdir()
            link /= "pending.json"
            destination = sentinel
    link.symlink_to(destination, target_is_directory=destination.is_dir())
    source = support._commit(repo, "linked composition input")
    before = {path.relative_to(external): path.read_bytes()
              for path in external.rglob("*") if path.is_file()}
    worktrees = support._git(["worktree", "list", "--porcelain"], repo)

    with pytest.raises(ValueError, match="unsafe (library version|changefile)"):
        local.compose(repo, source_commit=source, main_seed=main)

    assert {path.relative_to(external): path.read_bytes()
            for path in external.rglob("*") if path.is_file()} == before
    assert support._git(["worktree", "list", "--porcelain"], repo) == worktrees
    assert support._git(["status", "--porcelain"], repo) == ""
