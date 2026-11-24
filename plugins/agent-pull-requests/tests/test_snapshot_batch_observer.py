"""Real scalar serialization crosses batch decoding, projection and replay."""

from __future__ import annotations

from dataclasses import replace
import importlib
import json
from pathlib import Path
import subprocess
import sys
import threading
import time
from typing import Any

import pytest

from agent_pull_requests import repository_native, snapshot_batch_native, watch_observer
from agent_pull_requests.repository_contract import RepositoryIdentity
from agent_pull_requests.repository_projection import RepositoryProjection
from agent_pull_requests.snapshot_batch_state import PROTOCOL, BatchAdmission, scope
from agent_pull_requests.watch_observer import NativeObservationBinding, ReplayObserver
from agent_pull_requests.watch_replay import ConsumerScope
from agent_pull_requests.watch_replay_store import ReplayStore

Document = dict[str, Any]


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> Any:
    root = Path(__file__).resolve().parents[3]
    monkeypatch.syspath_prepend(str(root / "plugins/agent-worktrees/src"))
    serializer = importlib.import_module("agent_worktrees.pr_snapshot_payload")
    snapshots = importlib.import_module("agent_worktrees.pr_snapshot")
    contract = importlib.import_module("agent_worktrees.providers.github_batch_query").contract()
    def payload(number: int, *, closed: bool = False) -> Document:
        snapshot = snapshots.PRSnapshot(
            pr_state="closed" if closed else "open", merged=closed,
            head_sha="a" * 40, base_sha="b" * 40, base_ref="dev",
            checks_state="none", checks_complete=True, reviews_complete=True,
            author="author", title="Example", updated_at="2026-01-01T00:00:00Z",
            reviews=(snapshots.Review(
                4294967301, "APPROVED", "reviewer", "2026-01-01T00:00:00Z", "a" * 40,
            ),),
        )
        return dict(serializer.snapshot_payload(
            snapshot, "owner/repository", number, "github", "https://api.github.com",
        ))
    return payload, contract


def binding(root: Path) -> NativeObservationBinding:
    identity = RepositoryIdentity("test", "github", "https://api.github.com", "reader", "owner/repository")
    return NativeObservationBinding(
        identity.key, identity.repo, sys.executable, (sys.executable,),
        (sys.executable,), identity, str(root / "native.json"), PROTOCOL,
    )


def envelope(payload: Any, numbers: tuple[int, ...], *, missing: int = 0, closed: bool = False) -> Document:
    return {
        "schema": "agent-worktrees.pr-snapshot-batch", "version": 1,
        "provider": "github", "api_base": "https://api.github.com",
        "endpoint": "https://api.github.com/graphql", "repo": "owner/repository",
        "results": [{
            "pr": n, "outcome": "unavailable" if n == missing else "complete",
            "reason": "provider-member-error" if n == missing else "",
            "snapshot": None if n == missing else payload(n, closed=closed),
        } for n in numbers], "quota": {"cost": 1, "remaining": 99, "reset_at": "2099-01-01T00:00:00Z"},
        "backoff": None, "credential_acquisitions": 1, "query_attempts": 1,
        "validated_primary_points": 1, "stop_reason": "",
    }


def setup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> tuple[ReplayStore, ReplayObserver, RepositoryProjection, list[tuple[int, ...]], Document]:
    payload, contract = native
    selected = binding(tmp_path)
    store = ReplayStore(
        tmp_path, "test", repository_cache=True, repository_continuing=True,
        repository_activity=True, github_snapshot_batches=True,
    )
    store.provision()
    owner = ReplayObserver(store, bindings=(selected,), native_read_concurrency=2)
    assert isinstance(owner.repositories, RepositoryProjection)
    calls: list[tuple[int, ...]] = []
    control: Document = {"numbers": list(range(1, 41)), "missing": 0, "closed": False, "inventories": 0}
    def run(argv: list[str], **_kwargs: Any) -> subprocess.CompletedProcess[str]:
        if "--contract" in argv:
            value = {
                "protocol": "agent-worktrees.pr-inventory/v1", "providers": ["github", "gitea"],
                "credential": "observe/per-request", "effects": "none",
                "snapshot_request_budget": 64, "snapshot_capture_bytes": 1_048_584,
                "snapshot_batch": contract,
            }
        elif argv[1:3] == ["pr-watch", "inventory"]:
            value = {
                "schema": "agent-worktrees.pr-inventory", "version": 1, "provider": "github",
                "api_base": "https://github.com", "repo": "owner/repository",
                "outcome": "complete", "numbers": control["numbers"], "pages": 1,
                "error": "", "max_pages": 4, "max_pulls": 200,
            }
        else:
            assert argv[1:3] == ["pr-watch", "snapshot-batch"]
            numbers = tuple(int(n) for n in argv[4:argv.index("--config")])
            calls.append(numbers)
            value = envelope(payload, numbers, missing=control["missing"], closed=control["closed"])
            if control.get("backoff"):
                value["backoff"] = control["backoff"]
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
    monkeypatch.setattr(repository_native, "run_command", run)
    def inventory(*_args: Any, **_kwargs: Any) -> Document:
        control["inventories"] += 1
        return {"outcome": "complete", "numbers": control["numbers"], "pages": 1, "error": ""}
    monkeypatch.setattr(watch_observer, "fetch_inventory", inventory)
    monkeypatch.setattr(watch_observer, "fetch_snapshot", lambda *_a, **_kw: pytest.fail("scalar replay"))
    return store, owner, owner.repositories, calls, control


def test_actual_serializer_decoder_fair_selection_projection_and_unchanged_replay(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    store, owner, projection, calls, control = setup(tmp_path, monkeypatch, native)
    expires = int(time.time()) + 60
    assert owner.cycle(expires_at=expires)["observed"] == 32
    assert [len(call) for call in calls] == [10, 10, 10, 2]
    assert [n for call in calls for n in call] == list(range(1, 33))
    identity = binding(tmp_path).repository_identity
    assert identity
    first = projection.query(identity.key, number=1, expires_at=expires)["pulls"][0]
    assert first["facts"]["reviews"][0]["id"] == 4294967301
    assert first["facts"]["reviews"][0]["submitted_at"] == "2026-01-01T00:00:00Z"
    assert first["facts"]["head_sha"] == "a" * 40 and first["member"]
    before = len(json.loads(store.path.read_bytes())["repository_cache"]["events"])
    owner.cycle(expires_at=expires)
    assert [n for call in calls[4:] for n in call][:8] == list(range(33, 41))
    control["numbers"] = [1]
    owner.cycle(expires_at=expires)
    count = len(json.loads(store.path.read_bytes())["repository_cache"]["events"])
    owner.cycle(expires_at=expires)
    assert len(json.loads(store.path.read_bytes())["repository_cache"]["events"]) == count
    assert count > before


def test_partial_results_retain_facts_and_terminal_closes_only_its_registered_watch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    store, owner, projection, calls, control = setup(tmp_path, monkeypatch, native)
    expires = int(time.time()) + 60
    control["numbers"] = [1, 2]
    owner.cycle(expires_at=expires)
    identity = binding(tmp_path).repository_identity
    assert identity
    selected_scope = ConsumerScope("consumer", identity.key, identity.repo)
    for number in (1, 2):
        store.execute(selected_scope, "register", {
            "request_id": str(number), "issued_at": store.clock(), "number": number, "until": ("merged",),
        }, expires_at=expires)
    control.update(missing=2, closed=True)
    assert owner.cycle(expires_at=expires)["unavailable"] == 1
    pulls = projection.query(identity.key, expires_at=expires)["pulls"]
    assert pulls[0]["facts"]["merged"] and not pulls[1]["facts"]["merged"]
    assert pulls[1]["error"] == "snapshot_unavailable"
    events = store.execute(selected_scope, "pending", {}, expires_at=expires)["events"]
    assert len(events) == 1 and events[0]["number"] == 1
    assert calls == [(1, 2), (1, 2)]


@pytest.mark.parametrize("change", ["missing", "foreign", "duplicate", "quota", "incomplete"])
def test_malformed_native_envelope_remains_unresolved_without_fallback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any, change: str,
) -> None:
    store, owner, _projection, _calls, control = setup(tmp_path, monkeypatch, native)
    control["numbers"] = [1, 2]
    value = envelope(native[0], (1, 2))
    if change == "missing":
        value["results"].pop()
    elif change == "foreign":
        value["results"][0]["snapshot"]["api_base"] = "https://foreign.example"
    elif change == "duplicate":
        value["results"][1]["pr"] = 1
    elif change == "quota":
        value["backoff"] = {"resource": "shared-secondary", "not_before": True, "reason": "throttled"}
    else:
        value["results"][0]["snapshot"]["checks_complete"] = False
    def invoke(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        kwargs["on_admitted"]()
        return subprocess.CompletedProcess([], 0, json.dumps(value), "")
    monkeypatch.setattr(snapshot_batch_native, "_invoke", invoke)
    assert owner.cycle(expires_at=int(time.time()) + 60)["observed"] == 0
    identity = binding(tmp_path).repository_identity
    assert identity
    assert not BatchAdmission(store).allowed(scope(identity), query=False, expires_at=int(time.time()) + 60)


def test_selected_old_provider_refuses_before_inventory_or_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    _store, owner, _projection, _calls, _control = setup(tmp_path, monkeypatch, native)
    native[1].pop("protocol")
    monkeypatch.setattr(watch_observer, "fetch_inventory", lambda *_a, **_kw: pytest.fail("inventory"))
    with pytest.raises(repository_native.RepositoryNativeUnsupported):
        owner.cycle(expires_at=int(time.time()) + 60)
    assert replace(binding(tmp_path), snapshot_batch=None).snapshot_batch is None


def test_batch_metadata_cannot_admit_an_unqualified_scalar_binding(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    store, _owner, _projection, _calls, _control = setup(tmp_path, monkeypatch, native)
    legacy = NativeObservationBinding("reader", "owner/repository", sys.executable)
    with pytest.raises(ValueError, match="full identities"):
        ReplayObserver(store, bindings=(legacy,))


def test_provider_without_shared_config_origin_support_refuses_before_acquisition(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    _store, owner, _projection, _calls, _control = setup(tmp_path, monkeypatch, native)
    native[1].pop("source_config_api_bases")
    monkeypatch.setattr(watch_observer, "fetch_inventory", lambda *_a, **_kw: pytest.fail("inventory"))
    with pytest.raises(repository_native.RepositoryNativeUnsupported):
        owner.cycle(expires_at=int(time.time()) + 60)


def test_stop_retains_actual_join_and_unresolved_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from agent_pull_requests.watch_replay_store import ReplayCallerExpired
    store, owner, _projection, _calls, control = setup(tmp_path, monkeypatch, native)
    control["numbers"] = [1, 2]
    entered, release, stop = threading.Event(), threading.Event(), threading.Event()
    def acquire(*_args: Any, **kwargs: Any) -> Any:
        kwargs["on_admitted"](kwargs["deadline"] - 3)
        entered.set()
        assert release.wait(5)
        kwargs["checkpoint"]()
    monkeypatch.setattr(snapshot_batch_native, "acquire", acquire)
    def check() -> None:
        if stop.is_set():
            raise ReplayCallerExpired("withdrawn")
    with ThreadPoolExecutor(max_workers=1) as pool:
        result = pool.submit(owner.cycle, expires_at=int(time.time()) + 60, checkpoint=check)
        assert entered.wait(5)
        stop.set()
        assert not result.done()
        release.set()
        with pytest.raises(ReplayCallerExpired):
            result.result(5)
    identity = binding(tmp_path).repository_identity
    assert identity
    assert not BatchAdmission(store).allowed(scope(identity), query=False, expires_at=int(time.time()) + 60)


@pytest.mark.parametrize("resource", ["graphql-primary", "shared-secondary"])
def test_observer_enforces_persisted_resource_scope_and_rollback_without_erasing_facts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any, resource: str,
) -> None:
    store, owner, projection, calls, control = setup(tmp_path, monkeypatch, native)
    control["numbers"] = [1, 2]
    expires = int(time.time()) + 60
    control["backoff"] = {"resource": resource, "not_before": time.time() + 3600, "reason": "limited"}
    assert owner.cycle(expires_at=expires)["observed"] == 2
    identity = binding(tmp_path).repository_identity
    assert identity
    before = projection.query(identity.key, number=1, expires_at=expires)["pulls"][0]["facts"]
    assert owner.cycle(expires_at=expires)["observed"] == 0
    assert len(calls) == 1
    assert control["inventories"] == (2 if resource == "graphql-primary" else 1)
    assert projection.query(identity.key, number=1, expires_at=expires)["pulls"][0]["facts"] == before
    scalar_calls: list[int] = []
    def scalar(_repo: str, number: int, *_args: Any, **_kwargs: Any) -> Any:
        from agent_pull_requests.watch_provider import decode_snapshot
        scalar_calls.append(number)
        return decode_snapshot(native[0](number) | {"api_base": "https://github.com"}, identity.repo, number)
    monkeypatch.setattr(watch_observer, "fetch_snapshot", scalar)
    rolled_back = ReplayObserver(store, bindings=(replace(binding(tmp_path), snapshot_batch=None),))
    result = rolled_back.cycle(expires_at=expires)
    assert result["observed"] == (2 if resource == "graphql-primary" else 0)
    assert scalar_calls == ([1, 2] if resource == "graphql-primary" else [])


def test_mixed_bindings_keep_batch_and_scalar_evidence_separate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    store, _owner, _projection, calls, control = setup(tmp_path, monkeypatch, native)
    control["numbers"] = [1, 2]
    github = binding(tmp_path)
    identity = RepositoryIdentity("test", "gitea", "https://forge.example", "other", github.repo)
    gitea = NativeObservationBinding(
        identity.key, identity.repo, sys.executable, (sys.executable,),
        (sys.executable,), identity, str(tmp_path / "gitea.json"),
    )
    scalar_calls: list[str] = []
    def scalar(_repo: str, number: int, *_args: Any, **kwargs: Any) -> Any:
        from agent_pull_requests.watch_provider import decode_snapshot
        scalar_calls.append(kwargs["config_file"])
        payload = native[0](number) | {"provider": "gitea", "api_base": identity.endpoint}
        return decode_snapshot(payload, identity.repo, number)
    monkeypatch.setattr(watch_observer, "fetch_snapshot", scalar)
    owner = ReplayObserver(store, bindings=(github, gitea))
    assert owner.cycle(expires_at=int(time.time()) + 60)["observed"] == 4
    assert calls == [(1, 2)] and scalar_calls == [gitea.inventory_config] * 2


def test_selected_members_pack_across_scalar_departures_without_changing_fair_progress(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    store, _owner, _projection, calls, control = setup(tmp_path, monkeypatch, native)
    github = binding(tmp_path)
    identity = RepositoryIdentity("test", "gitea", "https://forge.example", "other", github.repo)
    gitea = NativeObservationBinding(
        identity.key, identity.repo, sys.executable, (sys.executable,),
        (sys.executable,), identity, str(tmp_path / "gitea.json"),
    )
    departures = [101, 102, 103, 104]
    scalar_calls: list[int] = []

    def inventory(selected: NativeObservationBinding, **_kwargs: Any) -> Document:
        return {
            "outcome": "complete", "numbers": control["numbers"] if selected == github else departures,
            "pages": 1, "error": "",
        }

    def scalar(_repo: str, number: int, *_args: Any, **_kwargs: Any) -> Any:
        from agent_pull_requests.watch_provider import decode_snapshot
        scalar_calls.append(number)
        payload = native[0](number) | {"provider": "gitea", "api_base": identity.endpoint}
        return decode_snapshot(payload, identity.repo, number)

    monkeypatch.setattr(watch_observer, "fetch_inventory", inventory)
    monkeypatch.setattr(watch_observer, "fetch_snapshot", scalar)
    owner = ReplayObserver(store, bindings=(github, gitea), native_read_concurrency=1)
    expires = int(time.time()) + 60
    owner.cycle(expires_at=expires)
    assert scalar_calls == departures
    departures = []
    calls.clear()
    scalar_calls.clear()
    start = owner._repository_cursor % 40
    rotated = control["numbers"][start:] + control["numbers"][:start]
    assert owner.cycle(expires_at=expires)["observed"] == 32
    assert [len(call) for call in calls] == [10, 10, 8]
    assert [n for call in calls for n in call] == rotated[:28]
    assert scalar_calls == [101, 102, 103, 104]
    assert owner._repository_cursor == start + 28
    assert owner._repository_reconcile_cursor == 4
    assert isinstance(owner.repositories, RepositoryProjection) and github.repository_identity
    assert owner.repositories.members(expires_at=expires) == {
        (github.account, github.repo, number) for number in control["numbers"]
    }


@pytest.mark.parametrize("selected", [False, True])
def test_membership_hint_is_omitted_for_ordinary_calls_and_scalar_rollback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any, selected: bool,
) -> None:
    from agent_pull_requests import snapshot_batch_observer
    store, _owner, _projection, _calls, _control = setup(tmp_path, monkeypatch, native)
    configured = binding(tmp_path)
    if not selected:
        configured = replace(configured, snapshot_batch=None)
    owner = ReplayObserver(store, bindings=(configured,))
    kwargs_seen: list[Document] = []

    def consume(*_args: Any, **kwargs: Any) -> Document:
        kwargs_seen.append(kwargs)
        return {"status": "observed", "observed": 0, "unavailable": 0, "unbound": 0}

    monkeypatch.setattr(snapshot_batch_observer, "consume", consume)
    expires = int(time.time()) + 60
    owner._consume([], expires_at=expires, checkpoint=None)
    members = {(configured.account, configured.repo, 1)}
    owner._consume([], member_keys=members, cache_keys=members, expires_at=expires, checkpoint=None)
    assert "member_keys" not in kwargs_seen[0]
    assert ("member_keys" in kwargs_seen[1]) is selected
    if selected:
        assert kwargs_seen[1]["member_keys"] == members


def test_withdrawn_registration_cannot_be_revived_or_replaced_by_completed_batch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    store, owner, _projection, _calls, control = setup(tmp_path, monkeypatch, native)
    control["numbers"] = [1]
    expires = int(time.time()) + 60
    identity = binding(tmp_path).repository_identity
    assert identity
    selected_scope = ConsumerScope("consumer", identity.key, identity.repo)
    original = {"request_id": "original", "issued_at": store.clock(), "number": 1, "until": ("merged",)}
    receipt = store.execute(selected_scope, "register", original, expires_at=expires)
    def acquire(selected: NativeObservationBinding, numbers: tuple[int, ...], **_kwargs: Any) -> Any:
        _kwargs["on_admitted"](_kwargs["deadline"] - 3)
        store.execute(selected_scope, "cancel", {
            "request_id": original["request_id"], "issued_at": original["issued_at"],
            "registration_id": receipt["registration_id"],
        }, expires_at=expires)
        store.execute(selected_scope, "register", {
            "request_id": "replacement", "issued_at": store.clock(), "number": 1, "until": ("merged",),
        }, expires_at=expires)
        return snapshot_batch_native.decode(
            json.dumps(envelope(native[0], numbers, closed=True)), selected, numbers,
        )
    monkeypatch.setattr(snapshot_batch_native, "acquire", acquire)
    owner.cycle(expires_at=expires)
    assert store.execute(selected_scope, "pending", {}, expires_at=expires)["events"] == []


def test_actual_native_process_prefix_carries_batch_into_existing_projection(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    from agent_pull_requests.native_command import run_observation_command
    store, _owner, _projection, _calls, control = setup(tmp_path, monkeypatch, native)
    control["numbers"] = [1, 2]
    script, packet, log = (tmp_path / name for name in ("native.py", "packet.json", "calls.json"))
    packet.write_text(json.dumps({
        "contract": native[1], "result": envelope(native[0], (1, 2)),
    }))
    script.write_text('''import json,sys
from pathlib import Path
packet=json.loads(Path(sys.argv[1]).read_text());args=sys.argv[3:]
if args==["pr-watch","inventory","--contract","--json"]:
 print(json.dumps({"protocol":"agent-worktrees.pr-inventory/v1","providers":["github"],
  "credential":"observe/per-request","effects":"none","snapshot_request_budget":64,
  "snapshot_capture_bytes":1048584,"snapshot_batch":packet["contract"]}))
elif args[:3]==["pr-watch","inventory","owner/repository"]:
 print(json.dumps({"schema":"agent-worktrees.pr-inventory","version":1,"provider":"github",
  "api_base":"https://github.com","repo":"owner/repository","outcome":"complete",
  "numbers":[1,2],"pages":1,"error":"","max_pages":4,"max_pulls":200}))
else:
 assert args[:5]==["pr-watch","snapshot-batch","owner/repository","1","2"]
 assert "--config" in args and "--credential-command" in args and args[-1]=="--json"
 assert 1<=int(args[args.index("--seconds")+1])<=60
 assert args[args.index("--max-credential-requests")+1]=="64"
 Path(sys.argv[2]).write_text(json.dumps(args))
 print(json.dumps(packet["result"]))
''')
    selected = replace(binding(tmp_path), prefix=(sys.executable, str(script), str(packet), str(log)))
    monkeypatch.setattr(repository_native, "run_command", run_observation_command)
    monkeypatch.setattr(watch_observer, "fetch_inventory", repository_native.inventory)
    owner = ReplayObserver(store, bindings=(selected,))
    assert owner.cycle(expires_at=int(time.time()) + 60)["observed"] == 2
    assert json.loads(log.read_bytes())[3:5] == ["1", "2"]
    assert isinstance(owner.repositories, RepositoryProjection) and selected.repository_identity
    pulls = owner.repositories.query(
        selected.repository_identity.key, expires_at=int(time.time()) + 60,
    )["pulls"]
    assert [row["number"] for row in pulls if row["facts"] is not None] == [1, 2]


def test_unrepresentable_pr_does_not_poison_valid_siblings_or_reserve_unknown_work(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    store, owner, _projection, calls, control = setup(tmp_path, monkeypatch, native)
    control["numbers"] = [1, 2_147_483_648]
    expires = int(time.time()) + 60
    assert owner.cycle(expires_at=expires)["observed"] == 1
    assert calls == [(1,)]
    identity = binding(tmp_path).repository_identity
    assert identity
    assert BatchAdmission(store).allowed(scope(identity), query=True, expires_at=expires)


def test_expired_local_budget_never_leaves_a_pending_query(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    from agent_pull_requests.snapshot_batch_observer import _read
    from agent_pull_requests.watch_replay import ObservationTarget
    store, owner, _projection, calls, _control = setup(tmp_path, monkeypatch, native)
    selected = binding(tmp_path)
    target = ObservationTarget(selected.account, selected.repo, 1, frozenset())
    assert _read(
        owner, (target,), selected, stopped=threading.Event(),
        expires_at=int(time.time()) + 60, checkpoint=None, deadline=time.monotonic() + 1,
    ) == [(target, None)]
    assert not calls and selected.repository_identity
    assert BatchAdmission(store).allowed(
        scope(selected.repository_identity), query=True, expires_at=int(time.time()) + 60,
    )


@pytest.mark.parametrize("reason", [
    "credential-or-transport-unavailable", "invalid-provider-evidence",
    "provider-unavailable", "operation-budget-exhausted",
])
def test_valid_failure_envelope_cannot_clear_an_unknown_provider_outcome(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any, reason: str,
) -> None:
    store, owner, _projection, _calls, control = setup(tmp_path, monkeypatch, native)
    control["numbers"] = [1, 2]
    value = envelope(native[0], (1, 2), missing=2)
    value["stop_reason"] = reason
    def invoke(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        kwargs["on_admitted"]()
        return subprocess.CompletedProcess([], 0, json.dumps(value), "")
    monkeypatch.setattr(snapshot_batch_native, "_invoke", invoke)
    assert owner.cycle(expires_at=int(time.time()) + 60)["observed"] == 1
    identity = binding(tmp_path).repository_identity
    assert identity
    assert not BatchAdmission(store).allowed(
        scope(identity), query=False, expires_at=int(time.time()) + 60,
    )


@pytest.mark.parametrize("endpoint", ["https://github.com", "https://api.github.com"])
def test_native_inventory_web_origin_reaches_batch_projection_without_rekeying(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any, endpoint: str,
) -> None:
    store, _owner, _projection, calls, control = setup(tmp_path, monkeypatch, native)
    control["numbers"] = [1, 2]
    selected = binding(tmp_path)
    assert selected.repository_identity
    identity = replace(selected.repository_identity, endpoint=endpoint)
    selected = replace(selected, account=identity.key, repository_identity=identity)
    monkeypatch.setattr(watch_observer, "fetch_inventory", repository_native.inventory)
    owner = ReplayObserver(store, bindings=(selected,))
    assert owner.cycle(expires_at=int(time.time()) + 60)["observed"] == 2
    assert isinstance(owner.repositories, RepositoryProjection)
    result = owner.repositories.query(identity.key, expires_at=int(time.time()) + 60)
    assert result["inventory"]["outcome"] == "complete"
    assert [row["number"] for row in result["pulls"] if row["member"] and row["facts"]] == [1, 2]
    assert calls == [(1, 2)]


def test_native_cutoff_is_rechecked_after_actual_replay_lock_contention(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any,
) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from agent_pull_requests import snapshot_batch_state
    from agent_pull_requests.snapshot_batch_observer import _read
    from agent_pull_requests.watch_replay import ObservationTarget
    store, owner, _projection, calls, _control = setup(tmp_path, monkeypatch, native)
    selected = binding(tmp_path)
    target = ObservationTarget(selected.account, selected.repo, 1, frozenset())
    deadline = time.monotonic() + 30
    native_clock = [time.monotonic()]
    monkeypatch.setattr(snapshot_batch_state, "monotonic", lambda: native_clock[0])
    attempted = threading.Event()
    original_lock = store._lock
    def contended_lock() -> Any:
        attempted.set()
        return original_lock()
    with ThreadPoolExecutor(max_workers=1) as pool:
        with original_lock():
            monkeypatch.setattr(store, "_lock", contended_lock)
            future = pool.submit(
                _read, owner, (target,), selected, stopped=threading.Event(),
                expires_at=int(time.time()) + 60, checkpoint=None, deadline=deadline,
            )
            assert attempted.wait(5)
            native_clock[0] = deadline + 1
        assert future.result(5) == [(target, None)]
    assert not calls
    assert json.loads(store.path.read_bytes())["github_snapshot_batches"]["scopes"] == []


@pytest.mark.parametrize("refusal", ["proven-prelaunch", "opaque-sdk-timeout"])
def test_only_proven_local_prelaunch_refusal_releases_admission(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any, refusal: str,
) -> None:
    from types import SimpleNamespace
    from agent_pull_requests import native_command
    from agent_pull_requests.snapshot_batch_observer import _read
    from agent_pull_requests.watch_replay import ObservationTarget
    store, owner, _projection, _calls, _control = setup(tmp_path, monkeypatch, native)
    selected = binding(tmp_path)
    target = ObservationTarget(selected.account, selected.repo, 1, frozenset())
    deadline = time.monotonic() + 30
    entered: list[bool] = []
    def sdk(*_args: Any, **_kwargs: Any) -> Any:
        entered.append(True)
        raise subprocess.TimeoutExpired([], 30)
    monkeypatch.setattr(native_command, "run_caller_owned", sdk)
    monkeypatch.setattr(repository_native, "run_command", native_command.run_observation_command)
    if refusal == "proven-prelaunch":
        monkeypatch.setattr(native_command, "time", SimpleNamespace(monotonic=lambda: deadline + 1))
    assert _read(
        owner, (target,), selected, stopped=threading.Event(),
        expires_at=int(time.time()) + 60, checkpoint=None, deadline=deadline,
    ) == [(target, None)]
    assert entered == ([] if refusal == "proven-prelaunch" else [True])
    assert selected.repository_identity
    assert BatchAdmission(store).allowed(
        scope(selected.repository_identity), query=False, expires_at=int(time.time()) + 60,
    ) == (refusal == "proven-prelaunch")


@pytest.mark.parametrize("damage", ["none", "ordinary-failure", "attempt", "quota", "completed"])
def test_selected_prefix_can_report_proven_no_start_without_inventing_provider_backoff(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, native: Any, damage: str,
) -> None:
    store, owner, _projection, _calls, control = setup(tmp_path, monkeypatch, native)
    control["numbers"] = [1, 2]
    value = envelope(native[0], (1, 2))
    for row in value["results"]:
        row.update(outcome="unavailable", reason="owner-allowance-denied", snapshot=None)
    value.update(
        stop_reason="owner-prelaunch-refused", quota=None, backoff=None,
        credential_acquisitions=0, query_attempts=0, validated_primary_points=0,
    )
    if damage == "ordinary-failure":
        value["stop_reason"] = "operation-budget-exhausted"
    elif damage == "attempt":
        value["query_attempts"] = 1
    elif damage == "quota":
        value["quota"] = {"cost": 1, "remaining": 99, "reset_at": "2099-01-01T00:00:00Z"}
    elif damage == "completed":
        value["results"][0].update(outcome="complete", reason="", snapshot=native[0](1))
    reads: list[bool] = []
    def invoke(*_args: Any, **kwargs: Any) -> subprocess.CompletedProcess[str]:
        kwargs["on_admitted"]()
        reads.append(True)
        return subprocess.CompletedProcess([], 0, json.dumps(value), "")
    monkeypatch.setattr(snapshot_batch_native, "_invoke", invoke)
    expires = int(time.time()) + 60
    owner.cycle(expires_at=expires)
    assert reads == [True]
    identity = binding(tmp_path).repository_identity
    assert identity
    assert BatchAdmission(store).allowed(scope(identity), query=False, expires_at=expires) == (damage == "none")
    if damage == "none":
        owner.cycle(expires_at=expires)
        assert reads == [True, True]
