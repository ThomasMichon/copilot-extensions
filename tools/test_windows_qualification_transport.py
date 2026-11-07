"""Actual Git consumer checks for the one-ref qualification transport."""

from __future__ import annotations

import subprocess

import pytest

from windows_qualification_contract import TRANSPORT_REF
from windows_qualification_transport import Transport, command


def git(root, *args):
    return command(["git", *args], cwd=root).stdout.strip()


def repository(path):
    path.mkdir()
    git(path, "init", "--initial-branch=main")
    git(path, "config", "user.name", "Fixture")
    git(path, "config", "user.email", "fixture@example.invalid")
    (path / "source.txt").write_text("first", encoding="utf-8")
    git(path, "add", "source.txt")
    git(path, "commit", "-m", "First")
    return path


def test_append_retains_old_candidates_and_preserves_head_index_dirty_tree_and_refs(tmp_path):
    root = repository(tmp_path / "source")
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", str(remote))
    transport = Transport(root, str(remote))
    first = git(root, "rev-parse", "HEAD")
    (root / "source.txt").write_text("second", encoding="utf-8")
    git(root, "commit", "-am", "Second")
    second = git(root, "rev-parse", "HEAD")
    (root / "source.txt").write_text("dirty", encoding="utf-8")
    before = (
        git(root, "symbolic-ref", "HEAD"), git(root, "rev-parse", "HEAD"),
        git(root, "diff"), git(root, "diff", "--cached"),
    )
    updates = []
    first_envelope = transport.append(first, updates.append)
    second_envelope = transport.append(second, updates.append)
    assert first_envelope != second_envelope
    assert git(root, "ls-remote", "--refs", str(remote)).split()[1] == TRANSPORT_REF
    git(root, "merge-base", "--is-ancestor", first, second_envelope)
    git(root, "merge-base", "--is-ancestor", first_envelope, second_envelope)
    assert git(root, "rev-parse", second_envelope + "^{tree}") == git(root, "rev-parse", second + "^{tree}")
    assert before == (
        git(root, "symbolic-ref", "HEAD"), git(root, "rev-parse", "HEAD"),
        git(root, "diff"), git(root, "diff", "--cached"),
    )
    assert updates[0]["phase"] == "transport-attempt" and updates[-1]["phase"] == "transport-confirmed"


def test_unrelated_reserved_ref_is_not_adopted(tmp_path):
    root = repository(tmp_path / "source")
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", str(remote))
    source = git(root, "rev-parse", "HEAD")
    git(root, "push", str(remote), source + ":" + TRANSPORT_REF)
    with pytest.raises(ValueError, match="unrelated"):
        Transport(root, str(remote)).append(source, lambda state: None)
    assert git(root, "ls-remote", str(remote), TRANSPORT_REF).split()[0] == source


def test_competing_append_rebuilds_fast_forward_without_force(tmp_path):
    root = repository(tmp_path / "source")
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", str(remote))
    first = git(root, "rev-parse", "HEAD")
    (root / "source.txt").write_text("other", encoding="utf-8")
    git(root, "commit", "-am", "Other")
    second = git(root, "rev-parse", "HEAD")
    raced = False
    argv_seen = []

    def racing_run(argv, **kwargs):
        nonlocal raced
        argv_seen.append(argv)
        if "push" in argv and not raced:
            raced = True
            Transport(root, str(remote)).append(first, lambda state: None)
        return command(argv, **kwargs)
    envelope = Transport(root, str(remote), run=racing_run).append(second, lambda state: None)
    git(root, "merge-base", "--is-ancestor", first, envelope)
    assert sum("push" in argv for argv in argv_seen) == 2
    assert not any("--force" in argv or any(arg.startswith("+") for arg in argv) for argv in argv_seen)


def test_lost_push_response_reconciles_exact_original_envelope(tmp_path):
    root = repository(tmp_path / "source")
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", str(remote))
    source = git(root, "rev-parse", "HEAD")
    pushes = []

    def lost_response(argv, **kwargs):
        result = command(argv, **kwargs)
        if "push" in argv:
            pushes.append(argv)
            return subprocess.CompletedProcess(argv, 1, "", "response lost")
        return result
    updates = []
    envelope = Transport(root, str(remote), run=lost_response).append(source, updates.append)
    assert len(pushes) == 1
    assert envelope == updates[0]["envelope_sha"] == updates[-1]["envelope_sha"]
