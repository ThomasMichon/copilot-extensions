"""agent-dispatch attention: contract, ordering, dedupe, first-observed times,
aggregate status, the dispatch source, command sources and the CLI."""

from __future__ import annotations

import json
import subprocess

import pytest

from agent_dispatch import __main__ as m
from agent_dispatch import attention_contract as ac
from agent_dispatch import attention_sources as srcs
from agent_dispatch import attention_store
from agent_dispatch.attention_store import FirstObserved

T0, T1, T2 = "2026-10-07T10:00:00+00:00", "2026-10-07T11:00:00+00:00", "2026-10-07T12:00:00+00:00"


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(srcs, "registry_path", lambda: tmp_path / "attention-sources.json")
    monkeypatch.setattr(attention_store, "default_path", lambda: tmp_path / "attention-observed.json")


def _item(source="s", entity="task", ref="t1", state="awaiting_input", created=T0, **kw):
    item = ac.new_item(source=source, entity=entity, entity_ref=ref, lifecycle_state=kw.pop("lifecycle", "started"),
                       display_state=state, reason=kw.pop("reason", f"{ref} needs you"), created_at=created,
                       updated_at=created, **kw)
    return item


def _ok(*items, status="ok", uncertain=0):
    return lambda read_at: {"items": [dict(i) for i in items], "status": status, "uncertain": uncertain}


def _collect(readers, store, selected=None, config_errors=(), read_at=T1):
    return srcs.collect(readers, timeouts={}, selected=selected, config_errors=list(config_errors),
                        store=store, read_at=read_at)


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
    ({"awaiting_steer": True, "status": "started", "card": {"request_input": {"answer": "text"}}}, "awaiting_input"),
    ({"hold_reason": "operator pause", "status": "queued"}, "blocked"),
    ({"status": "submitted"}, "review"),
    ({"status": "completed"}, None),
    ({"status": "started"}, None),
    ({"status": "submitted", "awaiting_steer": True, "hold_reason": "x"}, "awaiting_input"),  # worst wins
])
def test_dispatch_task_mapping(task, state):
    item = srcs._task_item({"id": "t1", "title": "Fix it", **task}, T1)
    assert (item and item["display_state"]) == state
    if state == "awaiting_input" and task.get("card"):
        assert item["input"] == {"answer": "text"}
        assert item["actions"][0] == {"verb": "show", "argv": ["agent-dispatch", "card", "show", "t1"]}


def test_the_coordinators_epoch_timestamps_become_iso(tmp_path):
    """The coordinator reports updated_at as epoch seconds (a float)."""
    tasks = [{"id": "t1", "title": "A", "status": "submitted", "updated_at": 1790922891.0485818}]
    result = srcs.read_dispatch(lambda: _Client(tasks), T1)
    env = _collect({"dispatch": lambda r: result}, FirstObserved(tmp_path / "o.json"))
    assert env["sources"][0]["status"] == "ok"
    assert env["items"][0]["updated_at"] == "2026-10-02T06:34:51+00:00"


# -- command sources ---------------------------------------------------------------------


def _run_returning(stdout="", returncode=0, stderr=""):
    return lambda argv, timeout: subprocess.CompletedProcess(argv, returncode, stdout, stderr)


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
    assert srcs.read_command("ext", {"argv": ["x"], "timeout": 5}, T1, run=lambda a, timeout: None)["status"] == "failed"


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
    srcs.save_registrations({"dispatch": {"argv": ["x"]}, "ok-one": {"argv": ["y"]}, "Bad Name": {"argv": ["z"]}})
    valid, errors = srcs.load_registrations()
    assert list(valid) == ["ok-one"] and sorted(e["name"] for e in errors) == ["Bad Name", "dispatch"]


class _Client:
    def __init__(self, tasks):
        self.tasks = tasks

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def list(self, **_kw):
        return self.tasks


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
    assert rc == 0 and json.loads(out.out) == {"registered": "ext", "argv": ["python", "-c", "print(1)"],
                                               "timeout": 5.0}


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
    rc, out = _cli(monkeypatch, capsys, ["--url", "http://peer:8787", "--token", "s3cret", "attention", "--json"],
                   tasks)
    argv = json.loads(out.out)["items"][0]["actions"][0]["argv"]
    assert argv == ["agent-dispatch", "--url", "http://peer:8787", "card", "show", "t1"]
    assert "s3cret" not in out.out


def test_concurrent_registrations_are_never_lost(monkeypatch):
    import threading

    monkeypatch.setattr(m, "_emit", lambda payload: 0)
    parser = m.build_parser()
    names = [f"src-{i}" for i in range(8)]

    def add(name):
        args = parser.parse_args(["attention", "source", "add", name, "--", "x"])
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
    rc, out = _cli(monkeypatch, capsys, ["attention", "source", "add", "ext", "--", "x"])
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
