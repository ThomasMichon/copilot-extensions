"""Actual finite native-process fixture; no real backend, credential or service."""

from __future__ import annotations

import json
import sys
import time
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from subprocess import CompletedProcess
from types import SimpleNamespace
from typing import Any, cast

import pytest
from pr_filters import PROTOCOL, Expression
from pr_filters.acquisition import PROTOCOL as ACQUISITION_PROTOCOL

from agent_pull_requests.filtered_native import population, qualify
from agent_pull_requests.filtered_projection import FilteredProjection
from agent_pull_requests.filtered_subscriptions import FilteredSubscriptions
from agent_pull_requests.repository_contract import RepositoryIdentity
from agent_pull_requests.repository_native import (
    RepositoryNativeUnavailable,
    RepositoryNativeUnsupported,
)
from agent_pull_requests.watch_observer import NativeObservationBinding, ReplayObserver
from agent_pull_requests.watch_replay_store import ReplayStore

Native = tuple[NativeObservationBinding, Path, Path]

SUPPORT: dict[str, Any] = {
    "protocol": PROTOCOL, "fields": ["repo", "from", "age", "publication", "assigned", "tags"],
           "groups": [], "age_fields": ["created"], "acquisition": {
               "protocol": ACQUISITION_PROTOCOL, "user_ids": "uuid-lowercase",
               "query_fields": {"from": "creatorId", "assigned": "reviewerId"}, "max_query_branches": 64,
           }}


@pytest.fixture
def native(tmp_path: Path) -> Native:
    script, log, control = tmp_path / "native.py", tmp_path / "calls.jsonl", tmp_path / "control.json"
    control.write_text("{}")
    script.write_text('''
import hashlib,json,sys,time
from pathlib import Path
log,control=map(Path,sys.argv[1:3]);args=sys.argv[3:]
settings=json.loads(control.read_text())
with log.open("a") as stream:stream.write(json.dumps(args)+"\\n")
assert args[:2]==["pr-watch","population"]
if "--contract" in args:
 time.sleep(settings.get("qualification_delay",0))
 value={"protocol":"agent-worktrees.pr-population/v1",
 "filter_protocol":"agent-pull-requests.filters/v1",
 "providers":{"azure-devops":{"protocol":"agent-pull-requests.filters/v1",
 "fields":["repo","from","age","publication","assigned","tags"],"groups":[],"age_fields":["created"]}},
 "credential":"observe/per-request","effects":"read-only","max_pages":20,"max_pulls":1000,
 "max_requests":64,"max_seconds":60,"max_capture_bytes":1048584,"max_json_bytes":1048576}
 value["query_rotation"]="offset/v1"
 value["providers"]["azure-devops"]["acquisition"]={
 "protocol":"agent-pull-requests.filter-acquisition/v1","user_ids":"uuid-lowercase",
 "query_fields":{"from":"creatorId","assigned":"reviewerId"},"max_query_branches":64}
 if settings.get("unsupported"):value["effects"]="unknown"
else:
 assert int(args[args.index("--seconds")+1])<=settings.get("max_seconds",60)
 time.sleep(settings.get("population_delay",0))
 assert args[2]=="Project/repo"
 path=Path(args[args.index("--filter-file")+1]);assert path.is_absolute()
 raw=path.read_bytes();assert len(raw)<=32768
 if settings.get("unavailable"):raise SystemExit(3)
 revision=hashlib.sha256(raw).hexdigest()
 value={"schema":"agent-worktrees.pr-population","version":1,"provider":"azure-devops",
 "api_base":"https://dev.azure.com/example","repo":"Project/repo","filter_revision":revision,
 "outcome":"complete","pages":1,"max_pages":4,"max_pulls":200,"error":"","pulls":[{
 "number":7,"facts":{"repo":"Project/repo","from":["alice"],"assigned":None,"tags":None,
 "created_at":95,"activity_at":None,"publication":"published"},"match":"match"}]}
 if settings.get("foreign"):value["filter_revision"]="foreign"
 if settings.get("partial"):value.update(outcome="partial",error="deadline_exhausted")
print(json.dumps(value))
''')
    identity = RepositoryIdentity("test", "azure-devops", "https://dev.azure.com/example", "reader", "Project/repo")
    prefix = sys.executable, "-I", str(script), str(log), str(control)
    binding = NativeObservationBinding(
        identity.key, identity.repo, sys.executable, prefix, (sys.executable, "-I", "credential-fixture"),
        identity, str(tmp_path / "native.yaml"),
    )
    return binding, log, control


def test_native_process_exact_scope_input_lifetime_and_driver_refusal(
    native: Native, tmp_path: Path,
) -> None:
    binding, log, control = native
    selected = Expression.parse({"protocol": PROTOCOL, "where": {"repo": ["Project/repo"]}})
    support = qualify(binding, checkpoint=lambda: None)
    assert support == SUPPORT
    result = population(binding, selected, root=tmp_path, checkpoint=lambda: None)
    assert result["filter_revision"] == selected.revision and result["pulls"][0]["number"] == 7
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    command = calls[-1]
    assert command[command.index("--config") + 1] == binding.inventory_config
    assert json.loads(command[command.index("--credential-command") + 1]) == list(
        cast(tuple[str, ...], binding.credential_command),
    )
    assert command[-7:-2] == ["--max-pages", "4", "--max-pulls", "200", "--seconds"]
    assert 1 <= int(command[-2]) <= 55 and command[-1] == "--json"
    assert not Path(command[command.index("--filter-file") + 1]).exists()
    control.write_text(json.dumps({"unsupported": True}))
    with pytest.raises(RepositoryNativeUnsupported):
        population(binding, selected, root=tmp_path, checkpoint=lambda: None)
    assert "--contract" in json.loads(log.read_text().splitlines()[-1])


def test_unavailable_and_wrong_revision_never_broaden_or_leave_an_input_file(
    native: Native, tmp_path: Path,
) -> None:
    binding, _log, control = native
    selected = Expression.parse({"protocol": PROTOCOL, "where": {"repo": ["Project/repo"]}})
    for settings in ({"unavailable": True}, {"foreign": True}):
        control.write_text(json.dumps(settings))
        with pytest.raises(RepositoryNativeUnavailable):
            population(binding, selected, root=tmp_path, checkpoint=lambda: None)
        assert not list(tmp_path.glob(".population-*.json"))


def test_population_refuses_insufficient_lifetime_without_launching(
    native: Native, tmp_path: Path,
) -> None:
    binding, log, _control = native
    selected = Expression.parse({"protocol": PROTOCOL, "where": {"repo": ["Project/repo"]}})
    captured: list[bool] = []
    with pytest.raises(RepositoryNativeUnavailable):
        population(binding, selected, root=tmp_path, checkpoint=lambda: None,
                   deadline=time.monotonic() + 1, on_capture=lambda: captured.append(True))
    assert not captured
    assert not log.exists()
    assert not list(tmp_path.glob(".population-*.json"))


def test_slow_qualification_consumes_population_budget(
    native: Native, tmp_path: Path,
) -> None:
    binding, log, control = native
    control.write_text(json.dumps({"qualification_delay": 1.1, "max_seconds": 3, "partial": True}))
    selected = Expression.parse({"protocol": PROTOCOL, "where": {"repo": ["Project/repo"]}})
    result = population(binding, selected, root=tmp_path, checkpoint=lambda: None,
                        deadline=time.monotonic() + 10)
    assert result["outcome"] == "partial" and result["pulls"][0]["number"] == 7
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 2
    assert 1 <= int(calls[-1][calls[-1].index("--seconds") + 1]) <= 3
    assert not list(tmp_path.glob(".population-*.json"))


@pytest.mark.parametrize("phase", ["qualification_delay", "population_delay"])
def test_population_timeout_settles_before_enclosing_deadline(
    native: Native, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    from agent_pull_requests import filtered_native

    binding, log, control = native
    control.write_text(json.dumps({phase: 30}))
    if phase == "population_delay":
        # Isolate capture timeout from qualification's independent startup cost.
        monkeypatch.setattr(filtered_native, "qualify", lambda *_a, **_kw: SUPPORT)
    selected = Expression.parse({"protocol": PROTOCOL, "where": {"repo": ["Project/repo"]}})
    deadline = time.monotonic() + 8
    captured: list[bool] = []
    with pytest.raises(RepositoryNativeUnavailable):
        population(binding, selected, root=tmp_path, checkpoint=lambda: None, deadline=deadline,
                   on_capture=lambda: captured.append(True))
    assert time.monotonic() < deadline
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert len(calls) == 1
    assert ("--contract" in calls[0]) is (phase == "qualification_delay")
    assert captured == ([True] if phase == "population_delay" else [])
    assert not list(tmp_path.glob(".population-*.json"))


@pytest.mark.parametrize("delay, seconds", [(2, 3), (5, None)])
def test_capture_allowance_is_revalidated_after_blocking_interest_checkpoint(
    native: Native, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
    delay: int, seconds: int | None,
) -> None:
    from agent_pull_requests import filtered_native, repository_native

    binding, _log, _control = native
    identity = cast(RepositoryIdentity, binding.repository_identity)
    selected = Expression.parse({"protocol": PROTOCOL, "where": {"repo": [binding.repo]}})
    elapsed = [100.0]
    clock = SimpleNamespace(monotonic=lambda: elapsed[0])
    monkeypatch.setattr(filtered_native, "time", clock)
    monkeypatch.setattr(repository_native, "time", clock)
    monkeypatch.setattr(filtered_native, "qualify", lambda *_a, **_kw: SUPPORT)
    checks: list[bool] = []
    captures: list[bool] = []
    allowances: list[int] = []

    def interested() -> None:
        if not checks:
            elapsed[0] += delay
        checks.append(True)

    def run(argv: list[str], **_kwargs: object) -> CompletedProcess[str]:
        allowances.append(int(argv[argv.index("--seconds") + 1]))
        value = {
            "schema": "agent-worktrees.pr-population", "version": 1,
            "provider": identity.provider, "api_base": identity.endpoint, "repo": identity.repo,
            "filter_revision": selected.revision, "outcome": "partial", "pages": 1,
            "max_pages": 4, "max_pulls": 200, "error": "deadline_exhausted", "pulls": [],
        }
        return CompletedProcess(argv, 0, json.dumps(value), "")

    monkeypatch.setattr(repository_native, "run_command", run)
    if seconds is None:
        with pytest.raises(RepositoryNativeUnavailable):
            population(binding, selected, root=tmp_path, checkpoint=interested,
                       start_checkpoint=lambda: None, deadline=110,
                       on_capture=lambda: captures.append(True))
        assert not allowances and not captures
    else:
        result = population(binding, selected, root=tmp_path, checkpoint=interested,
                            start_checkpoint=lambda: None, deadline=110,
                            on_capture=lambda: captures.append(True))
        assert result["outcome"] == "partial"
        assert allowances == [seconds] and captures == [True]
    assert checks
    assert not list(tmp_path.glob(".population-*.json"))


def test_observer_publishes_partial_population_within_original_authority(
    native: Native, tmp_path: Path,
) -> None:
    binding, log, control = native
    control.write_text(json.dumps({"max_seconds": 4, "population_delay": 1, "partial": True}))
    started = time.monotonic()
    store = ReplayStore(tmp_path / "state", "test",
                        clock=lambda: 100 + time.monotonic() - started,
                        repository_cache=True, repository_continuing=True, repository_filtered=True)
    store.provision()
    observer = ReplayObserver(store, bindings=(binding,))
    projection = cast(FilteredProjection, observer.repositories)
    projection.capabilities[binding.account] = SUPPORT
    registry = FilteredSubscriptions(projection)
    subscribed = registry.execute(binding.account, "consumer", "subscribe", {
        "request_id": "request", "ttl": 60,
        "filter": {"protocol": PROTOCOL, "where": {"repo": ["Project/repo"]}},
    }, expires_at=10000)
    assert observer.cycle(expires_at=110)["observed"] == 1
    page = registry.execute(binding.account, "consumer", "query", {
        "registration_id": subscribed["subscription"]["registration_id"],
    }, expires_at=110)
    assert page["baseline"]["inventory"]["outcome"] == "partial"
    assert page["baseline"]["pulls"][0]["number"] == 7
    assert store.clock() < 110
    command = json.loads(log.read_text().splitlines()[-1])
    assert 1 <= int(command[command.index("--seconds") + 1]) <= 4


def test_observer_has_no_provider_reads_for_inert_definitions_and_coalesces_live_demand(
    native: Native, tmp_path: Path,
) -> None:
    binding, log, _control = native
    store = ReplayStore(tmp_path / "state", "test", clock=lambda: 100,
                        repository_cache=True, repository_continuing=True, repository_filtered=True)
    store.provision()
    observer = ReplayObserver(store, bindings=(binding,))
    projection = cast(FilteredProjection, observer.repositories)
    projection.capabilities[binding.account] = SUPPORT
    registry = FilteredSubscriptions(projection)
    assert observer.cycle(expires_at=10000)["observed"] == 0
    assert not log.exists()
    for consumer in ("first", "second"):
        registry.execute(binding.account, consumer, "subscribe", {
            "request_id": "request", "ttl": 60,
            "filter": {"protocol": PROTOCOL, "where": {"repo": ["Project/repo"]}},
        }, expires_at=10000)
    assert observer.cycle(expires_at=10000)["observed"] == 1
    calls = [json.loads(line) for line in log.read_text().splitlines()]
    assert sum("--filter-file" in command for command in calls) == 1
    observer.cycle(expires_at=10000)
    acquisitions = [json.loads(line) for line in log.read_text().splitlines() if "--filter-file" in line]
    assert [command[command.index("--query-offset") + 1] for command in acquisitions] == ["0", "1"]


@pytest.mark.parametrize("fail_first", [False, True])
def test_query_rotation_does_not_skip_branches_when_another_repository_uses_the_deadline(
    native: Native, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fail_first: bool,
) -> None:
    from agent_pull_requests import filtered_native, watch_observer

    first, _log, _control = native
    identity = replace(cast(RepositoryIdentity, first.repository_identity), repo="Project/other")
    second = replace(first, account=identity.key, repo=identity.repo, repository_identity=identity)
    elapsed = [0.0]
    monkeypatch.setattr(watch_observer, "time", SimpleNamespace(
        monotonic=lambda: elapsed[0], sleep=time.sleep,
    ))
    store = ReplayStore(tmp_path / "state", "test", clock=lambda: 100 + elapsed[0],
                        repository_cache=True, repository_continuing=True, repository_filtered=True)
    store.provision()
    observer = ReplayObserver(store, bindings=(first, second))
    projection = cast(FilteredProjection, observer.repositories)
    registry = FilteredSubscriptions(projection)
    offsets: dict[str, list[int]] = {}
    for binding in (first, second):
        projection.capabilities[binding.account] = SUPPORT
        offsets[binding.repo] = []
        registry.execute(binding.account, "consumer", "subscribe", {
            "request_id": binding.account, "ttl": 60,
            "filter": {"protocol": PROTOCOL, "where": {"from": {"users": [
                "11111111-1111-1111-1111-111111111111",
                "22222222-2222-2222-2222-222222222222",
            ]}}},
        }, expires_at=10000)

    def acquire(
        binding: NativeObservationBinding, _expression: Expression, *, deadline: float,
        query_offset: int, on_capture: Callable[[], None] | None = None, **_kwargs: object,
    ) -> dict[str, Any]:
        if deadline - elapsed[0] < 5:
            raise RepositoryNativeUnavailable(qualified=True)
        if on_capture is not None:
            on_capture()
        offsets[binding.repo].append(query_offset)
        elapsed[0] += 7
        if fail_first and sum(map(len, offsets.values())) == 1:
            raise RepositoryNativeUnavailable(qualified=True)
        return {"outcome": "partial", "error": "deadline_exhausted", "pages": 1, "pulls": []}

    monkeypatch.setattr(filtered_native, "population", acquire)
    for _ in range(4):
        observer.cycle(expires_at=int(store.clock()) + 10)
    assert offsets == {first.repo: [0, 1], second.repo: [0, 1]}


@pytest.mark.parametrize("withdraw_all", [False, True])
def test_inflight_read_keeps_results_for_remaining_captured_demand(
    native: Native, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, withdraw_all: bool,
) -> None:
    from agent_pull_requests import filtered_native

    binding, _log, _control = native
    store = ReplayStore(tmp_path / "state", "test", clock=lambda: 100,
                        repository_cache=True, repository_continuing=True, repository_filtered=True)
    store.provision()
    observer = ReplayObserver(store, bindings=(binding,))
    projection = cast(FilteredProjection, observer.repositories)
    projection.capabilities[binding.account] = SUPPORT
    registry = FilteredSubscriptions(projection)
    registrations: dict[str, str] = {}
    for consumer in ("first", "second"):
        result = registry.execute(binding.account, consumer, "subscribe", {
            "request_id": "request", "ttl": 60,
            "filter": {"protocol": PROTOCOL, "where": {"repo": ["Project/repo"]}},
        }, expires_at=10000)
        registrations[consumer] = result["subscription"]["registration_id"]
    reads: list[str] = []
    def acquire(
        _binding: NativeObservationBinding, _expression: Expression, *, root: Path,
        checkpoint: Callable[[], None], start_checkpoint: Callable[[], None], query_offset: int,
        deadline: float, on_capture: Callable[[], None],
    ) -> dict[str, Any]:
        start_checkpoint()
        on_capture()
        reads.append("once")
        for consumer in ("first", "second") if withdraw_all else ("first",):
            registry.execute(binding.account, consumer, "cancel", {
                "registration_id": registrations[consumer],
            }, expires_at=10000)
        checkpoint()
        return {"outcome": "complete", "error": "", "pages": 1, "pulls": [{
            "number": 7, "facts": {"repo": "Project/repo", "from": None, "assigned": None, "tags": None,
                                 "created_at": 95, "activity_at": None, "publication": "published"},
        }]}
    monkeypatch.setattr(filtered_native, "population", acquire)
    assert observer.cycle(expires_at=10000)["observed"] == (0 if withdraw_all else 1)
    assert reads == ["once"]
    for consumer in ("first", "second"):
        result = registry.execute(binding.account, consumer, "events", {
            "registration_id": registrations[consumer],
        }, expires_at=10000)
        assert bool(result["events"]) is (consumer == "second" and not withdraw_all)


def test_expired_sponsor_conflict_does_not_stop_admitted_dynamic_observation(
    native: Native, tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    from pr_filters import Definitions

    from agent_pull_requests import filtered_native
    from agent_pull_requests.watch_service import WatchService

    binding, _log, _control = native
    now = [100]
    store = ReplayStore(tmp_path / "state", "test", clock=lambda: now[0],
                        repository_cache=True, repository_continuing=True, repository_filtered=True)
    store.provision()
    observer = ReplayObserver(store, bindings=(binding,))
    projection = cast(FilteredProjection, observer.repositories)
    projection.capabilities[binding.account] = SUPPORT
    users = [f"{n:08x}-1111-1111-1111-111111111111" for n in range(128)]
    projection.definitions = Definitions.parse({
        "protocol": PROTOCOL, "sets": {"sponsor": {
            "protocol": PROTOCOL, "where": {"from": {"users": users[:64]}},
        }},
    })
    registry = FilteredSubscriptions(projection)
    registry.execute(binding.account, "sponsor", "subscribe", {
        "request_id": "startup", "ttl": 1, "definition": "sponsor",
    }, expires_at=10000)
    now[0] = 102
    dynamic = registry.execute(binding.account, "dynamic", "subscribe", {
        "request_id": "dynamic", "ttl": 60,
        "filter": {"protocol": PROTOCOL, "where": {"from": {"users": users[64:]}}},
    }, expires_at=10000)
    reads: list[Expression] = []
    def acquire(
        _binding: NativeObservationBinding, expression: Expression, **kwargs: object,
    ) -> dict[str, Any]:
        reads.append(expression)
        return {"outcome": "complete", "error": "", "pages": 1, "pulls": []}
    monkeypatch.setattr(filtered_native, "population", acquire)
    service = SimpleNamespace(
        store=store, observer=observer, _authority=lambda: 10000, _check=lambda: None,
        filter_sponsors=({"principal": "sponsor", "target": "project", "request_id": "startup",
                          "ttl": 1, "definition": "sponsor"},),
        gateway=SimpleNamespace(bindings={("sponsor", "project"): SimpleNamespace(
            account=binding.account, consumer="sponsor",
        )}),
    )
    result = WatchService._observe_cycle(cast(WatchService, service))
    assert result["status"] == "observed" and result["sponsor_conflicts"] == 1
    assert len(reads) == 1
    assert registry.execute(binding.account, "dynamic", "inspect", {
        "registration_id": dynamic["subscription"]["registration_id"],
    }, expires_at=10000)["status"] == "active"
