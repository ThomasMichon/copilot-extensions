"""Actual Git consumer checks for the one-ref qualification transport."""

from __future__ import annotations

import subprocess
import os
import shlex
import sys
from pathlib import Path

import pytest

from windows_qualification_contract import TRANSPORT_MARKER, TRANSPORT_REF, canonical
from windows_qualification_transport import Transport, command, rejected_attempt


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


@pytest.mark.parametrize("remove_history", [False, True])
def test_competing_append_rebuilds_fast_forward_without_force(tmp_path, remove_history):
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
        result = command(argv, **kwargs)
        if "fetch" in argv and raced and remove_history:
            git(remote, "update-ref", "-d", TRANSPORT_REF)
        return result
    transport = Transport(root, str(remote), run=racing_run)
    if remove_history:
        with pytest.raises(ValueError, match="Original transport history"):
            transport.append(second, lambda state: None)
        assert sum("push" in argv for argv in argv_seen) == 1
        assert git(root, "ls-remote", "--refs", str(remote)) == ""
        return
    envelope = transport.append(second, lambda state: None)
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


@pytest.mark.parametrize("observed", [
    "valid", "other-envelope", "other-ref", "other-previous", "lost-response", "policy",
])
def test_only_exact_contention_rejection_permits_recovery(observed):
    envelope, previous = "a" * 40, "b" * 40
    status_envelope = "d" * 40 if observed == "other-envelope" else envelope
    ref = "refs/heads/other" if observed == "other-ref" else TRANSPORT_REF
    expected = "d" * 40 if observed == "other-previous" else previous
    reason = (
        f"[remote rejected] (cannot lock ref '{ref}': is at {'c' * 40} but expected {expected})"
    )
    if observed == "policy":
        reason = "[remote rejected] (fixture policy)"
    output = "" if observed == "lost-response" else f"!\t{status_envelope}:{ref}\t{reason}\n"
    assert rejected_attempt({
        "envelope_sha": envelope, "previous_tip": previous,
        "transport_failure": {"returncode": 1, "stdout": output, "stderr": "push failed"},
    }) is (observed == "valid")


@pytest.mark.parametrize("lost", [False, True])
@pytest.mark.parametrize("first_ref", [False, True])
def test_interrupted_reconciliation_recovers_known_rejection_not_unknown_effect(
    tmp_path, monkeypatch, lost, first_ref,
):
    from test_windows_qualification import fake_client
    client, directory, state = fake_client(tmp_path, monkeypatch)
    root = repository(tmp_path / "source")
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", str(remote))
    first = git(root, "rev-parse", "HEAD")
    if not first_ref:
        Transport(root, str(remote)).append(first, lambda value: None)
    (root / "source.txt").write_text("candidate", encoding="utf-8")
    git(root, "commit", "-am", "Candidate")
    candidate = git(root, "rev-parse", "HEAD")
    state["request"].update(source_sha=candidate, envelope_sha=None)
    client.save(directory, state)
    monkeypatch.setattr(client, "preflight", lambda selected: state["provider"])
    pushes = []
    raced = False

    def interrupted(argv, **kwargs):
        nonlocal raced
        if "fetch" in argv and raced:
            raise TimeoutError("interrupted read-only reconciliation")
        if "push" in argv:
            pushes.append(argv)
            raced = True
            # A different valid envelope advances the one shared ref.
            if first_ref:
                Transport(root, str(remote)).append(first, lambda value: None)
            tree = git(root, "rev-parse", first + "^{tree}")
            previous = git(root, "ls-remote", str(remote), TRANSPORT_REF).split()[0]
            body = git(root, "show", "-s", "--format=%B", previous)
            competitor = command(
                ["git", "commit-tree", tree, "-p", previous, "-p", first],
                cwd=root, data=body + "\n",
            ).stdout.strip()
            git(root, "push", str(remote), competitor + ":" + TRANSPORT_REF)
            if lost:
                return subprocess.CompletedProcess(argv, 1, "", "response lost")
        return command(argv, **kwargs)

    client.transport = Transport(root, str(remote), run=interrupted)
    with pytest.raises(RuntimeError, match="interrupted read-only"):
        client.append_transport(directory, state)
    original = client.load(state["request"]["request_id"])[1]
    assert original["phase"] == "transport-attempt"
    anchor = original.get("transport_observed_tip") or original.get("transport_history_anchor")
    assert anchor
    assert len(pushes) == 1
    client.transport = Transport(root, str(remote))
    dispatched = []
    monkeypatch.setattr(client, "dispatch", lambda directory, value: dispatched.append(value) or value)
    if lost:
        with pytest.raises(ValueError, match="remains unknown"):
            client.resume(state["request"]["request_id"])
        assert dispatched == []
        saved = client.load(state["request"]["request_id"])[1]
        keys = (
            "repository", "request", "phase", "provider", "created_at",
            "envelope_sha", "previous_tip", "transport_failure",
        )
        assert {key: saved[key] for key in keys} == {key: original[key] for key in keys}
    else:
        recovered = client.resume(state["request"]["request_id"])
        assert recovered["request"]["request_id"] == original["request"]["request_id"]
        assert recovered["request"]["source_sha"] == candidate
        assert recovered["request"]["envelope_sha"] != original["envelope_sha"]
        assert recovered["transport_failure"] is None
        git(root, "merge-base", "--is-ancestor", anchor, recovered["transport_history_anchor"])
        assert recovered["transport_history"] == [{
            key: original[key] for key in
            ("envelope_sha", "previous_tip", "phase", "transport_failure")
        }]
        assert len(dispatched) == 1
        git(root, "merge-base", "--is-ancestor", candidate, recovered["request"]["envelope_sha"])


@pytest.mark.parametrize("mutation", ["delete", "rewrite", "rewind", "contended-rewrite"])
@pytest.mark.parametrize("first_ref", [False, True])
def test_recovery_keeps_original_history_across_append_reads_and_retries(
    tmp_path, monkeypatch, mutation, first_ref,
):
    from test_windows_qualification import fake_client
    client, directory, state = fake_client(tmp_path, monkeypatch)
    root = repository(tmp_path / "source")
    remote = tmp_path / "remote.git"
    git(root, "init", "--bare", str(remote))
    first = git(root, "rev-parse", "HEAD")
    competing = Transport(root, str(remote)).append(first, lambda value: None)
    previous = None if first_ref else competing
    (root / "source.txt").write_text("candidate", encoding="utf-8")
    git(root, "commit", "-am", "Candidate")
    candidate = git(root, "rev-parse", "HEAD")

    def envelope(source, parents):
        return command(
            ["git", "commit-tree", git(root, "rev-parse", source + "^{tree}"),
             *[argument for parent in parents for argument in ("-p", parent)]],
            cwd=root, data=canonical({"format": TRANSPORT_MARKER, "candidate": source}) + "\n",
        ).stdout.strip()

    original = envelope(candidate, ([previous] if previous else []) + [candidate])
    rewritten = envelope(first, [first])
    assert rewritten != competing
    seen = competing
    if mutation == "rewind":
        seen = envelope(first, [competing, first])
        git(remote, "fetch", "--no-tags", "--no-write-fetch-head", str(root), seen)
        git(remote, "update-ref", TRANSPORT_REF, seen)
    if mutation != "delete":
        git(remote, "fetch", "--no-tags", "--no-write-fetch-head", str(root), rewritten)
    state["request"].update(source_sha=candidate, envelope_sha=None)
    state.update(
        phase="transport-attempt", envelope_sha=original, previous_tip=previous,
        transport_failure={"returncode": 1, "stderr": "", "stdout": (
            f"!\t{original}:{TRANSPORT_REF}\t[rejected] (fetch first)\n"
        )},
    )
    client.save(directory, state)
    monkeypatch.setattr(client, "preflight", lambda selected: state["provider"])
    changed = False
    pushes = []

    def mutate():
        nonlocal changed
        if mutation == "delete":
            git(remote, "update-ref", "-d", TRANSPORT_REF)
        elif mutation == "rewind":
            git(remote, "update-ref", TRANSPORT_REF, competing)
        else:
            git(remote, "update-ref", TRANSPORT_REF, rewritten)
        changed = True

    def moving(argv, **kwargs):
        if "push" in argv:
            pushes.append(argv)
            if mutation == "contended-rewrite" and not changed:
                mutate()
        result = command(argv, **kwargs)
        if "fetch" in argv and mutation != "contended-rewrite" and not changed:
            # The reconciliation read returns its old tip; append must read again.
            mutate()
            if mutation == "rewind" and not first_ref:
                raise TimeoutError("interrupted history fetch")
        return result

    client.transport = Transport(root, str(remote), run=moving)
    dispatches = []
    monkeypatch.setattr(client, "dispatch", lambda *args: dispatches.append(args))
    with pytest.raises((RuntimeError, TimeoutError), match="Original transport history|interrupted history"):
        client.resume(state["request"]["request_id"])
    assert changed and dispatches == []
    assert len(pushes) == (1 if mutation == "contended-rewrite" else 0)
    saved = client.load(state["request"]["request_id"])[1]
    if mutation == "rewind" and not first_ref:
        assert saved["transport_observed_tip"] == seen
        with pytest.raises(ValueError, match="history"):
            client.resume(state["request"]["request_id"])
        saved = client.load(state["request"]["request_id"])[1]
    assert saved["request"] == state["request"]
    if mutation == "contended-rewrite":
        assert saved["transport_history"][0]["envelope_sha"] == original
    else:
        assert {key: saved[key] for key in state} == state
    assert saved["transport_history_anchor"] == seen
    assert saved["transport_history_failure"]["required"] == seen
    with pytest.raises((ValueError, RuntimeError), match="history"):
        client.resume(state["request"]["request_id"])
    assert dispatches == []
    assert len(pushes) == (1 if mutation == "contended-rewrite" else 0)
    assert client.load(state["request"]["request_id"])[1] == saved
    current = git(root, "ls-remote", "--refs", str(remote), TRANSPORT_REF)
    expected = competing if mutation == "rewind" else rewritten
    assert current == ("" if mutation == "delete" else expected + "\t" + TRANSPORT_REF)
    if mutation == "rewind" and not first_ref:
        git(remote, "update-ref", TRANSPORT_REF, seen)
        client.resume(state["request"]["request_id"])
        restored = client.load(state["request"]["request_id"])[1]
        assert len(dispatches) == 1 and len(pushes) == 1
        assert restored["request"]["source_sha"] == candidate
        assert restored["request"]["request_id"] == state["request"]["request_id"]
        git(root, "merge-base", "--is-ancestor", seen, restored["request"]["envelope_sha"])


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
