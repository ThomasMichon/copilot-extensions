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
def shim(tmp_path, monkeypatch):
    monkeypatch.setattr(hooks, "_pr_enabled", lambda cwd: False)
    monkeypatch.setattr(hooks, "_protected_branches", lambda cwd: [])
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
    payload = f"refs/heads/source {'a' * 40} refs/heads/target {'b' * 40}\n" * 2

    def run(
        name="pre-push", *, enabled="1", authorized="1", cli=True,
        cli_exit=0, local=True, local_exit=0, pr=False, protected=(), payload_override=None,
    ):
        monkeypatch.setattr(hooks, "_pr_enabled", lambda cwd: pr)
        monkeypatch.setattr(hooks, "_protected_branches", lambda cwd: list(protected))
        hook = directory / name
        text = hooks._pre_push_shim(tmp_path) if name == "pre-push" else hooks._SHIM_TEMPLATE.format(name=name)
        hook.write_text(text, encoding="utf-8", newline="\n")
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
            input=(payload if payload_override is None else payload_override).encode("utf-8"),
            capture_output=True, env=env, timeout=5,
            **no_window_kwargs(),
        )
        elapsed = time.monotonic() - started
        result = subprocess.CompletedProcess(
            result.args, result.returncode,
            result.stdout.decode("utf-8", "replace"), result.stderr.decode("utf-8", "replace"),
        )
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


@pytest.mark.parametrize("ref,head,accepted", [
    ("refs/heads/trunk", "a" * 40, True),
    ("refs/heads/trunk", "0" * 40, False),
    ("refs/heads/worktree/private", "a" * 40, False),
    ("refs/heads/feature", "0" * 40, True),
    ("refs/tags/fixture", "a" * 40, True),
])
def test_installed_shim_uses_resolved_policy_and_chains_exact_stdin(shim, ref, head, accepted):
    run, calls, arguments, stdin, _ = shim
    payload = f"HEAD {head} {ref} {'b' * 40}\n"
    result, _ = run(pr=True, protected=("trunk",), payload_override=payload)
    assert (result.returncode == 0) is accepted, result.stderr
    assert not calls.exists()
    assert arguments.exists() is accepted
    if accepted:
        assert stdin.read_text() == payload


@pytest.mark.parametrize("payload", ["not-a-ref-update\n", f"HEAD {'z' * 40} refs/heads/feature {'b' * 40}\n"])
def test_installed_shim_refuses_malformed_stdin_without_runtime(shim, payload):
    run, calls, arguments, stdin, _ = shim
    result, _ = run(payload_override=payload)
    assert result.returncode != 0
    assert not any(path.exists() for path in (calls, arguments, stdin))


@pytest.mark.parametrize("name,enabled,authorized,invoke", [
    ("pre-push", "1", None, False),
    ("pre-push", "1", "0", False),
    ("pre-push", "1", "true", False),
    ("pre-push", "1", "", False),
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


@pytest.mark.parametrize("name,authorized", [("pre-commit", "1")])
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


def test_install_preserves_repository_hook_without_recursive_chaining(tmp_path, monkeypatch):
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
    root = Path(__file__).resolve().parents[3]
    resource = anchor / "plugins/agent-worktrees/src/agent_worktrees/pre_push.sh"
    resource.parent.mkdir(parents=True)
    shutil.copyfile(root / "plugins/agent-worktrees/src/agent_worktrees/pre_push.sh", resource)
    seen = tmp_path / "repository-hook-seen"
    original = (
        '#!/bin/sh\n[ ! -e "$REPOSITORY_HOOK_SEEN" ] || exit 91\n'
        ': > "$REPOSITORY_HOOK_SEEN"\n'
        + (root / "tools/hooks/pre-push").read_text(encoding="utf-8")
    )
    hdir = hooks.hooks_dir_for(anchor)
    hdir.mkdir(parents=True, exist_ok=True)
    target = hdir / "pre-push"
    target.write_text(original, encoding="utf-8", newline="\n")
    target.chmod(0o755)
    monkeypatch.setattr(hooks, "_pr_enabled", lambda cwd: True)
    monkeypatch.setattr(hooks, "_protected_branches", lambda cwd: ["main", "dev"])
    hooks.install_hooks(anchor)
    assert (hdir / "pre-push.local").read_text(encoding="utf-8") == original
    env = {
        **os.environ, "PATH": os.pathsep.join((str(Path(shell).parent), str(Path(executable).parent))),
        "AGENT_WORKTREES_HOOKS": "1", "AGENT_WORKTREES_PR_PUSH": "1",
        "REPOSITORY_HOOK_SEEN": seen.as_posix(),
    }
    for key in ("BASH_ENV", "ENV"):
        env.pop(key, None)
    result = subprocess.run(
        [executable, "push", "origin", "HEAD:refs/heads/fixture"], env=env, cwd=anchor,
        capture_output=True, text=True, timeout=30, **no_window_kwargs(),
    )
    assert result.returncode == 0, result.stderr
    assert seen.exists()
