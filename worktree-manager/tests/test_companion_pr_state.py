"""Recorded PR associations and strict owner-backed point reads."""

import copy
import io

import pytest
from rich.console import Console
from rich.tree import Tree

from worktree_manager import companion_pr_state as cpr
from worktree_manager import engine_client as ec

HEAD = "a" * 40
ENDPOINT = "https://forge.example.test/api"


def row(pr_id="pr-1", number=7, **extra):
    value = {
        "pr_id": pr_id, "pr_revision": 2, "provider": "github", "repo": "owner/repo",
        "number": number, "url": f"https://forge.example.test/owner/repo/pull/{number}",
        "recorded_state": "active",
        "binding": {"available": True, "provider": "github", "repo": "owner/repo",
                    "api_base": "", "endpoint": ENDPOINT},
    }
    value.update(extra)
    return value


def fact(*rows, omitted=0, complete=True, available=True):
    return {"value": {
        "schema": "agent-worktrees.pr-associations", "version": 1, "project": "p",
        "worktree_id": "selected", "creation_nonce": "n", "available": available,
        "complete": complete, "associations": list(rows), "omitted": omitted,
    }, "confirmed": True, "observed_at": 1.0}


def snapshot(**extra):
    value = {
        "schema": "agent-worktrees.pr-snapshot", "version": 1, "provider": "github",
        "api_base": "", "repo": "owner/repo", "pr": 7, "endpoint": ENDPOINT,
        "pr_state": "open", "merged": False, "head_sha": HEAD, "base_ref": "dev",
        "base_sha": "", "mergeable": None, "checks_state": "pending",
        "checks_complete": False, "check_failures": [], "title": "t", "draft": False,
        "author": "a", "reviews": [{"id": 1, "state": "COMMENTED", "user": "r"}],
        "cursor": "c", "observation": None,
    }
    value.update(extra)
    return value


def association():
    return cpr.admitted(fact(row()))[0][0]


def test_supported_requires_exact_declaration():
    assert cpr.supported({"companion_pr_observations": copy.deepcopy(cpr.DECLARATION)})
    altered = copy.deepcopy(cpr.DECLARATION)
    altered["version"] = 2
    assert not cpr.supported({"companion_pr_observations": altered})
    assert not cpr.supported({})


def test_admission_rejects_unbound_duplicate_and_mismatched_rows():
    rows = [
        row("ok"),
        row("dup"), row("dup", number=8),
        row("unbound", binding={"available": False, "reason": "unresolved"}),
        row("foreign", binding={"available": True, "provider": "gitea", "repo": "owner/repo",
                                "endpoint": ENDPOINT}),
        row("no-number", number=None),
        row("bool-number", number=True),
    ]
    associations, rejected = cpr.admitted(fact(*rows))
    assert [item.pr_id for item in associations] == ["ok"]
    assert rejected == 6
    assert cpr.admitted(fact(row(), available=False)) == ([], 0)


def test_snapshot_argv_is_the_passive_owner_read_without_overrides():
    assert cpr.snapshot_argv(association()) == ["pr-watch", "snapshot", "owner/repo", "7", "--json"]


@pytest.mark.parametrize(("extra", "state"), [
    ({}, "open"),
    ({"pr_state": "closed"}, "closed-unmerged"),
    ({"pr_state": "closed", "merged": True}, "merged"),
])
def test_decode_maps_provider_state(extra, state):
    read = cpr.decode_snapshot(snapshot(**extra), association(), observed_at=5.0)
    assert read.state == state and read.observed_at == 5.0
    assert read.last_review == "COMMENTED" and read.reviews == 1


@pytest.mark.parametrize(("extra", "reason"), [
    ({"schema": "other"}, "contract"),
    ({"version": 2}, "contract"),
    ({"repo": "owner/other"}, "foreign provider"),
    ({"provider": "gitea"}, "foreign provider"),
    ({"pr": 8}, "foreign PR"),
    ({"pr": True}, "foreign PR"),
    ({"endpoint": ""}, "endpoint unknown"),
    ({"endpoint": "https://other.example.test/api"}, "foreign endpoint"),
    ({"pr_state": "draft"}, "malformed"),
    ({"merged": "no"}, "malformed"),
    ({"head_sha": "main"}, "malformed"),
    ({"checks_state": "green"}, "malformed"),
    ({"mergeable": "yes"}, "malformed"),
    ({"reviews": [{"id": 1}]}, "malformed"),
    ({"merged": True}, "contradictory"),
    ({"checks_state": ["success"]}, "malformed"),
    ({"checks_state": {"state": "success"}}, "malformed"),
])
def test_decode_refuses_inadmissible_replies(extra, reason):
    with pytest.raises(cpr.PointReadError, match=reason):
        cpr.decode_snapshot(snapshot(**extra), association(), observed_at=1.0)


def test_point_read_failure_is_explicit_unavailability(monkeypatch):
    def fail(*args, **kwargs):
        raise ec.EngineError("pr-watch snapshot failed (exit 1): rate limited")

    monkeypatch.setattr(cpr.ec, "run_json", fail)
    read = cpr.point_read(association(), clock=lambda: 9.0)
    assert read.state == "unavailable" and "rate limited" in read.reason
    monkeypatch.setattr(cpr.ec, "run_json", lambda *a, **kw: snapshot(repo="owner/other"))
    assert cpr.point_read(association(), clock=lambda: 9.0).state == "unavailable"


def test_point_read_bounds_actual_stdout_bytes_before_parsing(monkeypatch):
    import json

    compact = json.dumps(snapshot())
    padded = compact + " " * (cpr.MAX_SNAPSHOT_BYTES - len(compact) + 1)
    monkeypatch.setattr(cpr.ec, "_run", lambda *a, **kw: padded)
    read = cpr.point_read(association(), clock=lambda: 9.0)
    assert read.state == "unavailable" and "exceeded" in read.reason
    monkeypatch.setattr(cpr.ec, "_run", lambda *a, **kw: padded[:-1])
    assert cpr.point_read(association(), clock=lambda: 9.0).state == "open"


@pytest.mark.parametrize("bad_id", [[], {}])
def test_render_reports_unhashable_ids_as_not_admitted(bad_id):
    root = Tree("selected")
    cpr.render(root, declared=True, fact=fact(row(pr_id=bad_id)), error=None,
               selected=None, read=None, busy=False)
    console = Console(width=200, record=True, file=io.StringIO())
    console.print(root)
    text = console.export_text()
    assert "Point read unavailable: not admitted" in text
    assert "1 association(s) not admitted" in text


def test_read_associations_requires_declared_selected_fact(monkeypatch):
    calls = []

    def reply(project, args, **kwargs):
        calls.append(args)
        return {"worktree_id": "selected", "companion_pr_observations": cpr.DECLARATION,
                "facts": {"pr_associations": fact(row())}}

    monkeypatch.setattr(cpr.ec, "run_json", reply)
    assert cpr.read_associations("selected")["value"]["associations"][0]["pr_id"] == "pr-1"
    assert calls == [["worktree-status-bundle", "--worktree", "selected", "--pr-associations", "--json"]]
    with pytest.raises(ec.EngineError, match="selected worktree"):
        cpr.read_associations("other")


def plain(**kwargs):
    root = Tree("root")
    cpr.render(root, **{"declared": True, "fact": None, "error": None, "selected": None,
                        "read": None, "busy": False, **kwargs})
    console = Console(width=200, record=True, file=io.StringIO())
    console.print(root)
    return console.export_text()


def test_render_keeps_record_state_distinct_and_labels_point_reads():
    assert "Not declared by this engine" in plain(declared=False)
    text = plain(fact=fact(row(), row("unbound", binding={"available": False, "reason": "unresolved"}),
                           omitted=3, complete=False))
    assert "recorded lifecycle: active (record, not current openness or approval)" in text
    assert "Not read: select it to inspect once" in text
    assert "Point read unavailable: unresolved" in text
    assert "3 association(s) omitted" in text
    selected = association()
    read = cpr.decode_snapshot(snapshot(pr_state="closed"), selected, observed_at=0.0)
    text = plain(fact=fact(row()), selected=selected, read=read)
    assert "Point read: closed without merge" in text and "not continuously confirmed" in text
    assert "Observed diagnostics only (no consent, gate or action)" in text
    failed = cpr.PointRead(selected, "unavailable", 0.0, reason="endpoint unknown")
    assert "unknown/unavailable" in plain(fact=fact(row()), selected=selected, read=failed)
    assert "Unavailable: boom" in plain(error="boom")



def test_revalidated_read_pins_the_declaring_runtime(monkeypatch):
    association = cpr.admitted(fact(row()))[0][0]
    seen = []

    def run_json(project, argv, **kw):
        seen.append(kw.get("engine_command"))
        if len(seen) == 1:
            return {"worktree_id": "selected", "companion_pr_observations": dict(cpr.DECLARATION),
                    "facts": {"pr_associations": fact(row())}}
        return snapshot()

    monkeypatch.setattr(cpr.ec, "run_json", run_json)
    refreshed, read = cpr.revalidated_read(association, "selected", engine_command=["pinned", "--x"])
    assert seen == [["pinned", "--x"], ["pinned", "--x"]]
    assert refreshed is not None and read.state == "open"

    _fact, read = cpr.revalidated_read(association, "selected", engine_command=None)
    assert read.state == "unavailable" and len(seen) == 2
