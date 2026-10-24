"""Exercise managed hook dispatch through the real shell without a runtime."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import time

from agent_procutil import no_window_kwargs
import pytest

from agent_worktrees import git_ops, hooks


def _shell():
    configured = git_ops.git("var", "GIT_SHELL_PATH", check=False).stdout.strip()
    candidates = [configured, shutil.which("sh")]
    git = shutil.which("git")
    if git:
        candidates.append(str(Path(git).parents[1] / "usr" / "bin" / "sh.exe"))
    shell = next((path for path in candidates if path and Path(path).is_file()), None)
    if shell is None:
        pytest.skip("Git's POSIX shell is unavailable")
    return shell


@pytest.fixture
def shim(tmp_path):
    shell = _shell()
    directory = tmp_path / "hooks with spaces"
    directory.mkdir()
    binaries = tmp_path / "bin"
    binaries.mkdir()
    dirname = binaries / "dirname"
    dirname.write_text(
        '#!/bin/sh\ncase "$1" in */*) printf "%s\\n" "${1%/*}";; *) printf ".\\n";; esac\n',
        encoding="utf-8", newline="\n",
    )
    dirname.chmod(0o755)
    calls, arguments, stdin = (tmp_path / name for name in ("calls", "arguments", "stdin"))
    payload = "refs/heads/source old refs/heads/target new\nsecond update line\n"

    def run(
        name="pre-push", *, enabled="1", authorized="1", cli=True,
        cli_exit=0, local=True, local_exit=0,
    ):
        hook = directory / name
        hook.write_text(hooks._SHIM_TEMPLATE.format(name=name), encoding="utf-8", newline="\n")
        hook.chmod(0o755)
        if cli:
            command = binaries / "agent-worktrees"
            command.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$@" > "$CALLS"\nexit "$CLI_EXIT"\n',
                encoding="utf-8", newline="\n",
            )
            command.chmod(0o755)
        if local:
            chained = directory / f"{name}.local"
            chained.write_text(
                '#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGUMENTS"\n'
                'while IFS= read -r line; do printf "%s\\n" "$line"; done > "$STDIN"\n'
                'exit "$LOCAL_EXIT"\n',
                encoding="utf-8", newline="\n",
            )
            chained.chmod(0o755)
        env = {
            **os.environ, "PATH": str(binaries), "CALLS": calls.as_posix(),
            "ARGUMENTS": arguments.as_posix(), "STDIN": stdin.as_posix(),
            "CLI_EXIT": str(cli_exit), "LOCAL_EXIT": str(local_exit),
        }
        for key in ("BASH_ENV", "ENV", "AGENT_WORKTREES_HOOKS", "AGENT_WORKTREES_PR_PUSH"):
            env.pop(key, None)
        for key, value in (
            ("AGENT_WORKTREES_HOOKS", enabled), ("AGENT_WORKTREES_PR_PUSH", authorized),
        ):
            if value is not None:
                env[key] = value
        started = time.monotonic()
        result = subprocess.run(
            [shell, hook.as_posix(), "origin", "fixture-target"], cwd=tmp_path,
            input=payload, capture_output=True, text=True, env=env, timeout=5,
            **no_window_kwargs(),
        )
        elapsed = time.monotonic() - started
        return result, elapsed

    return run, calls, arguments, stdin, payload


@pytest.mark.parametrize("cli", [False, True])
@pytest.mark.parametrize("local_exit", [0, 37])
def test_authorized_push_skips_runtime_but_preserves_local_hook(shim, cli, local_exit):
    run, calls, arguments, stdin, payload = shim
    result, elapsed = run(cli=cli, cli_exit=91, local_exit=local_exit)
    assert result.returncode == local_exit, result.stderr
    assert not calls.exists()
    assert arguments.read_text().splitlines() == ["origin", "fixture-target"]
    assert stdin.read_text() == payload
    assert elapsed < 5


def test_authorized_push_needs_neither_runtime_nor_local_hook(shim):
    run, calls, arguments, stdin, _ = shim
    result, _ = run(cli=False, local=False)
    assert result.returncode == 0, result.stderr
    assert not any(path.exists() for path in (calls, arguments, stdin))


@pytest.mark.parametrize("name,enabled,authorized,invoke", [
    ("pre-push", "1", None, True),
    ("pre-push", "1", "0", True),
    ("pre-push", "1", "true", True),
    ("pre-push", "1", "", True),
    ("pre-commit", "1", "1", True),
    ("pre-push", None, "1", False),
    ("pre-push", "0", "1", False),
    ("pre-commit", "0", "1", False),
])
def test_existing_guard_selection_and_local_chain_are_unchanged(
    shim, name, enabled, authorized, invoke,
):
    run, calls, arguments, stdin, payload = shim
    result, _ = run(name, enabled=enabled, authorized=authorized)
    assert result.returncode == 0, result.stderr
    assert calls.exists() == invoke
    if invoke:
        assert calls.read_text().splitlines() == ["hook", name, "origin", "fixture-target"]
    assert arguments.read_text().splitlines() == ["origin", "fixture-target"]
    assert stdin.read_text() == payload


@pytest.mark.parametrize("name,authorized", [("pre-push", None), ("pre-commit", "1")])
def test_runtime_guard_refusal_still_blocks_the_local_hook(shim, name, authorized):
    run, calls, arguments, stdin, _ = shim
    result, _ = run(name, authorized=authorized, cli_exit=41)
    assert result.returncode == 41
    assert calls.read_text().splitlines() == ["hook", name, "origin", "fixture-target"]
    assert not arguments.exists() and not stdin.exists()


@pytest.mark.parametrize("local_exit", [0, 37])
def test_real_git_push_without_runtime_still_honors_local_hook(tmp_path, local_exit):
    shell, executable = _shell(), shutil.which("git")
    anchor, remote = tmp_path / "source", tmp_path / "remote.git"
    anchor.mkdir()
    git_ops.git("init", "-b", "main", cwd=anchor)
    git_ops.git("config", "user.email", "fixture@example.invalid", cwd=anchor)
    git_ops.git("config", "user.name", "Fixture", cwd=anchor)
    (anchor / "fixture.txt").write_text("fixture\n", encoding="utf-8")
    git_ops.git("add", "fixture.txt", cwd=anchor)
    git_ops.git("commit", "-m", "fixture", cwd=anchor)
    git_ops.git("init", "--bare", str(remote), cwd=tmp_path)
    git_ops.git("remote", "add", "origin", str(remote), cwd=anchor)
    hooks.install_hooks(anchor)
    local = hooks.hooks_dir_for(anchor) / "pre-push.local"
    arguments, stdin = tmp_path / "arguments", tmp_path / "stdin"
    local.write_text(
        '#!/bin/sh\nprintf "%s\\n" "$@" > "$ARGUMENTS"\n'
        'while IFS= read -r line; do printf "%s\\n" "$line"; done > "$STDIN"\n'
        'exit "$LOCAL_EXIT"\n',
        encoding="utf-8", newline="\n",
    )
    local.chmod(0o755)
    env = {
        **os.environ, "PATH": os.pathsep.join((str(Path(shell).parent), str(Path(executable).parent))),
        "AGENT_WORKTREES_HOOKS": "1", "AGENT_WORKTREES_PR_PUSH": "1",
        "ARGUMENTS": arguments.as_posix(), "STDIN": stdin.as_posix(), "LOCAL_EXIT": str(local_exit),
    }
    for key in ("BASH_ENV", "ENV"):
        env.pop(key, None)
    probe = subprocess.run(
        [shell, "-c", "command -v agent-worktrees"], env=env, cwd=anchor,
        capture_output=True, text=True, timeout=5, **no_window_kwargs(),
    )
    assert probe.returncode != 0, "fixture must have no runtime command"
    result = subprocess.run(
        [executable, "push", "origin", "HEAD:refs/heads/fixture"], env=env, cwd=anchor,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert arguments.read_text().splitlines()[0] == "origin"
    assert "refs/heads/fixture" in stdin.read_text()
    published = git_ops.git(
        "--git-dir", str(remote), "rev-parse", "--verify", "refs/heads/fixture",
        cwd=anchor, check=False,
    )
    if local_exit:
        assert result.returncode != 0 and published.returncode != 0
    else:
        assert result.returncode == 0, result.stderr
        assert published.stdout.strip() == git_ops.git("rev-parse", "HEAD", cwd=anchor).stdout.strip()
