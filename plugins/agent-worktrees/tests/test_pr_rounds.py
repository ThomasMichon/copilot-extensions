"""``pr rounds``: one round per reviewed head, and a guard that stops a loop that isn't converging."""

from __future__ import annotations

import json

import pytest

from agent_worktrees import pr_bar, pr_rounds
from agent_worktrees.pr_rounds import Round, guard

COPILOT = pr_bar.COPILOT_REVIEWER


def _body(open_, missed=0):
    tail = f"\n\n<details><summary>Previously missed ({missed})</summary></details>" if missed else ""
    return f"**{open_} open findings**{tail}"


def _review(head, at, body, *, author=COPILOT, state="COMMENTED", rid=None):
    return {"id": rid, "author": author, "state": state, "commit": head, "body": body, "at": at}


def _rounds(*metrics):
    return [Round(head=f"h{i}", at=f"2026-10-07T0{i}:00:00Z", open=m, missed=0 if m is not None else None)
            for i, m in enumerate(metrics)]


@pytest.mark.parametrize("metrics", [(5, 4, 3, 2, 1), (9, 7, 7, 6, 4)])
def test_a_loop_that_keeps_improving_does_not_trip(metrics):
    verdict, reason = guard(_rounds(*metrics))
    assert verdict == "continue" and f"round {len(metrics)} of 6" in reason


def test_a_plateau_trips_at_a_predictable_round():
    """Best 3 at round 1; rounds 2-4 never beat it: the guard stops at round 4."""
    assert guard(_rounds(3, 4))[0] == "continue"
    assert guard(_rounds(3, 4, 3))[0] == "continue"
    verdict, reason = guard(_rounds(3, 4, 3, 5))
    assert verdict == "plateau" and "best before: 3" in reason


def test_done_wins_when_the_latest_round_is_clean():
    assert guard(_rounds(3, 4, 3, 0))[0] == "done"


def test_the_round_cap():
    assert guard(_rounds(9, 8, 7, 6, 5, 4), max_rounds=6)[0] == "round_cap"
    assert guard(_rounds(9, 8, 7, 6, 5), max_rounds=6)[0] == "continue"


def test_unmeasured_rounds_are_excluded_never_zero():
    """A review that states no count is a round without a metric: not 'done', and not
    a sample in the plateau window."""
    assert guard(_rounds(3, None))[0] == "continue"
    assert guard(_rounds(3, 4, None, 3, None, 5), max_rounds=10)[0] == "plateau"
    assert guard(_rounds(3, 4, None, 3), max_rounds=10)[0] == "continue"


def test_plateau_passes_zero_turns_the_plateau_off():
    assert guard(_rounds(3, 3, 3, 3), plateau_passes=0)[0] == "continue"


def test_rounds_are_one_per_reviewed_head_from_its_latest_review_in_effect():
    snap = pr_bar.Snapshot(repo="o/r", number=1, head="c", reviews=[
        _review("a", "2026-10-07T01:00:00Z", _body(4)),
        _review("a", "2026-10-07T01:30:00Z", _body(3)),                     # same head, later: wins
        _review("b", "2026-10-07T02:00:00Z", _body(2, 1)),
        _review("b", "2026-10-07T02:30:00Z", _body(0), state="DISMISSED"),  # not in effect
        _review("c", "2026-10-07T03:00:00Z", _body(9), author="someone"),   # not the reviewer
        _review("c", "2026-10-07T03:00:00Z", "no count here", rid=7),       # unmeasured
    ])
    rounds = pr_rounds.rounds_of(snap, COPILOT)
    assert [(r.head, r.metric) for r in rounds] == [("a", 3), ("b", 3), ("c", None)]


def test_unreadable_reviews_are_unknown():
    snap = pr_bar.Snapshot(repo="o/r", number=1, errors={"reviews": "HTTP 502"})
    result = pr_rounds.evaluate(snap)
    assert (result.verdict, result.reason) == ("unknown", "HTTP 502")
    assert pr_rounds.EXIT[result.verdict] == 12


def test_the_cli_reports_the_guard_as_its_exit_code(monkeypatch, capsys):
    from agent_worktrees import pr_bar_cli, pr_cli

    reviews = [_review(f"h{i}", f"2026-10-07T0{i}:00:00Z", _body(m)) for i, m in enumerate((3, 4, 3, 5))]
    snap = pr_bar.Snapshot(repo="o/r", number=7, head="h3", reviews=reviews)
    monkeypatch.setattr(pr_bar_cli, "read_target", lambda operands, config, verb: (
        0, {"slug": "o/r", "number": 7, "snap": snap}))
    assert pr_cli.cmd_pr_dispatch(["rounds", "o/r", "7", "--json"]) == pr_rounds.EXIT["plateau"]
    payload = json.loads(capsys.readouterr().out)
    assert (payload["verdict"], payload["round"], payload["trend"]) == ("plateau", 4, [3, 4, 3, 5])
    assert pr_cli.cmd_pr_dispatch(["rounds", "o/r", "7", "--plateau-passes", "0"]) == 0
    assert "round 4 of 6" in capsys.readouterr().out
    assert pr_cli.cmd_pr_dispatch(["rounds", "o/r", "7", "--max-rounds", "0"]) == 2
