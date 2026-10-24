"""Fresh committed policy reads share plumbing, not a cross-request cache."""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_worktrees import git_ops, inrepo_config_source as source


@pytest.fixture
def anchor(tmp_path):
    def git(*args, **kwargs):
        return git_ops.git(
            "-c", "user.name=Fixture", "-c", "user.email=fixture@example.invalid",
            *args, cwd=tmp_path, **kwargs,
        )

    git("init", "--template=", "-b", "main")

    def commit(text, *, branch="main"):
        (tmp_path / "config.yaml").write_bytes(text)
        git("add", "config.yaml")
        git("commit", "-m", "fixture policy")
        git("update-ref", f"refs/remotes/origin/{branch}", "HEAD")
        git("symbolic-ref", "refs/remotes/origin/HEAD", "refs/remotes/origin/main")

    return tmp_path, git, commit


def resolve(path):
    return source.load_inrepo_config_from_committed_ref(
        path, (Path("missing.yaml"), Path("config.yaml"), Path("later.yaml")),
    )


def test_real_batch_preserves_utf8_crlf_and_missing_candidates(anchor, monkeypatch):
    path, _git, commit = anchor
    commit("marker: caf\u00e9\r\nheadless: false\r\n".encode("utf-8"))
    original, calls = git_ops.git, []

    def observed(*args, **kwargs):
        calls.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", observed)
    assert resolve(path) == {"marker": "caf\u00e9", "headless": False}
    assert calls == [("symbolic-ref", "-q", "refs/remotes/origin/HEAD"), ("cat-file", "--batch")]


def test_later_request_observes_new_committed_headless_policy(anchor):
    path, _git, commit = anchor
    commit(b"headless: false\n")
    assert resolve(path) == {"headless": False}
    (path / "config.yaml").write_text("headless: true\n", encoding="utf-8")
    assert resolve(path) == {"headless": False}
    commit(b"headless: true\n")
    assert resolve(path) == {"headless": True}


@pytest.mark.parametrize("first", [b"", b"[malformed", b"marker: first\n"])
def test_empty_or_malformed_first_file_never_promotes_later_policy(anchor, first):
    path, git, commit = anchor
    commit(first)
    (path / "later.yaml").write_text("headless: true\n", encoding="utf-8")
    git("add", "later.yaml")
    git("commit", "-m", "lower priority")
    git("update-ref", "refs/remotes/origin/main", "HEAD")
    assert resolve(path) == ({"marker": "first"} if first == b"marker: first\n" else {})


def test_default_branch_hop_remains_fresh(anchor):
    path, git, commit = anchor
    commit(b"default_branch: dev\nheadless: false\n")
    git("checkout", "-b", "dev")
    commit(b"default_branch: dev\nheadless: true\n", branch="dev")
    assert resolve(path) == {"default_branch": "dev", "headless": True}


@pytest.mark.parametrize("output", [
    b"", b"not-a-header\n", b"a" * 40 + b" tree 0\n\n",
    b"x" * 40 + b" blob 0\n\n", b"a" * 40 + b" blob -1\n\n",
    b"a" * 40 + b" blob 20\nshort\n",
    b"a" * 40 + b" blob 0\n\nunexpected",
    "decoded text is not a binary packet",
])
def test_malformed_binary_packet_retains_ordered_fallback(monkeypatch, tmp_path, output):
    monkeypatch.setattr(
        git_ops, "git", lambda *_a, **_k: SimpleNamespace(returncode=0, stdout=output),
    )
    assert source._read_committed_batch(
        tmp_path, "origin", "main", (Path("config.yaml"),), time.monotonic() + 10,
    ) is None


def test_batch_failure_uses_existing_bounded_fallback(anchor, monkeypatch):
    path, _git, commit = anchor
    commit(b"marker: retained\n")
    original = git_ops.git

    def unavailable(*args, **kwargs):
        if args[0] == "cat-file":
            raise subprocess.TimeoutExpired(args, kwargs["timeout"])
        return original(*args, **kwargs)

    monkeypatch.setattr(git_ops, "git", unavailable)
    assert resolve(path) == {"marker": "retained"}


def test_git_binary_input_preserves_bytes_and_default_text_contract(anchor):
    _path, git, _commit = anchor
    payload = "line one\r\ncaf\u00e9\r\n".encode("utf-8")
    oid = git("hash-object", "-w", "--stdin", input_data=payload).stdout.strip()
    result = git("cat-file", "--batch", input_data=oid + b"\n")
    assert result.stdout == oid + b" blob " + str(len(payload)).encode() + b"\n" + payload + b"\n"
    assert isinstance(git("--version").stdout, str)
    with pytest.raises(git_ops.GitError) as failure:
        git("not-a-real-git-command", input_data=b"")
    assert isinstance(failure.value.stderr, str)


def test_binary_input_retains_requested_tree_runner(monkeypatch):
    monkeypatch.setattr(
        git_ops.subprocess, "run", lambda *_a, **_k: pytest.fail("tree custody bypassed"),
    )
    calls = []

    def bounded(cmd, **kwargs):
        calls.append(kwargs)
        return subprocess.CompletedProcess(cmd, 0, b"packet", b"")

    monkeypatch.setattr(git_ops.push_timeout, "run_bounded", bounded)
    assert git_ops.git("cat-file", "--batch", input_data=b"", kill_tree=True, timeout=1).stdout == b"packet"
    assert calls[0]["binary"] is True and calls[0]["input"] == b""
    assert calls[0]["timeout"] == 1
