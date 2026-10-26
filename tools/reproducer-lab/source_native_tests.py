"""Explicitly selected local-Git qualification; excluded from default discovery."""

from __future__ import annotations

import importlib.util
import os
import shutil
import sys
from pathlib import Path

import pytest

from process import run

spec = importlib.util.spec_from_file_location("reproducer_lab_source_native", Path(__file__).with_name("source.py"))
assert spec is not None and spec.loader is not None
source = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = source
spec.loader.exec_module(source)


@pytest.mark.parametrize("object_format", ["sha1", "sha256"])
def test_real_git_snapshot_excludes_dirty_untracked_configuration_and_hooks(
    tmp_path: Path, object_format: str,
) -> None:
    git = shutil.which("git")
    assert git is not None
    repository, results, template = (tmp_path / name for name in ("origin", "results", "template"))
    for directory in (repository, results, template):
        directory.mkdir(mode=0o700)
    env = {
        "PATH": os.defpath, "HOME": str(tmp_path),
        "GIT_CONFIG_NOSYSTEM": "1", "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_ATTR_NOSYSTEM": "1",
        "GIT_AUTHOR_NAME": "Synthetic Lab", "GIT_AUTHOR_EMAIL": "lab@example.invalid",
        "GIT_COMMITTER_NAME": "Synthetic Lab", "GIT_COMMITTER_EMAIL": "lab@example.invalid",
    }

    def invoke(*arguments: str) -> str:
        result = run([git, "-C", str(repository), *arguments], timeout=15, env=env)
        assert result.returncode == 0, "synthetic Git setup/inspection failed"
        return result.stdout.strip()

    invoke("init", "--quiet", f"--object-format={object_format}", f"--template={template}")
    (repository / "historical.txt").write_text("synthetic history outside the selected tree\n")
    invoke("add", "historical.txt")
    invoke("commit", "--quiet", "-m", "synthetic older source")
    previous = invoke("rev-parse", "HEAD")
    invoke("rm", "historical.txt")
    (repository / "module.py").write_text("print('committed')\n")
    invoke("add", "module.py")
    invoke("commit", "--quiet", "-m", "synthetic source")
    commit = invoke("rev-parse", "HEAD")
    invoke("config", "remote.unused.url", "https://example.invalid/not-used")
    hook = repository / ".git" / "hooks" / "post-checkout"
    hook.parent.mkdir(exist_ok=True)
    hook.write_text("#!/bin/sh\nexit 42\n")
    hook.chmod(0o700)
    (repository / "module.py").write_text("print('dirty')\n")
    (repository / "untracked.txt").write_text("synthetic input must stay outside snapshot\n")
    invoke("update-index", "--skip-worktree", "module.py")
    selected = source.SourceReader(Path(git)).materialize(repository, commit, results, "attempt")
    checkout = results / "attempt" / "source"
    assert selected.commit == commit
    assert (checkout / "module.py").read_text() == "print('committed')\n"
    assert not (checkout / "untracked.txt").exists()
    assert not (checkout / "historical.txt").exists()
    assert not (checkout / ".git" / "FETCH_HEAD").exists()
    assert not (checkout / ".git" / "hooks" / "post-checkout").exists()
    config = (checkout / ".git" / "config").read_text()
    assert "example.invalid/not-used" not in config and str(repository) not in config
    count = run([git, "-C", str(checkout), "rev-list", "--count", "HEAD"], timeout=15, env=env)
    assert count.returncode == 0 and count.stdout.strip() == "1"
    prior = run([git, "-C", str(checkout), "cat-file", "-e", previous], timeout=15, env=env)
    assert prior.returncode != 0
    assert (repository / "module.py").read_text() == "print('dirty')\n"
    invoke("config", "core.ignoreCase", "false")
    (repository / "MODULE.py").write_text("print('colliding')\n")
    invoke("add", "MODULE.py")
    invoke("commit", "--quiet", "-m", "synthetic colliding paths")
    colliding = invoke("rev-parse", "HEAD")
    with pytest.raises(source.SourceError, match="path aliases"):
        source.SourceReader(Path(git)).materialize(repository, colliding, results, "collision")
    assert not (results / "collision" / "source").exists()
    assert not (results / "collision" / "source.json").exists()
