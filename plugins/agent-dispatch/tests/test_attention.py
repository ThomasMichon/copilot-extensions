"""agent-dispatch attention: contract, ordering, dedupe, first-observed times,
aggregate status, the dispatch source, command sources and the CLI."""

from __future__ import annotations

import json
import os
import subprocess
import sys

import pytest

from agent_dispatch import __main__ as m
from agent_dispatch import attention_contract as ac
from agent_dispatch import attention_sources as srcs
from agent_dispatch import attention_store
from agent_dispatch.attention_store import FirstObserved

T0, T1, T2 = "2026-10-07T10:00:00+00:00", "2026-10-07T11:00:00+00:00", "2026-10-07T12:00:00+00:00"
NOW = 1791370800.0  # T1 as epoch seconds


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from agent_dispatch import procutil

    monkeypatch.setattr(srcs, "registry_path", lambda: tmp_path / "attention-sources.json")
    monkeypatch.setattr(attention_store, "default_path", lambda: tmp_path / "attention-observed.json")
    # The bridge and pr sources read installed siblings; these tests run without them.
    monkeypatch.setattr(procutil, "agent_bridge_launch_prefix", lambda: None)
    monkeypatch.setattr(procutil, "agent_worktrees_launch_prefix", lambda: None)


def _item(source="s", entity="task", ref="t1", state="awaiting_input", created=T0, **kw):
    item = ac.new_item(source=source, entity=entity, entity_ref=ref, lifecycle_state=kw.pop("lifecycle", "started"),
                       display_state=state, reason=kw.pop("reason", f"{ref} needs you"), created_at=created,
                       updated_at=created, **kw)
    return item


def _ok(*items, status="ok", uncertain=0):
    return lambda read_at: {"items": [dict(i) for i in items], "status": status, "uncertain": uncertain}


def _collect(readers, store, selected=None, config_errors=(), read_at=T1, read_token=None):
    from datetime import datetime

    # Tests order reads by read_at unless a token says otherwise.
    token = read_token if read_token is not None else int(datetime.fromisoformat(read_at).timestamp() * 1e9)
    return srcs.collect(readers, timeouts={}, selected=selected, config_errors=list(config_errors),
                        store=store, read_at=read_at, read_token=token)


# -- contract ---------------------------------------------------------------------


def test_order_is_severity_then_age_then_id():
    items = [_item(ref="b", state="review", created=T0), _item(ref="a", state="failed", created=T2),
             _item(ref="c", state="awaiting_input", created=T1), _item(ref="d", state="awaiting_input", created=T1)]
    assert [i["entity_ref"] for i in ac.dedupe(items)] == ["a", "c", "d", "b"]


def test_one_entity_from_two_sources_dedupes_to_the_worst_with_the_rest_in_also():
    pr = "github.com/o/r#42"
    worse = _item(source="a", entity="pr", ref=pr, state="failed", created=T2)
    milder = _item(source="b", entity="pr", ref=pr, state="review", created=T0)
    for order in ([worse, milder], [milder, worse]):
        (only,) = ac.dedupe(order)
        assert only["source"] == "a" and [x["source"] for x in only["also"]] == ["b"]
        assert only["created_at"] == T0  # the earliest of the group


def test_a_task_and_a_session_sharing_an_id_never_collide():
    assert len(ac.dedupe([_item(entity="task", ref="x"), _item(entity="session", ref="x")])) == 2


def test_an_equal_severity_tie_resolves_the_same_in_any_adapter_order():
    a, b = _item(source="a", ref="r"), _item(source="b", ref="r")
    assert ac.dedupe([a, b])[0]["source"] == ac.dedupe([b, a])[0]["source"] == "a"


@pytest.mark.parametrize("entity,expected", [("task", "task"), ("login", "x.foo.login"), ("x.foo.login", "x.foo.login")])
def test_custom_entities_canonicalize_once(entity, expected):
    assert ac.canonical_entity(entity, "foo") == expected


@pytest.mark.parametrize("entity", ["x.other.login", "x.foo.", "a.b", ""])
def test_a_foreign_or_malformed_entity_is_rejected(entity):
    with pytest.raises(ac.ContractError):
        ac.canonical_entity(entity, "foo")


@pytest.mark.parametrize("action", [{"verb": "click", "argv": ["x"]}, {"verb": "x.other.go", "argv": ["x"]},
                                    {"verb": "show", "argv": []}])
def test_invalid_actions_invalidate_the_item(action):
    with pytest.raises(ac.ContractError):
        _item(source="foo", actions=[action])
    assert _item(source="foo", actions=[{"verb": "x.foo.go", "argv": ["go"]}])


def test_a_queue_item_has_no_lifecycle_and_every_item_carries_its_schema():
    item = _item(entity="queue", ref="o/r", state="stalled", lifecycle=None)
    assert item["lifecycle_state"] is None and item["schema"] == 1
    assert json.loads(json.dumps(item)) == item


@pytest.mark.parametrize("statuses,uncertain,items,expected", [
    (["ok"], 0, 0, "clear"), (["ok"], 0, 1, "attention"), (["failed"], 0, 0, "degraded"),
    (["uncertain"], 2, 1, "partial"), (["failed", "uncertain"], 1, 1, "degraded"), (["disabled", "ok"], 0, 0, "clear")])
def test_aggregate_status(statuses, uncertain, items, expected):
    sources = [{"name": str(i), "status": s, "uncertain": uncertain} for i, s in enumerate(statuses)]
    assert ac.aggregate_status(sources, [], [_item()] * items) == expected


def test_a_config_error_degrades_an_otherwise_clear_read():
    assert ac.aggregate_status([{"name": "d", "status": "ok"}], [{"name": "x", "error": "bad"}], []) == "degraded"


# -- first-observed times ------------------------------------------------------------


def _observed(store, source, result_fn, read_at):
    env = _collect({source: result_fn}, store, read_at=read_at)
    return {i["entity_ref"]: i["created_at"] for i in env["items"]}


def test_a_source_that_only_observes_keeps_its_time_across_processes(tmp_path):
    path = tmp_path / "obs.json"
    item = {**_item(), "created_at": None}
    assert _observed(FirstObserved(path), "s", _ok(item), T0)["t1"] == T0
    assert _observed(FirstObserved(path), "s", _ok(item), T2)["t1"] == T0  # a fresh instance, same store


def test_an_outage_and_recovery_keep_the_time(tmp_path):
    store, item = FirstObserved(tmp_path / "o.json"), {**_item(), "created_at": None}
    _observed(store, "s", _ok(item), T0)
    _collect({"s": lambda r: ac.command_failure("down")}, store, read_at=T1)
    _collect({"s": _ok(status="uncertain", uncertain=1)}, store, read_at=T1)
    assert _observed(store, "s", _ok(item), T2)["t1"] == T0


def test_an_ok_read_without_the_item_ends_it(tmp_path):
    store, item = FirstObserved(tmp_path / "o.json"), {**_item(), "created_at": None}
    _observed(store, "s", _ok(item), T0)
    _collect({"s": _ok()}, store, read_at=T1)
    assert _observed(store, "s", _ok(item), T2)["t1"] == T2


def test_one_sources_ok_omission_never_clears_anothers_evidence(tmp_path):
    store = FirstObserved(tmp_path / "o.json")
    a = {**_item(source="a", entity="pr", ref="h/o/r#1"), "created_at": None}
    b = {**_item(source="b", entity="pr", ref="h/o/r#1"), "created_at": None}
    _collect({"a": _ok(a), "b": _ok(b)}, store, read_at=T0)
    env = _collect({"a": _ok(), "b": _ok(b)}, store, read_at=T2)
    assert env["items"][0]["created_at"] == T0


# -- the envelope --------------------------------------------------------------------


def test_the_envelope_shape_and_a_selective_read(tmp_path):
    store = FirstObserved(tmp_path / "o.json")
    readers = {"dispatch": _ok(), "zeta": lambda r: ac.command_failure("down")}
    full = _collect(readers, store)
    assert list(full) == ["schema", "status", "read_at", "selected", "sources", "config_errors", "items"]
    assert [s["name"] for s in full["sources"]] == ["dispatch", "zeta"] and full["status"] == "degraded"
    scoped = _collect(readers, store, selected=["dispatch"], config_errors=[{"name": "foo", "error": "bad"}])
    assert scoped["selected"] == ["dispatch"] and [s["name"] for s in scoped["sources"]] == ["dispatch"]
    assert scoped["status"] == "clear" and scoped["config_errors"] == []
    named = _collect(readers, store, selected=["foo"], config_errors=[{"name": "foo", "error": "bad"}])
    assert named["status"] == "degraded" and named["sources"] == []


def test_a_source_that_raises_or_hangs_is_failed_never_a_crash(tmp_path):
    import time

    def boom(read_at):
        raise RuntimeError("coordinator down")

    def slow(read_at):
        time.sleep(3)
        return {"items": [], "status": "ok"}

    env = srcs.collect({"boom": boom, "slow": slow, "fine": _ok(_item(source="fine"))},
                       timeouts={"slow": 0.2}, selected=None, config_errors=[],
                       store=FirstObserved(tmp_path / "o.json"), read_at=T1)
    by = {s["name"]: s for s in env["sources"]}
    assert by["boom"]["status"] == by["slow"]["status"] == "failed" and "timed out" in by["slow"]["error"]
    assert env["status"] == "degraded" and len(env["items"]) == 1


def test_two_items_for_one_entity_fail_their_source(tmp_path):
    env = _collect({"s": _ok(_item(ref="x"), _item(ref="x", state="review"))}, FirstObserved(tmp_path / "o.json"))
    assert env["sources"][0]["status"] == "failed" and env["items"] == []


# -- the dispatch source --------------------------------------------------------------


@pytest.mark.parametrize("task,state", [
    ({"awaiting_steer": True, "status": "started", "card": {"request_input": [{"name": "answer", "type": "text"}]}}, "awaiting_input"),
    ({"hold_reason": "operator pause", "status": "queued"}, "blocked"),
    ({"status": "submitted"}, "review"),
    ({"status": "submitted", "evaluator_ref": "ev-1"}, None),  # its evaluator confirms it, not the operator
    ({"status": "completed"}, None),
    ({"status": "started"}, None),
    ({"status": "started", "awaiting_steer": True, "hold_reason": "x"}, "awaiting_input"),  # worst wins
    # submitted is concluded: a stale steering flag can't be answered (steer submit refuses it)
    ({"status": "submitted", "awaiting_steer": True, "hold_reason": "x"}, "review"),
    # a handoff baton's pickup is its completion: a submitted one is spent, never a review
    ({"status": "submitted", "labels": ["handoff"]}, None),
    ({"status": "submitted", "source": "context-handoff"}, None),
    # one nobody picked up waits on a successor; a young or claimed one doesn't
    ({"status": "proposed", "labels": ["handoff"], "created_at": NOW - 601}, "stalled"),
    ({"status": "queued", "source": "context-handoff", "created_at": NOW - 3600}, "stalled"),
    ({"status": "proposed", "labels": ["handoff"], "created_at": NOW - 60}, None),
    ({"status": "queued", "labels": ["handoff"], "owner": "w1", "created_at": NOW - 3600}, None),
    ({"status": "proposed", "created_at": NOW - 3600}, None),  # not a handoff
])
def test_dispatch_task_mapping(task, state):
    item = srcs._task_item({"id": "t1", "title": "Fix it", **task}, T1, now=NOW)
    assert (item and item["display_state"]) == state
    if state == "awaiting_input" and task.get("card"):
        assert item["input"] == [{"name": "answer", "type": "text"}]
        assert item["actions"][0] == {"verb": "show", "argv": ["agent-dispatch", "card", "show", "t1"]}
    elif state in ("review", "blocked", "stalled"):  # no card to show: the task itself
        assert item["actions"][0] == {"verb": "show", "argv": ["agent-dispatch", "show", "t1"]}


def test_the_unpicked_handoff_threshold_is_configurable(monkeypatch):
    task = {"id": "t1", "title": "Relay", "status": "proposed", "labels": ["handoff"], "created_at": NOW - 120}
    monkeypatch.setenv(srcs.HANDOFF_AFTER_ENV, "60")
    item = srcs._task_item(task, T1, now=NOW)
    assert item["display_state"] == "stalled" and "for 2 min: Relay" in item["reason"]
    monkeypatch.setenv(srcs.HANDOFF_AFTER_ENV, "0")
    assert srcs._task_item(task, T1, now=NOW) is None


def test_the_coordinators_epoch_timestamps_become_iso(tmp_path):
    """The coordinator reports updated_at as epoch seconds (a float)."""
    tasks = [{"id": "t1", "title": "A", "status": "submitted", "updated_at": 1790922891.0485818}]
    result = srcs.read_dispatch(lambda: _Client(tasks), T1)
    env = _collect({"dispatch": lambda r: result}, FirstObserved(tmp_path / "o.json"))
    assert env["sources"][0]["status"] == "ok"
    assert env["items"][0]["updated_at"] == "2026-10-02T06:34:51+00:00"


# -- command sources ---------------------------------------------------------------------


def _run_returning(stdout="", returncode=0, stderr=""):
    return lambda argv, timeout, cwd=None, **_k: subprocess.CompletedProcess(argv, returncode, stdout, stderr)


def _command(stdout, **kw):
    return srcs.read_command("ext", {"argv": ["x"], "timeout": 5}, T1, run=_run_returning(stdout, **kw))


def test_a_minimal_envelope_reads_ok_and_a_partial_read_says_so():
    assert _command(json.dumps({"schema": 1, "items": []}))["status"] == "ok"
    partial = _command(json.dumps({"schema": 1, "items": [], "status": "uncertain", "uncertain": 2}))
    assert (partial["status"], partial["uncertain"]) == ("uncertain", 2)


@pytest.mark.parametrize("payload", [
    {"items": []}, {"schema": 2, "items": []}, {"schema": 1}, {"schema": 1, "items": [], "status": "disabled"},
    {"schema": 1, "items": [], "status": "ok", "uncertain": 1}, {"schema": 1, "items": [], "status": "uncertain"},
])
def test_contract_violations_fail_the_source(payload):
    assert _command(json.dumps(payload))["status"] == "failed"


def test_a_self_reported_failure_without_an_error_gets_the_fallback():
    result = _command(json.dumps({"schema": 1, "items": [], "status": "failed"}))
    assert result["error"] == "source reported failed without an error"


@pytest.mark.parametrize("kw", [{"returncode": 3}, {"stdout": "not json"}])
def test_a_command_that_exits_non_zero_or_prints_non_json_fails(kw):
    assert srcs.read_command("ext", {"argv": ["x"], "timeout": 5}, T1,
                             run=_run_returning(**kw))["status"] == "failed"


def test_a_command_that_times_out_fails():
    assert srcs.read_command("ext", {"argv": ["x"], "timeout": 5}, T1, run=lambda a, timeout, cwd=None, **_k: None)["status"] == "failed"


def _cmd_item(**kw):
    item = {"schema": 1, "entity": "login", "entity_ref": "acct", "lifecycle_state": None,
            "display_state": "awaiting_input", "reason": "sign in again", "confidence": "reported",
            "actions": [], **kw}
    return {k: v for k, v in item.items() if v is not ...}


@pytest.mark.parametrize("item", [_cmd_item(source="dispatch"), _cmd_item(id="dispatch:task:t1"),
                                  _cmd_item(entity="x.other.login"), _cmd_item(also=[{"x": 1}])])
def test_an_item_cannot_alias_another_source(item):
    assert _command(json.dumps({"schema": 1, "items": [item]}))["status"] == "failed"


def test_an_item_omitting_source_id_and_times_is_stamped_then_validated(tmp_path):
    store = FirstObserved(tmp_path / "o.json")
    reader = lambda read_at: srcs.read_command("ext", {"argv": ["x"], "timeout": 5}, read_at,  # noqa: E731
                                               run=_run_returning(json.dumps({"schema": 1, "items": [_cmd_item()]})))
    first = _collect({"ext": reader}, store, read_at=T0)["items"][0]
    assert first["id"] == "ext:x.ext.login:acct" and first["source"] == "ext" and first["created_at"] == T0
    assert _collect({"ext": reader}, store, read_at=T2)["items"][0]["created_at"] == T0
    namespaced = _cmd_item(entity="x.ext.login")
    assert _command(json.dumps({"schema": 1, "items": [namespaced]}))["items"][0]["id"] == first["id"]


# -- registrations and the CLI ------------------------------------------------------------


def test_a_registration_under_a_builtin_name_is_rejected_not_listed(tmp_path):
    srcs.save_registrations({"dispatch": {"argv": [sys.executable]}, "ok-one": {"argv": [sys.executable]},
                             "Bad Name": {"argv": [sys.executable]}, "relative": {"argv": ["./source"]}})
    valid, errors = srcs.load_registrations()
    assert list(valid) == ["ok-one"]
    assert sorted(e["name"] for e in errors) == ["Bad Name", "dispatch", "relative"]


class _Client:
    def __init__(self, tasks, backlogs=None):
        self.tasks, self.backlogs = tasks, backlogs or {}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list(self, **_kw):
        return self.tasks

    def health(self, repo=None):
        return {"backlog": self.backlogs.get(repo, {})}


def _lane(age_queued=None, age_held=None, **counts):
    return {"queued": 1, "held_live": 0, "held_unknown": 0, "held_gone": 0,
            "oldest_queued_age": age_queued, "oldest_held_live_age": age_held, **counts}


@pytest.mark.parametrize("backlog,stalled", [
    (_lane(age_queued=1800), False),           # exactly the threshold is not stalled
    (_lane(age_queued=1801), True),            # one second over is
    (_lane(age_held=1801, held_live=1), True),  # a live owner stopped progressing
    (_lane(held_unknown=3, held_gone=2), False),  # unknown/gone owners never count
])
def test_a_lane_that_isnt_draining_is_one_stalled_queue_item(monkeypatch, backlog, stalled):
    monkeypatch.delenv(srcs.QUEUED_AFTER_ENV, raising=False)
    monkeypatch.delenv(srcs.HELD_LIVE_AFTER_ENV, raising=False)
    tasks = [{"id": "q1", "title": "x", "status": "queued", "repo": "o/r"}]
    result = srcs.read_dispatch(lambda: _Client(tasks, {"o/r": backlog}), T1)
    queue = [i for i in result["items"] if i["entity"] == "queue"]
    assert bool(queue) is stalled
    if stalled:
        (item,) = queue
        assert (item["entity_ref"], item["display_state"], item["lifecycle_state"]) == ("o/r", "stalled", None)
        ac.validate_item({**item, "created_at": T1})


def test_a_zero_threshold_turns_that_half_off(monkeypatch):
    monkeypatch.setenv(srcs.QUEUED_AFTER_ENV, "0")
    tasks = [{"id": "q1", "title": "x", "status": "queued", "repo": "o/r"}]
    result = srcs.read_dispatch(lambda: _Client(tasks, {"o/r": _lane(age_queued=99999)}), T1)
    assert not [i for i in result["items"] if i["entity"] == "queue"]


def _cli(monkeypatch, capsys, argv, tasks=()):
    monkeypatch.setattr(m, "_client", lambda args: _Client(list(tasks)))
    args = m.build_parser().parse_args(argv)
    rc = args.func(args)
    return rc, capsys.readouterr()


def test_cli_unknown_source_is_a_usage_error_that_reads_nothing(monkeypatch, capsys):
    monkeypatch.setattr(m, "_client", lambda args: pytest.fail("nothing may be read"))
    args = m.build_parser().parse_args(["attention", "--source", "bridgge"])
    assert args.func(args) == 2 and "bridgge" in capsys.readouterr().err


def test_cli_json_and_next_walk(monkeypatch, capsys):
    tasks = [{"id": "t1", "title": "A", "status": "submitted"}, {"id": "t2", "title": "B", "status": "started",
                                                                  "awaiting_steer": True}]
    rc, out = _cli(monkeypatch, capsys, ["attention", "--json"], tasks)
    env = json.loads(out.out)
    assert rc == 0 and env["status"] == "attention" and [i["entity_ref"] for i in env["items"]] == ["t2", "t1"]
    rc, out = _cli(monkeypatch, capsys, ["attention", "next", "--json", "--source", "dispatch"], tasks)
    first = json.loads(out.out)
    assert first["item"]["entity_ref"] == "t2" and "items" not in first and first["selected"] == ["dispatch"]
    # t2 is answered (gone) between calls: the walk still advances, then wraps.
    rc, out = _cli(monkeypatch, capsys, ["attention", "next", "--json", "--after", first["cursor"]], tasks[:1])
    second = json.loads(out.out)
    assert second["item"]["entity_ref"] == "t1"
    rc, out = _cli(monkeypatch, capsys, ["attention", "next", "--json", "--after", second["cursor"]], tasks[:1])
    assert json.loads(out.out)["item"]["entity_ref"] == "t1"


def test_cli_a_clear_and_a_degraded_empty_read_stay_distinguishable(monkeypatch, capsys):
    rc, out = _cli(monkeypatch, capsys, ["attention"])
    assert "Nothing needs you" in out.out
    srcs.save_registrations({"broken": {"argv": ["definitely-not-a-real-command-xyz"], "timeout": 2}})
    rc, out = _cli(monkeypatch, capsys, ["attention"])
    assert "[DEGRADED]" in out.out and "Nothing needs you" not in out.out


def test_cli_source_add_list_remove(monkeypatch, capsys):
    rc, out = _cli(monkeypatch, capsys, ["attention", "source", "add", "ext", "--", "python", "-c", "x"])
    assert rc == 0 and json.loads(out.out)["registered"] == "ext"
    rc, out = _cli(monkeypatch, capsys, ["attention", "source", "add", "dispatch", "--", "x"])
    assert rc == 2
    rc, out = _cli(monkeypatch, capsys, ["attention", "source", "list"])
    assert list(json.loads(out.out)["registered"]) == ["ext"]
    rc, out = _cli(monkeypatch, capsys, ["attention", "source", "remove", "ext"])
    assert json.loads(out.out)["removed"] is True


def test_cli_bad_cursor_is_a_usage_error(monkeypatch, capsys):
    rc, _ = _cli(monkeypatch, capsys, ["attention", "next", "--after", "%%%"])
    assert rc == 2


def test_cli_source_add_parses_options_before_the_command(monkeypatch, capsys):
    rc, out = _cli(monkeypatch, capsys, ["attention", "source", "add", "ext", "--timeout", "5", "--",
                                         "python", "-c", "print(1)"])
    doc = json.loads(out.out)
    assert rc == 0 and doc["registered"] == "ext" and doc["timeout"] == 5.0
    # The bare command is pinned to an absolute path at registration.
    assert os.path.isabs(doc["argv"][0]) and doc["argv"][1:] == ["-c", "print(1)"]


def test_a_command_source_runs_from_the_registry_directory_not_the_callers(monkeypatch, tmp_path):
    seen = {}

    def run(argv, timeout, cwd=None, **_k):
        seen["cwd"] = cwd
        return subprocess.CompletedProcess(argv, 0, json.dumps({"schema": 1, "items": []}), "")

    checkout = tmp_path / "untrusted-checkout"
    checkout.mkdir()
    monkeypatch.chdir(checkout)
    srcs.read_command("ext", {"argv": [sys.executable], "timeout": 5}, T1, run=run)
    assert seen["cwd"] == str(srcs.registry_path().parent) != str(checkout)


def test_a_command_readers_deadline_covers_its_runners_whole_cleanup():
    """Past the command's own timeout, its runner still stops the tree: a 5 s
    SIGTERM grace and a 2 s post-SIGKILL reap at least. The reader must not be
    abandoned before that finishes, or a SIGTERM-ignoring command outlives the CLI."""
    from agent_dispatch import attention_cli

    srcs.save_registrations({"slow": {"argv": [sys.executable], "timeout": 10}})
    args = m.build_parser().parse_args(["attention"])
    _readers, timeouts, _errors, _known = attention_cli._readers(args)
    assert timeouts["slow"] - 10 >= 5.0 + 2.0 + 5.0  # grace, reap, identity probe


def test_a_command_source_that_floods_output_fails_alone(monkeypatch):
    monkeypatch.setattr(srcs, "MAX_OUTPUT", 10_000)
    flood = [sys.executable, "-c", "import sys\nwhile True: sys.stdout.write('x' * 65536)"]
    result = srcs.read_command("ext", {"argv": flood, "timeout": 20}, T1)
    assert result["status"] == "failed" and "more than 10000 characters" in result["error"]
    quiet = [sys.executable, "-c", "import json; print(json.dumps({'schema': 1, 'items': []}))"]
    assert srcs.read_command("ext", {"argv": quiet, "timeout": 20}, T1)["status"] == "ok"


def test_a_short_deadline_is_honored_even_when_checked_after_a_long_one(tmp_path):
    import time

    def slow_ok(read_at):
        time.sleep(0.6)
        return {"items": [], "status": "ok"}

    env = srcs.collect({"a-long": lambda r: (time.sleep(1.0), {"items": [], "status": "ok"})[1], "b-short": slow_ok},
                       timeouts={"a-long": 2.0, "b-short": 0.2}, selected=None, config_errors=[],
                       store=FirstObserved(tmp_path / "o.json"), read_at=T1)
    by = {s["name"]: s["status"] for s in env["sources"]}
    assert by == {"a-long": "ok", "b-short": "failed"}


def test_a_malformed_store_is_recovered_not_a_crash(tmp_path):
    path = tmp_path / "o.json"
    path.write_text('{"entries": ["bad"]}', encoding="utf-8")
    env = _collect({"s": _ok({**_item(), "created_at": None})}, FirstObserved(path), read_at=T2)
    assert env["items"][0]["created_at"] == T2


def test_a_real_steering_card_form_reaches_the_item():
    """steering.build_card stores request_input as a list of fields."""
    from agent_dispatch import steering

    form = steering.parse_request_input("decision:choice[revise,approve],notes:textarea")
    assert isinstance(form, list) and form
    item = srcs._task_item({"id": "t1", "title": "A", "status": "started", "awaiting_steer": True,
                            "card": {"request_input": form}}, T1)
    assert item["input"] == form
    ac.validate_item({**item, "created_at": T1})


def test_the_action_reaches_the_coordinator_the_read_came_from(monkeypatch, capsys):
    tasks = [{"id": "t1", "title": "A", "status": "submitted"}]
    rc, out = _cli(monkeypatch, capsys, ["--url", "http://peer:8787", "attention", "--json"], tasks)
    argv = json.loads(out.out)["items"][0]["actions"][0]["argv"]
    assert argv == ["agent-dispatch", "--url", "http://peer:8787", "show", "t1"]


def test_a_read_authenticated_only_by_a_token_argument_offers_no_actions(monkeypatch, capsys):
    """An action never carries a secret, so after `--token` none could run as-is."""
    tasks = [{"id": "t1", "title": "A", "status": "submitted"}]
    rc, out = _cli(monkeypatch, capsys, ["--url", "http://peer:8787", "--token", "s3cret", "attention", "--json"],
                   tasks)
    assert json.loads(out.out)["items"][0]["actions"] == [] and "s3cret" not in out.out


def test_the_next_hint_walks_the_same_coordinator_and_sources(monkeypatch, capsys):
    tasks = [{"id": "t1", "title": "A", "status": "submitted"}]
    rc, out = _cli(monkeypatch, capsys, ["--url", "http://peer:8787", "--shared",
                                         "attention", "next", "--source", "dispatch"], tasks)
    hint = next(line for line in out.out.splitlines() if "next:" in line).split("next: ", 1)[1].split()
    assert hint[:4] == ["agent-dispatch", "--url", "http://peer:8787", "--shared"]
    assert hint[4:7] == ["attention", "next", "--after"] and hint[8:] == ["--source", "dispatch"]
    assert "s3cret" not in out.out


def test_concurrent_registrations_are_never_lost(monkeypatch):
    import threading

    monkeypatch.setattr(m, "_emit", lambda payload: 0)
    parser = m.build_parser()
    names = [f"src-{i}" for i in range(8)]

    def add(name):
        args = parser.parse_args(["attention", "source", "add", name, "--", sys.executable])
        args.func(args)

    threads = [threading.Thread(target=add, args=(n,)) for n in names]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert sorted(srcs.load_registrations()[0]) == names


def test_a_dead_lock_holder_never_wedges_the_next_read(tmp_path):
    import subprocess as sp
    import sys

    path = tmp_path / "o.json"
    holder = ("from agent_dispatch.attention_store import locked\nimport os, pathlib\n"
              f"with locked(pathlib.Path(r'{path}')):\n    os._exit(0)\n")
    sp.run([sys.executable, "-c", holder], check=True, timeout=30)
    with attention_store.locked(path, timeout=2):
        pass  # the OS released the dead holder's lock


def test_a_read_that_hits_the_limit_says_it_may_be_incomplete():
    tasks = [{"id": f"t{i}", "title": "x", "status": "submitted"} for i in range(3)]
    result = srcs.read_dispatch(lambda: _Client(tasks), T1, limit=3)
    assert (result["status"], result["uncertain"]) == ("uncertain", 1)
    assert srcs.read_dispatch(lambda: _Client(tasks), T1, limit=4)["status"] == "ok"


def test_a_hung_reader_never_keeps_the_process_alive(tmp_path):
    import sys
    import time

    script = (
        "import time\n"
        "from agent_dispatch import attention_sources as s\n"
        "from agent_dispatch.attention_store import FirstObserved\n"
        f"store = FirstObserved(__import__('pathlib').Path(r'{tmp_path}') / 'o.json')\n"
        "env = s.collect({'hang': lambda r: time.sleep(60)}, timeouts={'hang': 0.3}, selected=None,\n"
        "                config_errors=[], store=store)\n"
        "print(env['sources'][0]['status'])\n"
    )
    started = time.monotonic()
    done = subprocess.run([sys.executable, "-c", script], capture_output=True, text=True, timeout=30)
    assert done.stdout.strip() == "failed" and time.monotonic() - started < 15


def test_cli_next_on_a_degraded_empty_read_is_not_all_clear(monkeypatch, capsys):
    srcs.save_registrations({"broken": {"argv": ["definitely-not-a-real-command-xyz"], "timeout": 2}})
    rc, out = _cli(monkeypatch, capsys, ["attention", "next"])
    assert "[DEGRADED]" in out.out and "Nothing needs you" not in out.out and "No items read." in out.out


def test_cli_source_add_refuses_a_registry_whose_sources_is_not_an_object(monkeypatch, capsys):
    srcs.registry_path().write_text('{"sources": []}', encoding="utf-8")
    rc, out = _cli(monkeypatch, capsys, ["attention", "source", "add", "ext", "--", sys.executable])
    assert rc == 1 and "not an object" in out.err


@pytest.mark.parametrize("entity", ["a:b", "x.ext.a:b", "x.ext.A"])
def test_a_custom_kind_cannot_carry_an_id_delimiter(entity):
    with pytest.raises(ac.ContractError):
        ac.canonical_entity(entity, "ext")


@pytest.mark.parametrize("value,expected", [
    ("2026-10-07T12:00:00Z", "2026-10-07T12:00:00+00:00"),
    ("2026-10-07T13:00:00.123+01:00", "2026-10-07T12:00:00+00:00"),
])
def test_command_timestamps_are_normalized_to_utc(value, expected):
    result = _command(json.dumps({"schema": 1, "items": [_cmd_item(created_at=value)]}))
    assert result["items"][0]["created_at"] == expected


@pytest.mark.parametrize("value", ["zzz", "2026-10-07T12:00:00", 5])
def test_a_bad_or_offsetless_timestamp_fails_the_source(value):
    assert _command(json.dumps({"schema": 1, "items": [_cmd_item(created_at=value)]}))["status"] == "failed"


@pytest.mark.parametrize("schema", [True, 1.0])
def test_a_schema_that_only_compares_equal_to_1_is_rejected(schema):
    with pytest.raises(ac.ContractError):
        ac.validate_item({**_item(), "schema": schema})
    assert ac.normalize_command_result({"schema": schema, "items": []}, name="ext")["status"] == "failed"


def test_human_output_escapes_control_characters_but_json_keeps_them(monkeypatch, capsys):
    monkeypatch.setattr(srcs, "load_registrations",
                        lambda path=None: ({}, [{"name": "evil", "error": "boom\x1b[2Jgone\x07"}]))
    rc, out = _cli(monkeypatch, capsys, ["attention"])
    assert "\x1b" not in out.out and "\x07" not in out.out and "\\x1b[2J" in out.out
    rc, out = _cli(monkeypatch, capsys, ["attention", "--json"])
    assert json.loads(out.out)["config_errors"][0]["error"] == "boom\x1b[2Jgone\x07"


def test_a_stored_time_that_is_not_canonical_is_dropped(tmp_path):
    path = tmp_path / "observed.json"
    path.write_text(json.dumps({"version": 1, "entries": {"a": "bad", "b": T0, "c": "2026-10-07T10:00:00Z"}}),
                    encoding="utf-8")
    assert attention_store._read(path) == ({"b": T0}, {}, 1)


def test_lanes_past_the_backlog_budget_are_uncertain_not_a_failed_source():
    tasks = [{"id": f"q{i}", "title": "x", "status": "started", "awaiting_steer": True, "repo": f"o/r{i}"}
             for i in range(3)]
    result = srcs.read_dispatch(lambda: _Client(tasks), T1, backlog_budget=0)
    assert result["status"] == "uncertain" and result["uncertain"] == 3
    assert {i["entity_ref"] for i in result["items"]} == {"q0", "q1", "q2"}  # the task items survive


@pytest.mark.parametrize("also", [{}, None, "", ["x"]])
def test_any_present_also_other_than_an_empty_list_is_malformed(also):
    with pytest.raises(ac.ContractError):
        ac.stamp_command_item({"schema": 1, "entity": "task", "entity_ref": "t1", "lifecycle_state": "x",
                               "display_state": "review", "reason": "r", "confidence": "reported",
                               "actions": [], "also": also}, name="ext")


def test_an_older_snapshot_never_re_adds_a_time_a_newer_ok_read_cleared(tmp_path):
    store = FirstObserved(tmp_path / "o.json")
    _collect({"s": _ok({**_item(), "created_at": None})}, store, read_at=T0)
    _collect({"s": _ok()}, store, read_at=T2)  # newer proof: the condition ended
    _collect({"s": _ok({**_item(), "created_at": None})}, store, read_at=T1)  # a slow, older read lands last
    env = _collect({"s": _ok({**_item(), "created_at": None})}, store, read_at=T2)
    assert env["items"][0]["created_at"] == T2  # first seen again after the clear, not T0/T1


def test_a_failed_lane_read_is_uncertain_and_keeps_the_task_items():
    class Flaky(_Client):
        def health(self, repo=None):
            raise srcs.DispatchError(500, "backlog query failed")

    tasks = [{"id": "q0", "title": "x", "status": "started", "awaiting_steer": True, "repo": "o/r"}]
    result = srcs.read_dispatch(lambda: Flaky(tasks), T1)
    assert result["status"] == "uncertain" and result["uncertain"] == 1
    assert [i["entity_ref"] for i in result["items"]] == ["q0"]


def test_reads_in_the_same_second_are_ordered_by_their_token(tmp_path):
    store = FirstObserved(tmp_path / "o.json")
    _collect({"s": _ok({**_item(), "created_at": None})}, store, read_at=T0, read_token=1)
    _collect({"s": _ok()}, store, read_at=T1, read_token=3)  # the newer ok read clears it
    _collect({"s": _ok({**_item(), "created_at": None})}, store, read_at=T1, read_token=2)  # same second, older
    env = _collect({"s": _ok({**_item(), "created_at": None})}, store, read_at=T2, read_token=4)
    assert env["items"][0]["created_at"] == T2


def _scoped(scope, *items):
    return lambda read_at: {"items": [dict(i) for i in items], "status": "ok", "uncertain": 0,
                            **({"scope": scope} if scope else {})}


def test_a_read_of_another_coordinator_never_clears_this_ones_first_observed_times(tmp_path):
    store = FirstObserved(tmp_path / "o.json")
    shared = "https://shared.example"
    x = {**_item(source="dispatch"), "created_at": None}
    _collect({"dispatch": _scoped(None, x)}, store, read_at=T0, read_token=1)
    _collect({"dispatch": _scoped(shared)}, store, read_at=T1, read_token=2)  # a failover read: ok, empty
    env = _collect({"dispatch": _scoped(None, x)}, store, read_at=T2, read_token=3)
    assert env["items"][0]["created_at"] == T0
    # The other way round: the shared queue's own times survive a local ok read.
    _collect({"dispatch": _scoped(shared, x)}, store, read_at=T1, read_token=4)
    _collect({"dispatch": _scoped(None)}, store, read_at=T2, read_token=5)
    env = _collect({"dispatch": _scoped(shared, x)}, store, read_at=T2, read_token=6)
    assert env["items"][0]["created_at"] == T1
    assert all("scope" not in s for s in env["sources"])


def test_a_command_source_cannot_choose_its_coordinator_scope(tmp_path):
    store = FirstObserved(tmp_path / "o.json")
    y = {**_item(source="ext"), "created_at": None}
    _collect({"ext": _scoped("https://elsewhere.example", y)}, store, read_at=T0, read_token=1)
    env = _collect({"ext": _scoped(None, y)}, store, read_at=T2, read_token=2)
    assert env["items"][0]["created_at"] == T0  # one key: the scope it claimed was ignored


@pytest.mark.parametrize("flags, base, tunnel, configured, expected", [
    ({}, "http://127.0.0.1:41000/", None, None, None),                          # this machine's own
    ({}, "http://127.0.0.1:41000/", None, "https://shared.example", None),
    ({}, "https://shared.example/", None, "https://shared.example", "https://shared.example"),  # silent failover
    ({"shared": True}, "https://shared.example/", None, None, "https://shared.example"),
    ({"url": "http://box:9000"}, "http://box:9000", None, None, "http://box:9000"),
    ({}, "http://127.0.0.1:51999", "peer-a", None, "ssh:peer-a"),              # never the random local port
])
def test_the_dispatch_read_names_the_coordinator_it_reached(monkeypatch, flags, base, tunnel, configured, expected):
    from types import SimpleNamespace

    from agent_dispatch import attention_cli, config

    monkeypatch.setattr(config, "shared_url", lambda: configured)
    args = SimpleNamespace(**{"url": None, "shared": False, "token": None, **flags})
    client = SimpleNamespace(base_url=base, _tunnel=SimpleNamespace(_machine=tunnel) if tunnel else None)
    assert attention_cli._coordinator_scope(args, client) == expected


def test_a_silent_failover_to_the_shared_coordinator_is_pinned_in_actions(monkeypatch, capsys):
    from agent_dispatch import config

    class Shared(_Client):
        base_url = "https://shared.example/"

    tasks = [{"id": "t1", "title": "A", "status": "submitted"}]
    monkeypatch.setattr(config, "shared_url", lambda: "https://shared.example")
    monkeypatch.setattr(m, "_client", lambda args: Shared(tasks))
    args = m.build_parser().parse_args(["attention", "next"])
    assert args.func(args) == 0
    out = capsys.readouterr().out
    assert "agent-dispatch --shared show t1" in out
    assert "next: agent-dispatch --shared attention next --after" in out
    stored = json.loads(attention_store.default_path().read_text(encoding="utf-8"))
    assert stored["applied"] and set(stored["applied"]) == {"dispatch@https://shared.example"}


@pytest.mark.parametrize("severity", [False, 1.0, True])
def test_a_severity_that_only_compares_equal_is_rejected(severity):
    with pytest.raises(ac.ContractError):
        ac.validate_item({**_item(state="failed"), "severity": severity})


def test_a_command_source_cannot_set_input():
    with pytest.raises(ac.ContractError, match="input"):
        ac.stamp_command_item({"schema": 1, "entity": "task", "entity_ref": "t1", "lifecycle_state": "x",
                               "display_state": "awaiting_input", "reason": "r", "confidence": "reported",
                               "actions": [], "input": [{"name": "answer", "type": "text"}]}, name="ext")


def test_an_ssh_failover_read_offers_no_actions(monkeypatch, capsys):
    class ViaPeer(_Client):
        _tunnel = object()  # an SSH port-forward to a peer's coordinator

    tasks = [{"id": "t1", "title": "A", "status": "submitted"}]
    monkeypatch.setattr(m, "_client", lambda args: ViaPeer(tasks))
    args = m.build_parser().parse_args(["attention", "--json"])
    assert args.func(args) == 0
    assert json.loads(capsys.readouterr().out)["items"][0]["actions"] == []


def test_an_ssh_failover_read_keeps_its_peer_scope_after_the_client_closes(monkeypatch, capsys):
    from types import SimpleNamespace

    class ViaPeer(_Client):
        def __init__(self, tasks):
            super().__init__(tasks)
            self._tunnel = SimpleNamespace(_machine="peer-a")

        def __exit__(self, *exc):
            self._tunnel = None  # as DispatchClient.close() does
            return False

    monkeypatch.setattr(m, "_client", lambda args: ViaPeer([{"id": "t1", "title": "A", "status": "submitted"}]))
    args = m.build_parser().parse_args(["attention", "--json"])
    assert args.func(args) == 0
    stored = json.loads(attention_store.default_path().read_text(encoding="utf-8"))
    assert set(stored["applied"]) == {"dispatch@ssh:peer-a"}


def test_a_coordinator_usage_error_fails_the_source_at_once():
    import time

    def reader(_read_at):
        raise SystemExit(2)  # e.g. --shared with no shared coordinator configured

    started = time.monotonic()
    env = srcs.collect({"dispatch": reader}, timeouts={"dispatch": 20.0}, selected=None, config_errors=[],
                       store=FirstObserved(attention_store.default_path()), read_at=T1)
    assert env["status"] == "degraded" and "coordinator" in env["sources"][0]["error"]
    assert time.monotonic() - started < 5


@pytest.mark.parametrize("raw", [None, [], {"items": []}, {"status": "ok"}, {"status": "great", "items": []},
                                 {"status": "ok", "items": [], "uncertain": -1}])
def test_a_malformed_reader_result_fails_that_source_not_the_read(tmp_path, raw):
    env = _collect({"s": lambda _r: raw, "t": _ok()}, FirstObserved(tmp_path / "o.json"))
    status = {s["name"]: s["status"] for s in env["sources"]}
    assert status == {"s": "failed", "t": "ok"} and env["status"] == "degraded"


@pytest.mark.parametrize("prefix", [["--token", "s3cret"], []])
def test_no_next_hint_when_no_invocation_reaches_the_coordinator(monkeypatch, capsys, prefix):
    class ViaPeer(_Client):
        _tunnel = object()

    tasks = [{"id": "t1", "title": "A", "status": "submitted"}]
    monkeypatch.setattr(m, "_client", lambda args: (_Client if prefix else ViaPeer)(tasks))
    args = m.build_parser().parse_args([*prefix, "attention", "next"])
    assert args.func(args) == 0
    assert "next:" not in capsys.readouterr().out


@pytest.mark.parametrize("form", [
    [{"name": "a", "type": []}],                                            # an unhashable type
    [{"name": "a", "type": {"k": "v"}}],
    {"answer": "text"},                                                     # an object, not a field list
    [],                                                                     # empty
    [{"name": "a", "type": "slider"}],                                      # unknown type
    [{"name": "a", "type": "choice"}],                                      # a choice without options
    [{"name": "a", "type": "text", "options": ["x"]}],                      # options on a text field
    [{"name": "a", "type": "text", "allow_other": True}],                   # allow_other on a text field
    [{"name": "a", "type": "text", "show_when": {"field": "b"}}],           # incomplete show_when
    [{"name": "a", "type": "text", "colour": "red"}],                       # an unknown key
])
def test_input_is_exactly_the_steering_field_list(form):
    with pytest.raises(ac.ContractError):
        ac.check_input(form)
    # A card whose form isn't that shape stays an item, reachable by `card show`, without input.
    item = srcs._task_item({"id": "t1", "title": "x", "status": "started", "awaiting_steer": True,
                            "card": {"request_input": form}}, T1)
    assert item["display_state"] == "awaiting_input" and "input" not in item


def test_every_steering_field_form_is_accepted():
    from agent_dispatch import steering

    form = steering.parse_request_input(
        "feedback,notes:textarea,decision:choice[revise,approve],tags:multichoice[a,b],why:textarea?decision=revise")
    ac.check_input(form)


@pytest.mark.parametrize("form", [
    [{"name": "a", "type": "text"}, {"name": "a", "type": "text"}],                      # duplicate name
    [{"name": "1a", "type": "text"}],                                                    # invalid name
    [{"name": "a", "type": "choice", "options": [""]}],                                  # an empty option
    [{"name": "a", "type": "text", "show_when": {"field": "nope", "equals": "x"}}],      # unknown reference
    [{"name": "c", "type": "choice", "options": ["x"]},
     {"name": "a", "type": "text", "show_when": {"field": "c", "equals": "y"}}],         # not an option
])
def test_input_uses_the_parsers_own_field_rules(form):
    from agent_dispatch import steering_fields

    assert steering_fields.field_list_problem(form)
    with pytest.raises(ac.ContractError):
        ac.check_input(form)


def test_a_disabled_source_stays_neutral_through_collect(tmp_path):
    env = _collect({"opt": lambda _r: {"status": "disabled", "items": []}, "t": _ok()},
                   FirstObserved(tmp_path / "o.json"))
    assert {s["name"]: s["status"] for s in env["sources"]} == {"opt": "disabled", "t": "ok"}
    assert env["status"] == "clear"


@pytest.mark.parametrize("status", ["failed", "disabled"])
def test_items_of_a_failed_or_disabled_source_never_reach_the_queue(tmp_path, status):
    env = _collect({"s": lambda _r: {"status": status, "error": "down", "items": [{}, _item()]}},
                   FirstObserved(tmp_path / "o.json"))
    assert env["items"] == [] and env["sources"][0]["items"] == 0


@pytest.mark.parametrize("value", ["ext\n", "ok-one\n"])
def test_names_with_a_trailing_newline_are_rejected(value):
    from agent_dispatch import steering_fields

    assert not ac.SOURCE_NAME.match(value) and not ac.CUSTOM_KIND.match(value)
    assert steering_fields.field_list_problem([{"name": "answer\n", "type": "text"}])
    assert srcs.registration_error(value, {"argv": [sys.executable]})


def test_read_numbers_are_strictly_increasing_and_persisted(tmp_path):
    a, b = FirstObserved(tmp_path / "o.json"), FirstObserved(tmp_path / "o.json")  # two processes
    numbers = [a.begin_read(), b.begin_read(), a.begin_read()]
    assert numbers == sorted(set(numbers)) and len(numbers) == 3


def test_a_damaged_counter_never_reissues_an_applied_number(tmp_path):
    path = tmp_path / "o.json"
    path.write_text(json.dumps({"version": 1, "entries": {}, "applied": {"s": 41}, "next_read": 3}), encoding="utf-8")
    assert FirstObserved(path).begin_read() == 42


@pytest.mark.parametrize("item_kw", [{"source": "bridge", "state": "awaiting_input"},
                                     {"source": "dispatch", "state": "review"}])
def test_input_belongs_to_a_dispatch_item_awaiting_input_only(item_kw):
    with pytest.raises(ac.ContractError, match="input belongs"):
        ac.validate_item({**_item(**item_kw), "input": [{"name": "a", "type": "text"}]})
    with pytest.raises(ac.ContractError):
        ac.validate_item({**_item(source="dispatch", state="awaiting_input"), "input": None})
