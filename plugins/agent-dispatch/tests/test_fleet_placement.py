"""Tests for pluggable fleet placement ordering (``fleet.placement_provider``)."""

from __future__ import annotations

import json
import os
import sys
import time
from collections.abc import Sequence
from pathlib import Path
from typing import Any, cast

import pytest
import yaml

from agent_dispatch import fleet
from agent_dispatch.client import DispatchClient
from agent_dispatch.fleet_placement import (
    PROVIDERS_ENV,
    PlacementConfigError,
    PlacementDeclaration,
    PlacementOrdering,
    PlacementProviderError,
    ProviderCommand,
    load_provider,
    parse_declaration,
    placement_from_flag,
    run_provider,
)
from agent_dispatch.pool_instances import PoolInstanceDefinition
from agent_dispatch.registrar import RegistrarError, load_declaration
from agent_dispatch.registrar_reconcile import declaration_to_registration, declaration_to_spec
from agent_dispatch.registrations import RegistrationError, RegistrationKind, validate_registration
from agent_dispatch.supervisor import Supervisor
from agent_dispatch.supervisor_registration import UnsupportedKind, _lane_flags, build_command
from tests._helpers import TEST_REPO
from tests._helpers import RepoDefaultingQueue as TaskQueue
from tests.test_direct_pool_definitions import _api, _client, _daemon
from tests.test_fleet import _create, _fake_spawn
from tests.test_supervisor import QueueBackedClient

_LANE_YAML = """
name: mixed
labels: [build]
body: {type: headless, agent: task-worker}
fleet:
  pool: [W1, cde, W2]
  origin: orig
  placement_provider: {name: devbox, hosts: [w1, W2]}
"""


class FakeRunner:
    """A provider runner that records calls and returns a fixed result."""

    def __init__(self, ordered: Sequence[str] = (), *, fail: bool = False) -> None:
        self.ordered = tuple(ordered)
        self.fail = fail
        self.calls: list[tuple[str, ...]] = []

    def __call__(self, command: ProviderCommand, candidates: Sequence[str]) -> tuple[str, ...]:
        self.calls.append(tuple(candidates))
        if self.fail:
            raise PlacementProviderError("exited 3")
        return self.ordered


def _ordering(hosts: Sequence[str], runner: FakeRunner) -> PlacementOrdering:
    return PlacementOrdering(
        PlacementDeclaration("devbox", tuple(hosts)),
        ProviderCommand(("unused",), 5.0),
        runner=runner,
    )


def _spawner(
    pool: list[str], ordering: PlacementOrdering | None, live: set[str] | None = None,
    rec: list[dict[str, Any]] | None = None,
) -> fleet.FleetSpawner:
    alive = set(pool) if live is None else live
    return fleet.FleetSpawner(
        pool, origin="orig", liveness=lambda h: h in alive, spawn_fn=_fake_spawn(rec),
        placement=ordering,
    )


# -- declaration plumbing ----------------------------------------------------


def _flag_value(argv: Sequence[str]) -> object:
    return json.loads(argv[list(argv).index("--placement-provider") + 1])


def test_yaml_declaration_reaches_argv_through_both_render_paths() -> None:
    decl = load_declaration(yaml.safe_load(_LANE_YAML))
    expected = {"name": "devbox", "hosts": ["W1", "W2"]}  # canonical pool spelling

    direct = decl.to_supervise_args()
    assert _flag_value(direct) == expected

    spec = declaration_to_spec(decl)
    assert spec["fleet"]["placement_provider"] == expected
    assert _flag_value(_lane_flags(spec)) == expected
    command = build_command(declaration_to_registration(decl, machine="host-a"), python="PY")
    assert _flag_value(command) == expected


def test_lane_without_placement_emits_no_flag() -> None:
    data = yaml.safe_load(_LANE_YAML)
    del data["fleet"]["placement_provider"]
    decl = load_declaration(data)
    assert "--placement-provider" not in decl.to_supervise_args()
    assert "--placement-provider" not in _lane_flags(declaration_to_spec(decl))


@pytest.mark.parametrize(
    "value",
    [
        {"name": "devbox", "hosts": ["W3"]},  # not in pool
        {"name": "devbox", "hosts": []},
        {"name": "devbox", "hosts": ["W1", "w1"]},
        {"name": "", "hosts": ["W1"]},
        {"name": "devbox", "hosts": ["W1"], "argv": ["evil"]},  # never from the declaration
        "devbox",
    ],
)
def test_invalid_declaration_refuses_the_lane(value: object) -> None:
    with pytest.raises(PlacementConfigError):
        parse_declaration(value, ["W1", "cde", "W2"])
    data = yaml.safe_load(_LANE_YAML)
    data["fleet"]["placement_provider"] = value
    with pytest.raises(RegistrarError):
        load_declaration(data)
    with pytest.raises(UnsupportedKind):
        _lane_flags({"fleet": {"pool": ["W1", "cde", "W2"], "placement_provider": value}})


def test_placement_without_pool_is_refused() -> None:
    with pytest.raises(UnsupportedKind):
        _lane_flags({"fleet": {"pool": [], "placement_provider": {"name": "x", "hosts": ["a"]}}})
    with pytest.raises(PlacementConfigError):
        placement_from_flag('{"name": "x", "hosts": ["a"]}', [])


# -- mixed-version admission boundary ------------------------------------------

#: The registration kinds a pre-placement supervisor daemon and coordinator know.
#: Their ``build_command`` raises ``UnsupportedKind`` (the daemon logs and skips)
#: and their ``validate_registration`` raises for any other kind.
_PRE_PLACEMENT_KINDS = frozenset(
    {"supervised-lane", "schedule", "emitter", "evaluator", "plugin-companion"}
)


def test_placement_lane_registers_under_a_kind_older_consumers_refuse() -> None:
    reg = declaration_to_registration(
        load_declaration(yaml.safe_load(_LANE_YAML)), machine="host-a"
    )
    assert reg["kind"] == RegistrationKind.PLACED_LANE
    assert reg["kind"] not in _PRE_PLACEMENT_KINDS
    validate_registration(reg["kind"], reg["spec"])
    assert PoolInstanceDefinition.from_registration(reg).registration_id == reg["id"]
    # The same unknown-kind skip an older daemon applies, through today's code.
    with pytest.raises(UnsupportedKind):
        build_command({**reg, "kind": "future-lane-kind"}, python="PY")


def test_lane_without_placement_keeps_the_baseline_kind_and_spec() -> None:
    data = yaml.safe_load(_LANE_YAML)
    del data["fleet"]["placement_provider"]
    reg = declaration_to_registration(load_declaration(data), machine="host-a")
    assert reg["kind"] == RegistrationKind.SUPERVISED_LANE
    assert reg["spec"]["fleet"] == {"pool": ["W1", "cde", "W2"], "origin": "orig", "headless": False}


def test_placement_requirement_is_bound_to_its_kind() -> None:
    placed: dict[str, Any] = {
        "repo": TEST_REPO,
        "fleet": {"pool": ["W1", "cde"], "placement_provider": {"name": "d", "hosts": ["W1"]}},
    }
    plain: dict[str, Any] = {"repo": TEST_REPO, "fleet": {"pool": ["W1", "cde"]}}
    for kind, spec in (
        (RegistrationKind.SUPERVISED_LANE, placed),
        (RegistrationKind.PLACED_LANE, plain),
        (RegistrationKind.EVALUATOR, placed),
    ):
        with pytest.raises(RegistrationError):
            validate_registration(kind, spec)
    for kind, spec in ((RegistrationKind.SUPERVISED_LANE, placed), (RegistrationKind.PLACED_LANE, plain)):
        with pytest.raises(UnsupportedKind):
            build_command({"id": "r", "kind": kind, "spec": spec}, python="PY")
    bad = {**placed, "fleet": {**placed["fleet"], "placement_provider": {"name": "d", "hosts": ["W9"]}}}
    with pytest.raises(RegistrationError):
        validate_registration(RegistrationKind.PLACED_LANE, bad)
    validate_registration(RegistrationKind.PLACED_LANE, placed)
    validate_registration(RegistrationKind.SUPERVISED_LANE, plain)


def test_placed_lanes_contribute_steering_denylists(tmp_path: Path) -> None:
    q = TaskQueue(tmp_path / "tasks.db")
    q.register_registration(RegistrationKind.PLACED_LANE, {
        "repo": TEST_REPO, "labels": ["build"], "steering_disallowed_labels": ["build"],
        "fleet": {"pool": ["W1", "cde"], "placement_provider": {"name": "d", "hosts": ["W1"]}},
    })
    assert q._steering_disallowed_labels() == {"build"}


def test_placed_lane_definition_registers_on_the_kind_gated_route(tmp_path: Path) -> None:
    decl = load_declaration({
        "name": "worker", "owner": "repo:example", "repos": "example", "labels": ["review"],
        "body": {"type": "headless", "agent": "task-worker"},
        "fleet": {"pool": ["W1", "cde"], "placement_provider": {"name": "devbox", "hosts": ["W1"]}},
    })
    registration = declaration_to_registration(decl, machine="host-a", env="isolated")
    registration["spec"]["pool_definition"] = registration.pop("pool_definition")
    calls: list[tuple[str, str]] = []
    with _api(TaskQueue(tmp_path / "tasks.db")) as api, _client(api, calls) as client:
        record = client.register_registration(
            registration["kind"], registration["spec"], reg_id=registration["id"],
            machine="host-a", env="isolated",
        )
        # Only a coordinator that knows the placed kind accepts it, so no
        # pool-definition capability probe or route is needed.
        assert calls == [("POST", "/registrations")]
        assert record["kind"] == RegistrationKind.PLACED_LANE
        daemon = _daemon(client)
        try:
            assert daemon._desired() == {registration["id"]: record}
            definition, = daemon.pool_source_snapshot().definitions
            assert definition.to_dict() == registration["spec"]["pool_definition"]
        finally:
            daemon.shutdown()


def test_declarations_cannot_spell_the_derived_kind_or_mix_an_evaluator() -> None:
    with pytest.raises(RegistrarError, match="derived"):
        load_declaration({"name": "x", "kind": RegistrationKind.PLACED_LANE, "spec": {"repo": TEST_REPO}})
    data = yaml.safe_load(_LANE_YAML)
    data["evaluator"] = "eval.yaml"
    with pytest.raises(RegistrarError, match="evaluator"):
        load_declaration(data)


# -- trusted provider config -------------------------------------------------


def _write_providers(path: Path, providers: object) -> Path:
    path.write_text(json.dumps({"providers": providers}), encoding="utf-8")
    return path


def test_provider_argv_and_timeout_come_from_trusted_config(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    cfg = _write_providers(
        tmp_path / "p.json", {"devbox": {"argv": ["py", "order.py"], "timeout_seconds": 7}},
    )
    monkeypatch.setenv(PROVIDERS_ENV, str(cfg))
    ordering = placement_from_flag('{"name": "devbox", "hosts": ["w2"]}', ["W1", "W2"])
    assert ordering is not None
    assert ordering.command == ProviderCommand(("py", "order.py"), 7.0)
    assert ordering.declaration.hosts == ("W2",)


@pytest.mark.parametrize(
    "providers",
    [
        {},  # unknown name
        {"devbox": {"argv": ["py"]}},  # missing timeout
        {"devbox": {"argv": ["py"], "timeout_seconds": 0}},
        {"devbox": {"argv": ["py"], "timeout_seconds": 301}},
        {"devbox": {"argv": [], "timeout_seconds": 5}},
        {"devbox": {"argv": ["py"], "timeout_seconds": 5, "env": {}}},
    ],
)
def test_unusable_provider_entry_is_refused(tmp_path: Path, providers: object) -> None:
    with pytest.raises(PlacementConfigError):
        load_provider("devbox", path=_write_providers(tmp_path / "p.json", providers))


def test_missing_trusted_config_is_refused(tmp_path: Path) -> None:
    with pytest.raises(PlacementConfigError):
        load_provider("devbox", path=tmp_path / "absent.json")


def test_supervise_fleet_lane_refuses_unknown_provider(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str],
) -> None:
    import argparse

    from agent_dispatch.supervise_fleet_lane import build_fleet_lane

    monkeypatch.setenv(PROVIDERS_ENV, str(_write_providers(tmp_path / "p.json", {})))
    args = argparse.Namespace(
        origin="orig", placement_provider='{"name": "devbox", "hosts": ["W1"]}',
    )
    result = build_fleet_lane(
        args, ["W1", "cde"], route=lambda _a: "route", backend="headless", all_repos=False,
        headless_labels=(), cli_labels=(), script_labels=(), disposable_cli_labels=(),
    )
    assert result == 2
    assert "not configured" in capsys.readouterr().err


# -- ordering ----------------------------------------------------------------


def test_pure_unmanaged_pool_never_calls_a_provider() -> None:
    runner = FakeRunner(["W1"])
    f = _spawner(["cde-a", "cde-b"], None)
    assert f.select({"id": "t"}) == "cde-a"
    managed_elsewhere = _spawner(["cde-a", "W1"], _ordering(["W1"], runner))
    assert managed_elsewhere.select({"id": "t", "target_machine": "cde-a"}) == "cde-a"
    assert runner.calls == []


def test_mixed_pool_merges_provider_order_into_managed_positions() -> None:
    runner = FakeRunner(["W2", "W1"])
    ordering = _ordering(["W1", "W2"], runner)
    assert ordering.order(["W1", "CDE", "W2"]) == ["W2", "CDE", "W1"]

    f = _spawner(["W1", "CDE", "W2"], ordering)
    assert f.select({"id": "t1"}) == "W2"
    assert f.select({"id": "t2"}) == "W2"
    assert runner.calls == [("W1", "W2")]  # one call per cycle, full managed set
    f.begin_placement_cycle()
    f.select({"id": "t3"})
    assert len(runner.calls) == 2  # no cross-cycle cache


def test_omitted_managed_host_takes_no_work() -> None:
    runner = FakeRunner(["W2"])
    f = _spawner(["W1", "CDE", "W2"], _ordering(["W1", "W2"], runner), live={"W1", "CDE"})
    assert f.select({"id": "t"}) == "CDE"  # W1 omitted, W2 not live


def test_provider_failure_disables_only_managed_hosts() -> None:
    runner = FakeRunner(fail=True)
    f = _spawner(["W1", "CDE", "W2"], _ordering(["W1", "W2"], runner))
    assert f.select({"id": "t"}) == "CDE"
    assert f.select({"id": "t", "target_machine": "W1"}) is None
    assert len(runner.calls) == 1


def test_hard_pin_to_managed_host_follows_the_cycle_result() -> None:
    runner = FakeRunner(["W2"])
    f = _spawner(["W1", "CDE", "W2"], _ordering(["W1", "W2"], runner), live={"W1", "CDE"})
    assert f.select({"id": "a", "target_machine": "w1"}) is None  # omitted: defer, never move
    assert f.can_spawn({"id": "a", "target_machine": "w1"}) is False
    assert f.select({"id": "b", "target_machine": "W2"}) is None  # kept but not live
    assert runner.calls == [("W1", "W2")]
    f.begin_placement_cycle()
    runner.ordered = ("W1",)
    assert f.select({"id": "a", "target_machine": "W1"}) == "W1"


def test_unavailable_pool_defers_without_reservation(tmp_path: Path) -> None:
    q = TaskQueue(tmp_path / "tasks.db")
    task = _create(q, "work")
    runner = FakeRunner([])
    rec: list[dict[str, Any]] = []
    f = _spawner(["W1", "CDE"], _ordering(["W1"], runner), live={"W1"}, rec=rec)
    sup = Supervisor(
        cast(DispatchClient, QueueBackedClient(q)), spawn_fn=f, capacity_gate=f.can_spawn,
        repo=TEST_REPO, max_concurrent=5,
    )
    assert sup.poll_once() == []
    assert sup.poll_once() == []
    assert rec == []
    assert q.latest_reservation(task.id) is None
    assert len(runner.calls) == 2  # the supervisor starts a fresh cycle each poll


# -- real provider processes -------------------------------------------------


def _script(tmp_path: Path, body: str, *, timeout: float = 20.0) -> ProviderCommand:
    path = tmp_path / "provider.py"
    path.write_text("import json, sys\n" + body, encoding="utf-8")
    return ProviderCommand((sys.executable, str(path)), timeout)


def test_run_provider_reverses_candidates(tmp_path: Path) -> None:
    command = _script(
        tmp_path,
        "req = json.load(sys.stdin)\n"
        "print(json.dumps({'version': 1, 'ordered': req['candidates'][::-1]}))\n",
    )
    assert run_provider(command, ["W1", "W2"]) == ("W2", "W1")


@pytest.mark.parametrize(
    "body",
    [
        "print('not json')\n",
        "print(json.dumps({'version': 2, 'ordered': []}))\n",
        "print(json.dumps({'version': 1, 'ordered': ['W9']}))\n",
        "print(json.dumps({'version': 1, 'ordered': ['W1', 'W1']}))\n",
        "print(json.dumps({'version': 1, 'ordered': 'W1'}))\n",
        "print(json.dumps({'version': 1, 'ordered': ['W1']}))\nsys.exit(4)\n",
    ],
)
def test_run_provider_rejects_bad_output(tmp_path: Path, body: str) -> None:
    with pytest.raises(PlacementProviderError):
        run_provider(_script(tmp_path, body), ["W1", "W2"])


def test_non_utf8_provider_output_disables_only_managed_hosts(tmp_path: Path) -> None:
    command = _script(tmp_path, "sys.stdin.read()\nsys.stdout.buffer.write(b'\\xff{}')\n")
    with pytest.raises(PlacementProviderError, match="UTF-8"):
        run_provider(command, ["W1", "W2"])
    ordering = PlacementOrdering(PlacementDeclaration("devbox", ("W1", "W2")), command)
    assert ordering.order(["W1", "CDE", "W2"]) == ["CDE"]


def _alive(pid: int) -> bool:
    if sys.platform == "win32":
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.OpenProcess(0x1000, False, pid)  # QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        code = ctypes.c_ulong()
        try:
            kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
        finally:
            kernel32.CloseHandle(handle)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    return True


def test_run_provider_timeout_cleans_up_the_process_tree(tmp_path: Path) -> None:
    pids = tmp_path / "pids.txt"
    command = _script(
        tmp_path,
        "import os, subprocess, time\n"
        "child = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(120)'])\n"
        f"open({str(pids)!r}, 'w').write(f'{{os.getpid()}} {{child.pid}}')\n"
        "time.sleep(120)\n",
        timeout=4.0,
    )
    started = time.monotonic()
    with pytest.raises(PlacementProviderError):
        run_provider(command, ["W1"])
    assert time.monotonic() - started < 15
    recorded = [int(p) for p in pids.read_text(encoding="utf-8").split()]
    deadline = time.monotonic() + 5
    while any(_alive(pid) for pid in recorded) and time.monotonic() < deadline:
        time.sleep(0.1)
    assert not any(_alive(pid) for pid in recorded)
