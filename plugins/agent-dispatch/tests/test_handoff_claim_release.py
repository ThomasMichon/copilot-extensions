"""Tests for ``handoff_claim_release``: releasing the agent-worktrees claim a
context-handoff task's target worktree holds, once the task goes terminal.
"""

from __future__ import annotations

import threading
import time

from agent_dispatch import handoff_claim_release


def test_is_handoff_task_true_for_labeled_task():
    assert handoff_claim_release.is_handoff_task({"labels": ["handoff"]}) is True


def test_is_handoff_task_true_for_source_stamped_task_predating_the_label():
    assert (
        handoff_claim_release.is_handoff_task({"labels": [], "source": "context-handoff"})
        is True
    )


def test_is_handoff_task_false_for_an_ordinary_task():
    assert handoff_claim_release.is_handoff_task({"labels": ["review"]}) is False


def test_is_handoff_task_false_for_none_or_non_dict():
    assert handoff_claim_release.is_handoff_task(None) is False
    assert handoff_claim_release.is_handoff_task("not-a-dict") is False


def test_is_handoff_task_tolerates_missing_labels_key():
    assert handoff_claim_release.is_handoff_task({"source": None}) is False


class _Proc:
    def __init__(self, returncode=0, stdout="{}", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


# -- _release_task_claim: the synchronous argv/behavior surface -------------


def test_release_task_claim_targets_the_given_worktree_not_cwd(monkeypatch):
    """The release must target the handoff's own `target_worktree` field
    explicitly (via --worktree), never the releasing process's own cwd --
    the coordinator/MCP call site is generally NOT the handoff's worktree."""
    from agent_dispatch import procutil

    calls = []

    def fake_capture(*args, timeout):
        calls.append(list(args))
        return _Proc()

    monkeypatch.setattr(procutil, "run_agent_worktrees_capture", fake_capture)

    handoff_claim_release._release_task_claim("T1", worktree="wt-9")
    assert calls == [["claims", "release", "T1", "--json", "--worktree", "wt-9"]]


def test_release_task_claim_omits_worktree_flag_when_unknown(monkeypatch):
    from agent_dispatch import procutil

    calls = []

    def fake_capture(*args, timeout):
        calls.append(list(args))
        return _Proc()

    monkeypatch.setattr(procutil, "run_agent_worktrees_capture", fake_capture)

    handoff_claim_release._release_task_claim("T1", worktree=None)
    assert calls == [["claims", "release", "T1", "--json"]]


def test_release_task_claim_degrades_safe_when_agent_worktrees_unavailable(monkeypatch):
    from agent_dispatch import procutil

    monkeypatch.setattr(procutil, "run_agent_worktrees_capture", lambda *a, timeout: None)
    # Must not raise even though there's nowhere to shell out to.
    handoff_claim_release._release_task_claim("T1", worktree="wt-9")


# -- release_if_handoff: the fire-and-forget entry point ---------------------


def _wait_for(predicate, *, timeout=2.0):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


def test_release_if_handoff_dispatches_on_a_background_thread_not_inline(monkeypatch):
    """release_if_handoff must never block its caller -- the actual release
    runs on a background thread, so a slow/hanging subprocess never adds
    latency to (or risks timing out) the request that triggered it."""
    started = threading.Event()
    release_thread_is_background = {}

    def fake_release(task_id, *, worktree, timeout=15.0):
        release_thread_is_background["value"] = (
            threading.current_thread() is not threading.main_thread()
        )
        started.set()

    monkeypatch.setattr(handoff_claim_release, "_release_task_claim", fake_release)

    handoff_claim_release.release_if_handoff(
        {"id": "T1", "labels": ["handoff"], "target_worktree": "wt-9"}
    )
    # release_if_handoff itself must return immediately (no blocking wait
    # baked into this call), and the work happens on another thread shortly
    # after.
    assert _wait_for(started.is_set)
    assert release_thread_is_background["value"] is True


def test_release_if_handoff_passes_through_worktree_and_id(monkeypatch):
    calls = []
    done = threading.Event()

    def fake_release(task_id, *, worktree, timeout=15.0):
        calls.append((task_id, worktree))
        done.set()

    monkeypatch.setattr(handoff_claim_release, "_release_task_claim", fake_release)

    handoff_claim_release.release_if_handoff(
        {"id": "T1", "labels": ["handoff"], "target_worktree": "wt-9"}
    )
    assert _wait_for(done.is_set)
    assert calls == [("T1", "wt-9")]


def test_release_if_handoff_no_ops_for_a_non_handoff_task(monkeypatch):
    calls = []

    def fake_release(task_id, *, worktree, timeout=15.0):
        calls.append(task_id)

    monkeypatch.setattr(handoff_claim_release, "_release_task_claim", fake_release)

    handoff_claim_release.release_if_handoff({"id": "T1", "labels": []})
    time.sleep(0.05)  # give a (wrongly-)spawned thread a chance to run
    assert calls == []


def test_release_if_handoff_no_ops_for_none_task():
    # Must not raise; must not spawn any thread.
    handoff_claim_release.release_if_handoff(None)


def test_release_if_handoff_prefers_explicit_task_id_override(monkeypatch):
    """A caller-supplied task_id wins over whatever the fetched record echoes
    back, so a fetch that (for whatever reason) doesn't round-trip the id
    verbatim still releases the right claim."""
    calls = []
    done = threading.Event()

    def fake_release(task_id, *, worktree, timeout=15.0):
        calls.append(task_id)
        done.set()

    monkeypatch.setattr(handoff_claim_release, "_release_task_claim", fake_release)

    handoff_claim_release.release_if_handoff(
        {"id": "stale-echo", "labels": ["handoff"]}, task_id="T1",
    )
    assert _wait_for(done.is_set)
    assert calls == ["T1"]


def test_release_if_handoff_no_ops_without_any_resolvable_id(monkeypatch):
    calls = []

    def fake_release(task_id, *, worktree, timeout=15.0):
        calls.append(task_id)

    monkeypatch.setattr(handoff_claim_release, "_release_task_claim", fake_release)

    handoff_claim_release.release_if_handoff({"labels": ["handoff"]})
    time.sleep(0.05)
    assert calls == []
