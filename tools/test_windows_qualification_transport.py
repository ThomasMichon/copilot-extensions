"""Actual Git consumer checks for the one-ref qualification transport."""

from __future__ import annotations

import subprocess
import os
import shlex
import sys
from pathlib import Path

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


def test_managed_transport_passes_legacy_pr_guard_without_disabling_local_hooks(tmp_path, monkeypatch):
    anchor = repository(tmp_path / "source")
    config = anchor / ".agent-worktrees/config.yaml"
    config.parent.mkdir()
    config.write_text("pr:\n  enabled: true\n  required: true\n", encoding="utf-8")
    git(anchor, "add", ".agent-worktrees/config.yaml")
    git(anchor, "commit", "-m", "Require reviewed source publication")
    root = tmp_path / "worktree"
    git(anchor, "worktree", "add", "-b", "candidate", str(root))
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", str(remote))
    source = git(root, "rev-parse", "HEAD")
    git(root, "config", "push.followTags", "true")
    git(root, "tag", "-a", "unrelated-tag", "-m", "Must remain local")
    hook = anchor / ".git/hooks/pre-push"
    marker = tmp_path / "local-hook-ran"
    source_root = Path(__file__).resolve().parents[1]
    paths = [str(path) for path in sorted((source_root / "libs").glob("*/src"))]
    code = (
        f"import sys; sys.path[:0] = {paths + sys.path!r}; "
        "from agent_worktrees.hooks import run_hook; "
        "sys.exit(run_hook('pre-push', []))"
    )
    hook.write_text(
        "#!/bin/sh\n"
        f"{shlex.quote(sys.executable.replace(os.sep, '/'))} -c {shlex.quote(code)} || exit $?\n"
        f"printf 'chained\\n' >> {shlex.quote(marker.as_posix())}\n",
        encoding="utf-8", newline="\n",
    )
    hook.chmod(0o755)
    monkeypatch.delenv("AGENT_WORKTREES_PR_PUSH", raising=False)
    denied = Transport(root, str(remote)).git(
        "push", "--porcelain", str(remote), source + ":refs/heads/unmediated", check=False,
    )
    assert denied.returncode and "BLOCKED" in denied.stderr, denied.stderr
    states = []
    envelope = Transport(root, str(remote)).append(source, states.append)
    assert git(root, "ls-remote", "--refs", str(remote)).split() == [envelope, TRANSPORT_REF]
    assert marker.read_text("utf-8") == "chained\n"
    assert "AGENT_WORKTREES_PR_PUSH" not in os.environ
    assert states[-1]["phase"] == "transport-confirmed"


@pytest.mark.parametrize("refspec", [
    "a" * 40 + ":refs/heads/main",
    "+" + "a" * 40 + ":" + TRANSPORT_REF,
    ":" + TRANSPORT_REF,
])
def test_mediated_transport_rejects_branch_force_and_delete_before_git(tmp_path, refspec):
    with pytest.raises(ValueError):
        command(
            ["git", "push", "--porcelain", "--no-follow-tags", str(tmp_path), refspec],
            cwd=tmp_path, transport_push=True,
        )


def test_rejected_push_retains_original_diagnostic_and_does_not_retry(tmp_path):
    root = repository(tmp_path / "source")
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", str(remote))
    source = git(root, "rev-parse", "HEAD")
    pushes = []

    def rejected(argv, **kwargs):
        if "push" in argv:
            pushes.append(argv)
            return subprocess.CompletedProcess(
                argv, 1, "! [remote rejected] fixture policy rejected\n", "failed to push some refs",
            )
        return command(argv, **kwargs)
    states = []
    with pytest.raises(RuntimeError, match="fixture policy rejected"):
        Transport(root, str(remote), run=rejected).append(source, states.append)
    assert len(pushes) == 1
    assert states[-1]["phase"] == "transport-unknown"
    assert states[-2]["transport_failure"] == {
        "returncode": 1, "stdout": "! [remote rejected] fixture policy rejected\n",
        "stderr": "failed to push some refs",
    }
    assert git(root, "ls-remote", "--refs", str(remote)) == ""


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


def test_competing_descendant_confirms_reused_tip_instead_of_rejected_envelope(tmp_path):
    root = repository(tmp_path / "source")
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", str(remote))
    candidate = git(root, "rev-parse", "HEAD")
    (root / "source.txt").write_text("descendant", encoding="utf-8")
    git(root, "commit", "-am", "Descendant")
    descendant = git(root, "rev-parse", "HEAD")
    published = []

    def competing(argv, **kwargs):
        if "push" in argv and not published:
            published.append(Transport(root, str(remote)).append(descendant, lambda state: None))
        return command(argv, **kwargs)
    states = []
    actual = Transport(root, str(remote), run=competing).append(candidate, states.append)
    assert actual == published[0]
    assert states[-1]["phase"] == "transport-confirmed"
    assert states[-1]["envelope_sha"] == actual
    assert states[0]["envelope_sha"] != actual


def test_inherited_selectors_cannot_move_candidate_or_request_store_to_another_worktree(tmp_path, monkeypatch):
    from test_windows_qualification import client_module
    root = repository(tmp_path / "source")
    (root / "tools").mkdir()
    (root / "tools/test_sample.py").write_text("def test_sample(): pass\n", encoding="utf-8")
    git(root, "add", "tools/test_sample.py")
    git(root, "commit", "-m", "Add focused consumer")
    git(root, "remote", "add", "origin", "https://github.com/owner/repository.git")
    candidate = git(root, "rev-parse", "HEAD")
    expected_store = (root / git(root, "rev-parse", "--git-path", "windows-qualification")).resolve()
    other = tmp_path / "other"
    git(root, "worktree", "add", "-b", "other", str(other))
    (other / "source.txt").write_text("other head", encoding="utf-8")
    git(other, "commit", "-am", "Other worktree")
    foreign_git = git(other, "rev-parse", "--absolute-git-dir")
    monkeypatch.setenv("GIT_DIR", foreign_git)
    monkeypatch.setenv("GIT_COMMON_DIR", str(root / ".git"))
    monkeypatch.setenv("GIT_WORK_TREE", str(other))
    monkeypatch.setenv("GIT_INDEX_FILE", str(Path(foreign_git) / "index"))
    client = client_module.Client(root, tmp_path / "unused-catalog-command", "owner/repository")
    monkeypatch.setattr(client, "preflight", lambda selected: {"owner": "owner", "branch": "main"})
    monkeypatch.setattr(client, "api", lambda endpoint: {"sha": endpoint.rsplit("/", 1)[1]})
    monkeypatch.setattr(client, "dispatch", lambda directory, state: state)
    actual = client.submit({
        "kind": "tools", "files": ["tools/test_sample.py"], "k": "",
        "full": False, "explicit_tiers": False,
    }, "HEAD")
    assert client.state_root == expected_store
    assert actual["request"]["source_sha"] == candidate
