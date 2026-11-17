"""Actual finite native-process fixture; no real backend, credential or service."""

from __future__ import annotations

import json
import sys
from collections.abc import Callable
from pathlib import Path
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
import hashlib,json,sys
from pathlib import Path
log,control=map(Path,sys.argv[1:3]);args=sys.argv[3:]
settings=json.loads(control.read_text())
with log.open("a") as stream:stream.write(json.dumps(args)+"\\n")
assert args[:2]==["pr-watch","population"]
if "--contract" in args:
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
    assert command[-7:] == ["--max-pages", "4", "--max-pulls", "200", "--seconds", "60", "--json"]
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
    ) -> dict[str, Any]:
        start_checkpoint()
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
