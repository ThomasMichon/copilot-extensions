"""Real Git containment controls for the source-only composition adapter."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shlex
import time

import pytest

import local_runtime_promotion as local
import test_promote_release as support

repo = support.repo


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
