"""agent-dispatch attention: the ``bridge`` and ``pr`` sources, read through
their owners' CLIs (faked here), and their CLI wiring."""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_dispatch import __main__ as m
from agent_dispatch import attention_cli
from agent_dispatch import attention_contract as ac
from agent_dispatch import attention_siblings as sib
from agent_dispatch import attention_sources as srcs
from agent_dispatch import attention_store
from agent_dispatch.attention_store import FirstObserved

T1, T2 = "2026-10-07T11:00:00+00:00", "2026-10-07T12:00:00+00:00"
BRIDGE, WT = ["agent-bridge"], ["agent-worktrees"]


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from agent_dispatch import procutil

    monkeypatch.setattr(srcs, "registry_path", lambda: tmp_path / "attention-sources.json")
    monkeypatch.setattr(attention_store, "default_path", lambda: tmp_path / "attention-observed.json")
    monkeypatch.setattr(procutil, "agent_bridge_launch_prefix", lambda: None)
    monkeypatch.setattr(procutil, "agent_worktrees_launch_prefix", lambda: None)


class Fake:
    """A runner that answers each argv from ``route(argv)``: ``(exit, body)`` or
    ``None`` for a call that didn't finish."""

    def __init__(self, route):
        self.route, self.calls = route, []

    def __call__(self, argv, timeout, max_output=None, env=None, **_kw):
        argv = list(argv)
        self.calls.append(argv)
        answer = self.route(argv)
        if answer is None:
            return None
        code, body = answer
        return subprocess.CompletedProcess(argv, code, body if isinstance(body, str) else json.dumps(body), "")


# -- bridge ------------------------------------------------------------------------


def _owned(sid, wt, project="proj", status="running", target_type="local"):
    return {"session_id": sid, "worktree_id": wt, "project": project, "status": status, "target_type": target_type}


def _live(sid, wt, repo="proj", machine="m1"):
    return {"session_id": sid, "worktree_id": wt, "repo": repo, "machine": machine, "status": "live"}


def _answer(reason=None, sid="s1", registry="bridge", availability=None, detail=None):
    return {"ref": "", "status": "ok", "reason": reason, "session_id": sid, "registry": registry,
            "availability": availability or ("available" if reason else None), "detail": detail}


def _bridge(sessions=(), live=(), attention=None, presence=None, sessions_code=0, live_code=0):
    """``attention``: {worktree handle: entry}; ``presence``: {session id: presence}."""
    attention, presence = attention or {}, presence or {}

    def route(argv):
        verb = argv[2]
        if verb == "sessions":
            return sessions_code, list(sessions)
        if verb == "live-sessions":
            return live_code, list(live)
        if verb == "attention":
            return 0, {"schema": 1, "sessions": [{**attention.get(h, {"status": "not_found"}), "ref": h}
                                                 for h in argv[3:]]}
        if verb == "presence":
            p = presence.get(argv[3], {"state": "idle", "confidence": "scanned"})
            return 0, {"session_id": argv[3], "presence": p}
        raise AssertionError(argv)

    return Fake(route)


def _read_bridge(fake, include_remote=False, machine="m1"):
    return sib.read_bridge(T1, prefix=BRIDGE, machine=machine, include_remote=include_remote, run=fake)


@pytest.mark.parametrize("reason", ["permission_required", "policy_required", "input_required"])
def test_a_managed_session_parked_on_an_operator_decision_is_awaiting_input(reason):
    fake = _bridge(sessions=[_owned("s1", "w1")], attention={"w1": _answer(reason, detail="run it")})
    result = _read_bridge(fake)
    (item,) = result["items"]
    assert (item["display_state"], item["confidence"], item["entity_ref"]) == (
        "awaiting_input", "reported", "wt:m1/proj/w1")
    assert "run it" in item["reason"] and result["status"] == "ok"
    ac.validate_item({**item, "created_at": T1})


def test_a_registered_interactive_session_is_a_candidate_too():
    fake = _bridge(live=[_live("cli-1", "w2")],
                   attention={"w2": _answer("permission_required", sid="cli-1", registry="live")})
    (item,) = _read_bridge(fake)["items"]
    assert item["entity_ref"] == "wt:m1/proj/w2" and item["actions"][0]["argv"] == ["agent-bridge", "result", "cli-1"]


@pytest.mark.parametrize("reason, state, unsure", [
    ("input_required", "awaiting_input", 0), ("permission_required", "awaiting_input", 0),
    ("policy_required", "awaiting_input", 0), ("failed", "failed", 0), ("unreachable", "failed", 0),
    ("contract_changed", None, 1), ("a-reason-from-a-newer-bridge", None, 1),
    ("turn_complete", None, 0), ("turn_cancelled", None, 0), ("stopped", None, 0), ("ended", None, 0),
    (None, None, 0),
])
def test_every_attention_reason_maps(reason, state, unsure):
    result = _read_bridge(_bridge(sessions=[_owned("s1", "w1")], attention={"w1": _answer(reason)}))
    assert [i["display_state"] for i in result["items"]] == ([state] if state else [])
    assert result["uncertain"] == unsure


def test_an_unreadable_attention_or_a_request_lost_to_a_restart_is_uncertain():
    fake = _bridge(sessions=[_owned("s1", "w1"), _owned("s2", "w2")],
                   attention={"w1": {"status": "error", "error": "boom"},
                              "w2": _answer("input_required", sid="s2", availability="unknown_after_restart")})
    result = _read_bridge(fake)
    assert (result["items"], result["status"], result["uncertain"]) == ([], "uncertain", 2)


def test_an_attention_command_that_fails_outright_leaves_every_candidate_uncertain():
    def route(argv):
        if argv[2] == "attention":
            return 2, "agent-bridge: invalid choice: 'attention'"
        return (0, [_owned("s1", "w1")]) if argv[2] == "sessions" else (0, [])

    result = _read_bridge(Fake(route))
    assert (result["status"], result["uncertain"]) == ("uncertain", 1)


def test_a_permission_and_transcript_presence_for_one_session_coalesce():
    fake = _bridge(sessions=[_owned("s1", "w1")], attention={"w1": _answer("permission_required")},
                   presence={"s1": {"state": "awaiting_input", "confidence": "scanned", "reason": "ask_user open"}})
    (item,) = _read_bridge(fake)["items"]
    assert item["confidence"] == "reported"
    assert "permission required" in item["reason"] and "transcript" in item["reason"]


def test_coalescing_is_independent_of_read_order():
    reported = sib._session_item("wt:m/p/w", "awaiting_input", "reported", "permission required", "running",
                                 "s1", T1)
    scanned = sib._session_item("wt:m/p/w", "awaiting_input", "scanned", "its transcript is waiting", "running",
                                "s1", T1)
    assert sib._coalesce([reported, scanned]) == sib._coalesce([scanned, reported])


def test_presence_alone_yields_a_scanned_item_and_unknown_presence_is_uncertain():
    fake = _bridge(sessions=[_owned("s1", "w1"), _owned("s2", "w2")],
                   attention={"w1": _answer(None), "w2": _answer(None, sid="s2")},
                   presence={"s1": {"state": "awaiting_input", "confidence": "heuristic"},
                             "s2": {"state": "unknown", "confidence": "scanned"}})
    result = _read_bridge(fake)
    assert [(i["entity_ref"], i["confidence"]) for i in result["items"]] == [("wt:m1/proj/w1", "heuristic")]
    assert result["uncertain"] == 1


def test_a_remote_transcript_is_read_only_when_opted_in():
    fake = _bridge(sessions=[_owned("s1", "w1", target_type="ssh")], attention={"w1": _answer(None)},
                   presence={"s1": {"state": "awaiting_input", "confidence": "scanned"}})
    assert _read_bridge(fake)["items"] == []
    assert not [c for c in fake.calls if "presence" in c]
    assert len(_read_bridge(fake, include_remote=True)["items"]) == 1


def test_a_local_command_sessions_transcript_is_read_by_default():
    fake = _bridge(sessions=[_owned("s1", "w1", target_type="command")], attention={"w1": _answer(None)},
                   presence={"s1": {"state": "awaiting_input", "confidence": "scanned"}})
    assert len(_read_bridge(fake)["items"]) == 1


def test_one_session_in_both_registries_is_one_item():
    fake = _bridge(sessions=[_owned("s1", "w1")], live=[_live("cli-1", "w1")],
                   attention={"w1": _answer("input_required", sid="cli-1", registry="live")})
    result = _read_bridge(fake)
    assert len(result["items"]) == 1 and [c for c in fake.calls if c[2] == "attention"][0][3:] == ["w1"]


@pytest.mark.parametrize("failing", ["sessions", "live"])
def test_a_failed_listing_of_either_registry_fails_the_source(failing):
    fake = _bridge(sessions=[_owned("s1", "w1")], attention={"w1": _answer("input_required")},
                   sessions_code=1 if failing == "sessions" else 0, live_code=1 if failing == "live" else 0)
    assert _read_bridge(fake)["status"] == "failed"


def test_a_successor_keeps_the_entity_and_its_first_observed_time(tmp_path):
    store = FirstObserved(tmp_path / "observed.json")

    def read(sid, read_at):
        fake = _bridge(sessions=[_owned(sid, "w1")], attention={"w1": _answer("input_required", sid=sid)})
        return srcs.collect({"bridge": lambda at: sib.read_bridge(at, prefix=BRIDGE, machine="m1", run=fake)},
                            timeouts={"bridge": 30}, selected=None, config_errors=[], store=store, read_at=read_at)

    first, second = read("old-session", T1)["items"][0], read("successor", T2)["items"][0]
    assert (first["id"], first["created_at"]) == (second["id"], second["created_at"])
    assert second["actions"][0]["argv"][-1] == "successor"
    assert "old-session" not in second["id"] and "successor" not in second["id"]


def test_one_worktree_id_in_two_projects_is_two_entities():
    candidates, _ = sib._bridge_candidates([_owned("s1", "w1", project="a")], [_live("cli", "w1", repo="b")], "m1")
    assert sorted(candidates) == ["wt:m1/a/w1", "wt:m1/b/w1"]


def test_a_handle_shared_by_two_projects_is_never_read_as_either():
    """The bridge resolves a bare handle, so its one answer can't be told apart:
    both candidates are uncertain, and an unambiguous one is still read."""
    fake = _bridge(sessions=[_owned("s1", "w1", project="a"), _owned("s3", "w3", project="a")],
                   live=[_live("cli", "w1", repo="b")],
                   attention={"w1": _answer("input_required"), "w3": _answer("permission_required", sid="s3")})
    result = _read_bridge(fake)
    assert [i["entity_ref"] for i in result["items"]] == ["wt:m1/a/w3"]
    assert (result["status"], result["uncertain"]) == ("uncertain", 2)
    assert [c for c in fake.calls if c[2] == "attention"][0][3:] == ["w3"]


@pytest.mark.parametrize("row, remote", [
    ({"target_type": "local"}, False), ({"target_type": "command"}, False), ({"target_type": None}, False),
    ({"target_type": "ssh"}, True), ({"target_type": "command", "agent_name": "codespace:cs-1"}, True),
    ({"target_type": "command", "agent_name": "container:c-1"}, True),
])
def test_transcript_locality_matches_the_bridges_own_classification(row, remote):
    assert sib._is_remote(row) is remote


def test_a_session_no_managed_worktree_hosts_is_uncertain_not_an_item():
    fake = _bridge(sessions=[{"session_id": "s9", "status": "running", "project": None, "worktree_id": None},
                             _owned("s8", "w8", status="ended")],
                   live=[{"session_id": "cli-9", "status": "live", "machine": "m1", "repo": None,
                          "worktree_id": None}])
    result = _read_bridge(fake)
    assert (result["items"], result["uncertain"]) == ([], 2)
    assert not [c for c in fake.calls if c[2] == "attention"]


# -- pr ----------------------------------------------------------------------------


def _bar(state="OPEN", verdict="failed"):
    return {"repo": "", "number": 0, "head": "abc", "state": state, "verdict": verdict, "observed_at": T1,
            "clauses": [{"id": "ci_green", "status": "failed" if verdict == "failed" else "met",
                         "evidence": "lint failed", "error": ""}]}


def _projects(*entries):
    return {"schema": 1, "repo": None, "state": "all", "projects": list(entries)}


def _project(name, *prs, status="ok", **extra):
    return {"project": name, "status": status, "prs": [
        {"worktree_id": p[0], "authority": p[1], "repo": p[2], "number": p[3], "state": p[4]} for p in prs], **extra}


def _pr_fake(envelope, bars, code=0):
    """``bars``: {(project, repo, number): bar or None (no answer) or str (not JSON)}."""

    def route(argv):
        if argv[1] == "claims":
            return code, envelope
        assert argv[1] == "-p" and argv[3:5] == ["pr", "bar"] and argv[-1] == "--json"
        bar = bars[(argv[2], argv[5], int(argv[6]))]
        if bar is None or isinstance(bar, str):
            return None if bar is None else (2, bar)
        return {"met": 0, "merged": 0, "pending": 10, "failed": 11, "unknown": 12}[bar["verdict"]], bar

    return Fake(route)


def _read_pr(fake):
    return sib.read_pr(T1, prefix=WT, run=fake)


def test_two_projects_each_with_a_failing_pr_give_two_items():
    fake = _pr_fake(_projects(_project("a", ("w1", "github.com", "o/x", 1, "open")),
                              _project("b", ("w2", "github.com", "o/y", 2, "open"))),
                    {("a", "o/x", 1): _bar(), ("b", "o/y", 2): _bar()})
    result = _read_pr(fake)
    assert sorted(i["entity_ref"] for i in result["items"]) == ["github.com/o/x#1", "github.com/o/y#2"]
    item = result["items"][0]
    assert item["actions"][0]["argv"][:3] == ["agent-worktrees", "-p", item["actions"][0]["argv"][2]]
    assert item["actions"][1]["verb"] == "open" and "ci_green" in item["reason"]
    ac.validate_item({**item, "created_at": T1})


def test_a_project_that_cant_be_enumerated_fails_the_source():
    fake = _pr_fake(_projects(_project("a", ("w1", "github.com", "o/x", 1, "open")),
                              _project("b", status="failed", error="config gone")), {})
    result = _read_pr(fake)
    assert result["status"] == "failed" and "config gone" in result["error"]


def test_the_live_state_decides_never_the_record():
    fake = _pr_fake(_projects(_project("a", ("w1", "github.com", "o/x", 1, "open"),
                                       ("w2", "github.com", "o/x", 2, "closed"))),
                    {("a", "o/x", 1): _bar(state="CLOSED"), ("a", "o/x", 2): _bar(state="OPEN")})
    assert [i["entity_ref"] for i in _read_pr(fake)["items"]] == ["github.com/o/x#2"]


def test_one_slug_under_two_authorities_never_dedupes():
    fake = _pr_fake(_projects(_project("a", ("w1", "github.com", "o/x", 42, "open")),
                              _project("b", ("w2", "ghes.example.com", "o/x", 42, "open"),
                                       ("w3", "dev.azure.com/orgA", "p/x", 42, "open"),
                                       ("w4", "dev.azure.com/orgB", "p/x", 42, "open"))),
                    {("a", "o/x", 42): _bar(), ("b", "o/x", 42): _bar(), ("b", "p/x", 42): _bar()})
    refs = sorted(i["entity_ref"] for i in _read_pr(fake)["items"])
    assert refs == ["dev.azure.com/orgA/p/x#42", "dev.azure.com/orgB/p/x#42", "ghes.example.com/o/x#42",
                    "github.com/o/x#42"]
    assert len({i for i in refs}) == 4
    assert not [i for i in refs if "http" in i]


def test_a_worktree_with_two_prs_gives_one_item_for_the_failing_one():
    fake = _pr_fake(_projects(_project("a", ("w1", "github.com", "o/x", 1, "open"),
                                       ("w1", "github.com", "o/x", 2, "closed"))),
                    {("a", "o/x", 1): _bar(verdict="met"), ("a", "o/x", 2): _bar()})
    assert [i["entity_ref"] for i in _read_pr(fake)["items"]] == ["github.com/o/x#2"]


def test_one_pr_tracked_by_two_worktrees_is_read_once():
    fake = _pr_fake(_projects(_project("a", ("w1", "github.com", "O/X", 1, "open"),
                                       ("w2", "github.com", "o/x", 1, "open"))), {("a", "O/X", 1): _bar()})
    assert len(_read_pr(fake)["items"]) == 1
    assert len([c for c in fake.calls if "bar" in c]) == 1


def test_unresolvable_records_and_unknown_bars_are_uncertain_and_merged_records_are_skipped():
    fake = _pr_fake(_projects(_project("a", ("w1", "github.com", "", 1, "open"),
                                       ("w2", "github.com", "o/x", None, "creating"),
                                       ("w3", None, "o/x", 3, "open"),
                                       ("w4", "github.com", "o/x", 4, "open"),
                                       ("w5", "github.com", "o/x", 5, "open"),
                                       ("w6", "github.com", "o/x", 6, "merged"), unreadable=1)),
                    {("a", "o/x", 4): _bar(verdict="unknown", state=""), ("a", "o/x", 5): "not json"})
    result = _read_pr(fake)
    assert (result["items"], result["status"], result["uncertain"]) == ([], "uncertain", 6)
    assert not [c for c in fake.calls if c[-2:] == ["6", "--json"]]


def test_an_older_agent_worktrees_without_the_envelope_fails_the_source():
    fake = _pr_fake({"repo": None, "matches": []}, {})
    assert _read_pr(fake)["status"] == "failed"
    assert _read_pr(_pr_fake(_projects(), {}, code=3))["status"] == "failed"


# -- the CLI -----------------------------------------------------------------------


def test_a_selected_source_whose_plugin_isnt_installed_is_a_usage_error(monkeypatch, capsys):
    monkeypatch.setattr(m, "_client", lambda args: pytest.fail("nothing may be read"))
    for name in ("bridge", "pr"):
        args = m.build_parser().parse_args(["attention", "--source", name])
        assert args.func(args) == 2 and "not installed" in capsys.readouterr().err


def test_missing_siblings_are_listed_disabled_and_never_degrade(monkeypatch, capsys):
    class _Client:
        def __enter__(self):
            return self

        def __exit__(self, *exc):
            return False

        def list(self, **kw):
            return []

    monkeypatch.setattr(m, "_client", lambda args: _Client())
    args = m.build_parser().parse_args(["attention", "--json"])
    assert args.func(args) == 0
    env = json.loads(capsys.readouterr().out)
    assert env["status"] == "clear"
    assert {s["name"]: s["status"] for s in env["sources"]} == {"bridge": "disabled", "dispatch": "ok",
                                                                "pr": "disabled"}


def test_include_remote_parses_on_attention_and_after_next():
    assert m.build_parser().parse_args(["attention", "--include-remote"]).include_remote is True
    assert m.build_parser().parse_args(["attention", "next", "--include-remote", "--json"]).include_remote is True


def _cell(tmp_path, monkeypatch, *installed):
    """A same-cell standalone layout: ``<cell>/plugins/agent-dispatch`` under an
    explicit installation context, with ``installed`` siblings' receipts."""
    from agent_dispatch import procutil

    plugins = tmp_path / "cell" / "plugins"
    for name in ("agent-dispatch", *installed):
        (plugins / name).mkdir(parents=True)
        (plugins / name / "install.json").write_text("{}", encoding="utf-8")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(plugins / "agent-dispatch" / "install.json"))
    monkeypatch.setattr(procutil, "install_dir", lambda: plugins / "agent-dispatch")
    # The real builders: with an explicit context they always return a prefix.
    monkeypatch.setattr(procutil, "agent_bridge_launch_prefix",
                        lambda: procutil._sibling_runtime_launch_prefix("agent-bridge", "agent_bridge", "agent-bridge"))
    monkeypatch.setattr(procutil, "agent_worktrees_launch_prefix", lambda: procutil._sibling_runtime_launch_prefix(
        "agent-worktrees", "agent_worktrees", "agent-worktrees"))


def test_a_sibling_absent_from_an_explicit_cell_is_disabled_not_run(tmp_path, monkeypatch, capsys):
    _cell(tmp_path, monkeypatch, "agent-worktrees")
    readers, _ = attention_cli._sibling_readers(m.build_parser().parse_args(["attention"]))
    assert readers["bridge"] is attention_cli._disabled
    assert readers["pr"] is not attention_cli._disabled  # installed: read (validated at execution)
    monkeypatch.setattr(m, "_client", lambda args: pytest.fail("nothing may be read"))
    args = m.build_parser().parse_args(["attention", "--source", "bridge"])
    assert args.func(args) == 2 and "not installed" in capsys.readouterr().err


def test_an_explicit_context_outside_a_cell_layout_is_never_hidden_as_absence(tmp_path, monkeypatch):
    from agent_dispatch import procutil

    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "bogus")
    monkeypatch.setattr(procutil, "install_dir", lambda: tmp_path / "somewhere" / "agent-dispatch")
    assert procutil.sibling_absent("agent-bridge") is False


def test_a_command_source_cant_take_a_builtin_sibling_name():
    assert "built-in" in srcs.registration_error("pr", {"argv": ["/bin/x"]})
    assert "built-in" in srcs.registration_error("bridge", {"argv": ["/bin/x"]})
