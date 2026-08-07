"""Tests for the pr-* reminder wiring helpers (_pr_reminder_for / _emit_pr_reminder).

The pure reminder logic lives in ``pr_contract`` (covered by test_pr_contract);
these cover the thin CLI seam that every pr-* verb uses to surface it.
"""

from __future__ import annotations

from types import SimpleNamespace

import agent_worktrees.__main__ as m


def _self_merge_config():
    prc = SimpleNamespace(
        enabled=True, required=True, provider="github", automerge_label="",
        self_approve=True, reviewer="copilot", review_blocking=False,
        review_latency_hint="~2m", merge_actor="", conflict_retriggers_review=True,
    )
    return SimpleNamespace(default_repo=SimpleNamespace(pr=prc))


def test_pr_reminder_for_builds_self_merge_reminder():
    r = m._pr_reminder_for(_self_merge_config(), "create-pr")
    assert r is not None
    assert r.profile == "pr-self-merge"
    assert "pr-merge" in r.next_step and "--now" in r.next_step


def test_pr_reminder_for_fails_open_on_bad_config():
    # No repo / classification failure -> None, never raises (a reminder must
    # never perturb the verb it rides along with).
    assert m._pr_reminder_for(SimpleNamespace(default_repo=None), "pr-status") is None


def test_emit_pr_reminder_json_adds_node():
    r = m._pr_reminder_for(_self_merge_config(), "pr-status")
    result: dict = {}
    m._emit_pr_reminder(r, use_json=True, result=result)
    assert "reminder" in result
    assert result["reminder"]["profile"] == "pr-self-merge"
    for key in ("next", "waiting_on", "use_instead", "cautions", "ok"):
        assert key in result["reminder"]


def test_emit_pr_reminder_none_is_noop():
    result: dict = {}
    m._emit_pr_reminder(None, use_json=True, result=result)
    assert result == {}


def test_emit_pr_reminder_human_no_result_mutation():
    r = m._pr_reminder_for(_self_merge_config(), "push-changes")
    # Human mode prints to stderr; must not require/mutate a result dict.
    m._emit_pr_reminder(r, use_json=False, result=None)
