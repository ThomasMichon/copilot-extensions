"""Configured populations share one finite observer, independent of subscribers."""

import json
import sys

import pytest

from agent_pull_requests import watch_observer as observation
from agent_pull_requests.repository_contract import RepositoryIdentity
from agent_pull_requests.repository_projection import RepositoryProjection
from agent_pull_requests.watch_observer import NativeObservationBinding, ReplayObserver
from agent_pull_requests.watch_provider import decode_snapshot
from agent_pull_requests.watch_replay_store import ReplayStore
from agent_pull_requests.watch_replay import ConsumerScope


def snapshot(identity, number=7, **changes):
    value = {
        "schema": "agent-worktrees.pr-snapshot", "version": 1,
        "provider": identity.provider, "api_base": identity.endpoint, "repo": identity.repo,
        "pr": number, "pr_state": "open", "merged": False, "head_sha": "a",
        "base_ref": "dev", "mergeable": None, "checks_state": "", "checks_complete": None,
        "title": "Synthetic", "draft": False, "reviews": [], **changes,
    }
    return decode_snapshot(value, identity.repo, number)


def selected(tmp_path):
    qualifier = tmp_path / "qualifier.py"
    identities = [
        RepositoryIdentity("test", provider, endpoint, "reader", "owner/repo")
        for provider, endpoint in (("github", "https://github.com"), ("gitea", "https://forge.example"))
    ]
    bindings = tuple(NativeObservationBinding(
        identity.key, identity.repo, sys.executable, (sys.executable, str(qualifier)),
        (sys.executable,), identity, str(tmp_path / f"{identity.provider}.yaml"),
    ) for identity in identities)
    store = ReplayStore(tmp_path, "test", repository_cache=True)
    store.provision()
    qualifier.write_text(
        'import json,sys\nassert sys.argv[1:]==["pr-watch","inventory","--contract","--json"]\n'
        'print(json.dumps({"protocol":"agent-worktrees.pr-inventory/v1",'
        '"providers":["github","gitea"],"credential":"observe/per-request","effects":"none",'
        '"snapshot_request_budget":64,"snapshot_capture_bytes":1048584}))\n'
    )
    return store, identities, bindings


def authority(store):
    return {"expires_at": int(store.clock()) + 60}


def document(store):
    return json.loads(store.path.read_bytes())["repository_cache"]


def test_both_providers_discover_without_waits_share_fetches_and_query_reads_no_forge(
    tmp_path, monkeypatch,
):
    store, identities, bindings = selected(tmp_path)
    scans, reads = [], []
    def inventory(binding, **kwargs):
        scans.append(binding.account)
        return {"outcome": "complete", "numbers": [7], "pages": 2, "error": ""}
    def fetch(repo, number, command, **kwargs):
        reads.append((repo, number, kwargs["config_file"]))
        identity = next(i for i in identities if kwargs["config_file"].endswith(i.provider + ".yaml"))
        return snapshot(identity)
    monkeypatch.setattr(observation, "fetch_inventory", inventory)
    monkeypatch.setattr(observation, "fetch_snapshot", fetch)
    owner = ReplayObserver(store, bindings=bindings)
    assert store.observation_targets(**authority(store)) == ()
    owner.cycle(**authority(store))
    for identity in identities:
        query = owner.repositories.query(identity.key, **authority(store))
        assert query["inventory"]["outcome"] == "complete"
        assert query["pulls"][0]["facts"]["provider"] == identity.provider
        assert query["pulls"][0]["member"] is True
        for _ in range(10):
            owner.repositories.query(identity.key, **authority(store))
    assert len(scans) == len(reads) == 2
    before = len(document(store)["events"])
    owner.cycle(**authority(store))
    assert len(document(store)["events"]) == before
    assert len(reads) == 4
    for consumer in ("first", "second"):
        store.execute(
            ConsumerScope(consumer, identities[0].key, identities[0].repo), "register",
            {"request_id": consumer, "issued_at": store.clock(), "number": 7, "until": ("merged",)},
            **authority(store),
        )
    owner.cycle(**authority(store))
    assert len(reads) == 6  # Two repositories, not two extra subscription readers.
    restarted = ReplayStore(tmp_path, "test", repository_cache=True)
    assert RepositoryProjection(restarted, identities).query(
        identities[0].key, **authority(restarted),
    )["pulls"][0]["facts"]["head_sha"] == "a"


@pytest.mark.parametrize("terminal_state", ["closed", "open"])
def test_partial_outage_departure_unknown_then_terminal_and_timestamp_nonevents(
    tmp_path, monkeypatch, terminal_state,
):
    store, identities, bindings = selected(tmp_path)
    identity = identities[0]
    owner = ReplayObserver(store, bindings=(bindings[0],))
    scan = {"outcome": "complete", "numbers": [7], "pages": 2, "error": ""}
    current = snapshot(identity)
    monkeypatch.setattr(observation, "fetch_inventory", lambda *_a, **_kw: dict(scan))
    monkeypatch.setattr(observation, "fetch_snapshot", lambda *_a, **_kw: current)
    owner.cycle(**authority(store))
    for outcome in ("partial", "unavailable"):
        scan.update(outcome=outcome, numbers=[], error="read_unavailable")
        owner.cycle(**authority(store))
        query = owner.repositories.query(identity.key, **authority(store))
        assert query["pulls"][0]["member"] is True
        assert query["pulls"][0]["facts"]["pr_state"] == "open"
    scan.update(outcome="complete", error="", pages=1)
    current = None
    owner.cycle(**authority(store))
    query = owner.repositories.query(identity.key, **authority(store))
    assert query["pulls"][0]["member"] is False
    assert query["pulls"][0]["facts"]["pr_state"] == "open"
    assert query["pulls"][0]["error"] == "snapshot_unavailable"
    current = snapshot(identity, pr_state=terminal_state, merged=True)
    owner.cycle(**authority(store))
    assert owner.repositories.query(identity.key, **authority(store))["pulls"][0]["facts"]["merged"]
    assert "departure" in [event["kind"] for event in document(store)["events"]]
    assert "terminal" in [event["kind"] for event in document(store)["events"]]
    assert not owner.repositories.plans({identity.key: scan}, **authority(store))
    before = len(document(store)["events"])
    owner.cycle(**authority(store))
    assert len(document(store)["events"]) == before


def test_state_and_events_survive_commit_failure_together(tmp_path, monkeypatch):
    from agent_pull_requests import watch_replay_store
    store, identities, _bindings = selected(tmp_path)
    projection = RepositoryProjection(store, identities[:1])
    identity = identities[0]
    scan = {identity.key: {"outcome": "complete", "numbers": [7], "pages": 2, "error": ""}}
    snapshots = {(identity.key, identity.repo, 7): snapshot(identity)}
    before = store.path.read_bytes()
    monkeypatch.setattr(watch_replay_store, "_atomic_write_json",
                        lambda *_a: (_ for _ in ()).throw(OSError("synthetic commit failure")))
    with pytest.raises(OSError):
        projection.apply(scan, snapshots, **authority(store))
    assert store.path.read_bytes() == before
    assert not document(store)["events"] and not document(store)["repositories"]


def test_real_native_process_discovers_both_providers_without_registered_wait(tmp_path):
    root = tmp_path / "store"
    root.mkdir()
    store, identities, bindings = selected(root)
    script = tmp_path / "native.py"
    script.write_text('''import json,sys
identity=json.loads(sys.argv[1])
args=sys.argv[2:]
if args[:2]==["pr-watch","inventory"] and "--contract" in args:
 print(json.dumps({"protocol":"agent-worktrees.pr-inventory/v1","providers":["github","gitea"],"credential":"observe/per-request","effects":"none","snapshot_request_budget":64,"snapshot_capture_bytes":1048584}))
elif args[:2]==["pr-watch","inventory"]:
 print(json.dumps({"schema":"agent-worktrees.pr-inventory","version":1,"provider":identity["provider"],"api_base":identity["endpoint"],"repo":identity["repo"],"outcome":"complete","numbers":[7],"pages":2,"error":"","max_pages":4,"max_pulls":200}))
elif args[:2]==["pr-watch","snapshot"]:
 print(json.dumps({"schema":"agent-worktrees.pr-snapshot","version":1,"provider":identity["provider"],"api_base":identity["endpoint"],"repo":identity["repo"],"pr":7,"pr_state":"open","merged":False,"head_sha":"native","base_ref":"dev","mergeable":None,"checks_state":"","title":"Synthetic","draft":False,"reviews":[]}))
else:
 raise SystemExit("unexpected native operation")
''')
    from dataclasses import replace
    bindings = tuple(replace(
        binding, prefix=(sys.executable, str(script), json.dumps(identity.document())),
    ) for binding, identity in zip(bindings, identities))
    owner = ReplayObserver(store, bindings=bindings)
    owner.cycle(**authority(store))
    for identity in identities:
        assert owner.repositories.query(identity.key, **authority(store))["pulls"][0]["facts"][
            "head_sha"
        ] == "native"
    assert store.observation_targets(**authority(store)) == ()


def test_repository_snapshot_budget_rotates_without_erasing_unread_members(tmp_path, monkeypatch):
    store, identities, bindings = selected(tmp_path)
    identity = identities[0]
    reads = []
    monkeypatch.setattr(observation, "fetch_inventory", lambda *_a, **_kw: {
        "outcome": "partial", "numbers": list(range(1, 201)), "pages": 4, "error": "pull_limit",
    })
    def fetch(repo, number, command, **kwargs):
        reads.append(number)
        return snapshot(identity, number)
    monkeypatch.setattr(observation, "fetch_snapshot", fetch)
    owner = ReplayObserver(store, bindings=(bindings[0],))
    owner.cycle(**authority(store))
    assert len(reads) == 32
    rows = document(store)["repositories"][0]["pulls"]
    assert len(rows) == 200 and sum(row["facts"] is not None for row in rows) == 32
    owner.cycle(**authority(store))
    assert len(reads) == len(set(reads)) == 64


@pytest.mark.parametrize("foreign", ["endpoint", "provider"])
def test_foreign_snapshot_cannot_replace_last_confirmed_facts(tmp_path, monkeypatch, foreign):
    from dataclasses import replace
    store, identities, bindings = selected(tmp_path)
    identity = identities[0]
    monkeypatch.setattr(observation, "fetch_inventory", lambda *_a, **_kw: {
        "outcome": "complete", "numbers": [7], "pages": 2, "error": "",
    })
    current = snapshot(identity)
    monkeypatch.setattr(observation, "fetch_snapshot", lambda *_a, **_kw: current)
    owner = ReplayObserver(store, bindings=(bindings[0],))
    scope = ConsumerScope("overlapping", identity.key, identity.repo)
    store.execute(scope, "register", {
        "request_id": "wait", "issued_at": store.clock(), "number": 7, "until": ("merged",),
    }, **authority(store))
    owner.cycle(**authority(store))
    before = len(document(store)["events"])
    current = snapshot(
        replace(identity, **({foreign: "https://other.example"} if foreign == "endpoint"
                             else {foreign: "gitea"})),
        head_sha="foreign", pr_state="closed", merged=True,
    )
    owner.cycle(**authority(store))
    result = owner.repositories.query(identity.key, **authority(store))
    assert result["pulls"][0]["facts"]["head_sha"] == "a"
    assert result["pulls"][0]["error"] == "snapshot_unavailable"
    assert len(document(store)["events"]) == before
    assert store.execute(scope, "pending", {}, **authority(store))["events"] == []
    assert len(store.observation_targets(**authority(store))) == 1


def test_selected_prior_native_peer_is_explicitly_unsupported(tmp_path, monkeypatch):
    import subprocess
    from agent_pull_requests import repository_native
    store, _identities, bindings = selected(tmp_path)
    monkeypatch.setattr(repository_native, "run_command", lambda *_a, **_kw: (
        subprocess.CompletedProcess([], 2, "", "unknown inventory command")
    ))
    owner = ReplayObserver(store, bindings=(bindings[0],))
    with pytest.raises(repository_native.RepositoryNativeUnsupported):
        owner.cycle(**authority(store))
    assert not document(store)["repositories"]


def test_meaningful_head_review_and_check_changes_ignore_time_only_refresh(tmp_path, monkeypatch):
    store, identities, bindings = selected(tmp_path)
    identity = identities[0]
    monkeypatch.setattr(observation, "fetch_inventory", lambda *_a, **_kw: {
        "outcome": "complete", "numbers": [7], "pages": 2, "error": "",
    })
    current = snapshot(identity)
    monkeypatch.setattr(observation, "fetch_snapshot", lambda *_a, **_kw: current)
    owner = ReplayObserver(store, bindings=(bindings[0],))
    owner.cycle(**authority(store))
    review = {"id": 1, "state": "APPROVED", "user": "reviewer", "commit_id": "b",
              "dismissed": False, "submitted_at": "first"}
    for fields in (
        {"head_sha": "b"},
        {"head_sha": "b", "reviews": [review]},
        {"head_sha": "b", "reviews": [review], "checks_state": "failure",
         "check_failures": [{"name": "unit", "state": "failure", "source": "check-run", "url": ""}]},
        {"head_sha": "b", "reviews": [review], "checks_state": "failure",
         "check_failures": [{"name": "lint", "state": "failure", "source": "check-run", "url": ""}]},
    ):
        current = snapshot(identity, **fields)
        owner.cycle(**authority(store))
    assert [event["kind"] for event in document(store)["events"]].count("change") == 4
    before = len(document(store)["events"])
    current = snapshot(identity, **(fields | {
        "reviews": [review | {"submitted_at": "later"}], "cursor": "later-timestamp",
    }))
    owner.cycle(**authority(store))
    assert len(document(store)["events"]) == before


@pytest.mark.parametrize("field,value", [("merged", "true"), ("head_sha", True), ("unknown", 1)])
def test_corrupt_persisted_facts_refuse_without_recreation(tmp_path, field, value):
    store, identities, _bindings = selected(tmp_path)
    identity = identities[0]
    projection = RepositoryProjection(store, identities[:1])
    projection.apply(
        {identity.key: {"outcome": "complete", "numbers": [7], "pages": 2, "error": ""}},
        {(identity.key, identity.repo, 7): snapshot(identity)}, **authority(store),
    )
    state = json.loads(store.path.read_bytes())
    state["repository_cache"]["repositories"][0]["pulls"][0]["facts"][field] = value
    store.path.write_text(json.dumps(state))
    original = store.path.read_bytes()
    with pytest.raises(ValueError):
        projection.query(identity.key, **authority(store))
    assert store.path.read_bytes() == original


def test_post_replace_uncertainty_recovers_facts_and_original_event_ids_together(tmp_path, monkeypatch):
    from agent_pull_requests import watch_replay_store
    from agent_pull_requests.watch_storage import StateCommitUncertain
    store, identities, _bindings = selected(tmp_path)
    identity = identities[0]
    projection = RepositoryProjection(store, identities[:1])
    scans = {identity.key: {"outcome": "complete", "numbers": [7], "pages": 2, "error": ""}}
    snapshots = {(identity.key, identity.repo, 7): snapshot(identity)}
    original = watch_replay_store._atomic_write_json
    def uncertain(path, value):
        original(path, value)
        raise StateCommitUncertain("synthetic post-replace interruption")
    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", uncertain)
    with pytest.raises(StateCommitUncertain):
        projection.apply(scans, snapshots, **authority(store))
    identities_before = [row["event_id"] for row in document(store)["events"]]
    monkeypatch.setattr(watch_replay_store, "_atomic_write_json", original)
    restored = ReplayStore(tmp_path, "test", repository_cache=True)
    recovered = RepositoryProjection(restored, identities[:1])
    assert recovered.query(identity.key, **authority(restored))["pulls"][0]["facts"]["head_sha"] == "a"
    recovered.apply(scans, snapshots, **authority(restored))
    assert [row["event_id"] for row in document(restored)["events"]] == identities_before


@pytest.mark.parametrize("initial_outcome,expected", [("complete", "entry"), ("unavailable", "baseline")])
def test_initial_empty_complete_differs_from_never_populated_outage(
    tmp_path, monkeypatch, initial_outcome, expected,
):
    store, identities, bindings = selected(tmp_path)
    identity = identities[0]
    scan = {"outcome": initial_outcome, "numbers": [], "pages": 1 if initial_outcome == "complete" else 0,
            "error": "" if initial_outcome == "complete" else "read_unavailable"}
    monkeypatch.setattr(observation, "fetch_inventory", lambda *_a, **_kw: dict(scan))
    monkeypatch.setattr(observation, "fetch_snapshot", lambda *_a, **_kw: snapshot(identity))
    owner = ReplayObserver(store, bindings=(bindings[0],))
    owner.cycle(**authority(store))
    query = owner.repositories.query(identity.key, **authority(store))
    assert query["inventory"]["outcome"] == initial_outcome and not query["pulls"]
    scan.update(outcome="complete", numbers=[7], pages=2, error="")
    owner.cycle(**authority(store))
    population = [event for event in document(store)["events"]
                  if event["number"] == 7 and event["facts"] is None]
    assert [event["kind"] for event in population] == [expected]


@pytest.mark.parametrize("phase", ["contract", "inventory"])
def test_settled_native_timeout_records_unavailability_and_keeps_legacy_waits_serviced(
    tmp_path, monkeypatch, phase,
):
    import subprocess
    from agent_pull_requests import repository_native
    from agent_pull_requests.watch_contract import PRSnapshot
    store, identities, bindings = selected(tmp_path)
    identity = identities[0]
    projection = RepositoryProjection(store, identities[:1])
    scan = {"outcome": "complete", "numbers": [7], "pages": 2, "error": ""}
    projection.apply({identity.key: scan}, {
        (identity.key, identity.repo, 7): snapshot(identity),
    }, **authority(store))
    scope = ConsumerScope("legacy", "legacy", "other/repo")
    store.execute(scope, "register", {
        "request_id": "legacy", "issued_at": store.clock(), "number": 7, "until": ("merged",),
    }, **authority(store))
    configured_scope = ConsumerScope("configured", identity.key, identity.repo)
    store.execute(configured_scope, "register", {
        "request_id": "configured", "issued_at": store.clock(), "number": 7, "until": ("merged",),
    }, **authority(store))
    legacy = NativeObservationBinding("legacy", "other/repo", sys.executable)
    failed = True
    def run(argv, **kwargs):
        if failed and ("--contract" in argv) == (phase == "contract"):
            raise subprocess.TimeoutExpired(argv, kwargs["timeout"])
        if "--contract" in argv:
            value = {"protocol": "agent-worktrees.pr-inventory/v1", "providers": ["github", "gitea"],
                     "credential": "observe/per-request", "effects": "none",
                     "snapshot_request_budget": 64, "snapshot_capture_bytes": 1_048_584}
        else:
            value = {"schema": "agent-worktrees.pr-inventory", "version": 1,
                     "provider": identity.provider, "api_base": identity.endpoint,
                     "repo": identity.repo, "max_pages": 4, "max_pulls": 200, **scan}
        return subprocess.CompletedProcess(argv, 0, json.dumps(value), "")
    reads = []
    def fetch(repo, number, command, **kwargs):
        reads.append(repo)
        return snapshot(identity) if repo == identity.repo else PRSnapshot(
            pr_state="open", head_sha="legacy",
        )
    monkeypatch.setattr(repository_native, "run_command", run)
    monkeypatch.setattr(observation, "fetch_snapshot", fetch)
    owner = ReplayObserver(store, bindings=(bindings[0], legacy))
    assert owner.cycle(**authority(store))["status"] == "observed"
    result = owner.repositories.query(identity.key, **authority(store))
    assert result["inventory"]["outcome"] == "unavailable"
    assert result["pulls"][0]["member"] and result["pulls"][0]["facts"]["head_sha"] == "a"
    assert reads.count("other/repo") == 1
    assert reads.count(identity.repo) == (0 if phase == "contract" else 1)
    assert store.execute(configured_scope, "pending", {}, **authority(store))["events"] == []
    failed = False
    owner.cycle(**authority(store))
    assert owner.repositories.query(identity.key, **authority(store))["inventory"]["outcome"] == "complete"
    assert reads.count("other/repo") == 2
    assert reads.count(identity.repo) == (1 if phase == "contract" else 2)


def test_native_cleanup_and_authority_failures_are_not_normalized_as_settled_timeouts(tmp_path, monkeypatch):
    from agent_procutil.owned_process import OwnedCleanupError
    from agent_pull_requests import repository_native
    from agent_pull_requests.watch_replay_store import ReplayCallerExpired
    _store, _identities, bindings = selected(tmp_path)
    error = OwnedCleanupError(None)
    monkeypatch.setattr(repository_native, "run_command",
                        lambda *_a, **_kw: (_ for _ in ()).throw(error))
    with pytest.raises(OwnedCleanupError) as failure:
        repository_native.inventory(bindings[0], checkpoint=lambda: None)
    assert failure.value is error
    def expired():
        raise ReplayCallerExpired("synthetic authority withdrawal")
    with pytest.raises(ReplayCallerExpired):
        repository_native.inventory(bindings[0], checkpoint=expired)


def test_shared_configured_wait_outside_rotation_updates_current_facts_and_events(tmp_path, monkeypatch):
    import threading
    import weakref
    store, identities, bindings = selected(tmp_path)
    identity = identities[0]
    scan = {"outcome": "complete", "numbers": list(range(1, 34)), "pages": 2, "error": ""}
    projection = RepositoryProjection(store, identities[:1])
    projection.apply({identity.key: scan}, {
        (identity.key, identity.repo, number): snapshot(identity, number)
        for number in scan["numbers"]
    }, **authority(store))
    scope = ConsumerScope("shared", identity.key, identity.repo)
    store.execute(scope, "register", {
        "request_id": "member-33", "issued_at": store.clock(), "number": 33, "until": ("merged",),
    }, **authority(store))
    reads, references, peaks = [], [], []
    lock = threading.Lock()
    monkeypatch.setattr(observation, "fetch_inventory", lambda *_a, **_kw: dict(scan))
    def fetch(repo, number, command, **kwargs):
        result = snapshot(identity, number, head_sha="fresh" if number == 33 else "a")
        with lock:
            reads.append(number)
            references.append(weakref.ref(result))
            peaks.append(sum(reference() is not None for reference in references))
        return result
    monkeypatch.setattr(observation, "fetch_snapshot", fetch)
    owner = ReplayObserver(store, bindings=(bindings[0],))
    owner.cycle(**authority(store))
    assert len(reads) == len(set(reads)) == 33
    assert max(peaks) <= 12
    result = owner.repositories.query(identity.key, number=33, **authority(store))
    assert result["pulls"][0]["facts"]["head_sha"] == "fresh"
    assert len([event for event in document(store)["events"]
                if event["number"] == 33 and event["kind"] == "change"]) == 1


def test_existing_legacy_deadline_is_not_consumed_by_new_repository_acquisition(tmp_path, monkeypatch):
    from agent_pull_requests.watch_contract import PRSnapshot
    store, identities, bindings = selected(tmp_path)
    identity = identities[0]
    now = [100.0]
    store.clock = lambda: now[0]
    scope = ConsumerScope("legacy", "legacy", "other/repo")
    store.execute(scope, "register", {
        "request_id": "short", "issued_at": 100, "deadline_at": 105,
        "number": 7, "until": ("merged",),
    }, **authority(store))
    order = []
    def inventory(*_args, **_kwargs):
        order.append("inventory")
        now[0] += 10
        return {"outcome": "complete", "numbers": [7], "pages": 2, "error": ""}
    def fetch(repo, number, command, **kwargs):
        order.append(repo)
        return snapshot(identity) if repo == identity.repo else PRSnapshot(
            pr_state="closed", merged=True, head_sha="legacy",
        )
    monkeypatch.setattr(observation, "fetch_inventory", inventory)
    monkeypatch.setattr(observation, "fetch_snapshot", fetch)
    legacy = NativeObservationBinding("legacy", "other/repo", sys.executable)
    owner = ReplayObserver(store, bindings=(bindings[0], legacy))
    owner.cycle(expires_at=160)
    event = store.execute(scope, "pending", {}, **authority(store))["events"][0]
    assert not event["timed_out"] and event["transitions"] == ["merged"]
    assert order.index("other/repo") < order.index("inventory")


def test_reentry_outside_rotation_preserves_last_terminal_facts_as_explicitly_unconfirmed(
    tmp_path, monkeypatch,
):
    store, identities, bindings = selected(tmp_path)
    identity = identities[0]
    projection = RepositoryProjection(store, identities[:1])
    projection.apply(
        {identity.key: {"outcome": "complete", "numbers": [33], "pages": 2, "error": ""}},
        {(identity.key, identity.repo, 33): snapshot(identity, 33)}, **authority(store),
    )
    projection.apply(
        {identity.key: {"outcome": "complete", "numbers": [], "pages": 1, "error": ""}},
        {(identity.key, identity.repo, 33): snapshot(identity, 33, pr_state="closed")},
        **authority(store),
    )
    monkeypatch.setattr(observation, "fetch_inventory", lambda *_a, **_kw: {
        "outcome": "complete", "numbers": list(range(1, 34)), "pages": 2, "error": "",
    })
    monkeypatch.setattr(observation, "fetch_snapshot", lambda _repo, number, *_a, **_kw: (
        snapshot(identity, number, head_sha="reopened")
    ))
    owner = ReplayObserver(store, bindings=(bindings[0],))
    owner.cycle(**authority(store))
    result = owner.repositories.query(identity.key, number=33, **authority(store))["pulls"][0]
    assert result["member"] and result["facts"]["pr_state"] == "closed"
    assert result["error"] == "selection_reentry_unconfirmed"
    entries = [event for event in document(store)["events"]
               if event["number"] == 33 and event["kind"] == "entry"]
    assert len(entries) == 1 and entries[0]["facts"] is None
    owner.cycle(**authority(store))
    result = owner.repositories.query(identity.key, number=33, **authority(store))["pulls"][0]
    assert result["facts"]["pr_state"] == "open" and not result["error"]


def test_qualified_configured_wait_is_read_before_slow_inventory(tmp_path, monkeypatch):
    store, identities, bindings = selected(tmp_path)
    identity = identities[0]
    now = [100.0]
    store.clock = lambda: now[0]
    scope = ConsumerScope("configured", identity.key, identity.repo)
    store.execute(scope, "register", {
        "request_id": "short-configured", "issued_at": 100, "deadline_at": 105,
        "number": 7, "until": ("merged",),
    }, **authority(store))
    order = []
    def inventory(*_args, **_kwargs):
        order.append("inventory")
        now[0] += 10
        return {"outcome": "complete", "numbers": [], "pages": 1, "error": ""}
    def fetch(*_args, **_kwargs):
        order.append("snapshot")
        return snapshot(identity, pr_state="closed", merged=True)
    monkeypatch.setattr(observation, "fetch_inventory", inventory)
    monkeypatch.setattr(observation, "fetch_snapshot", fetch)
    owner = ReplayObserver(store, bindings=(bindings[0],))
    owner.cycle(expires_at=160)
    event = store.execute(scope, "pending", {}, **authority(store))["events"][0]
    assert not event["timed_out"] and event["transitions"] == ["merged"]
    assert order == ["snapshot", "inventory"]


def test_reopening_evidence_from_wait_updates_known_departed_summary_even_if_inventory_omits_it(
    tmp_path, monkeypatch,
):
    store, identities, bindings = selected(tmp_path)
    identity = identities[0]
    projection = RepositoryProjection(store, identities[:1])
    projection.apply(
        {identity.key: {"outcome": "complete", "numbers": [33], "pages": 2, "error": ""}},
        {(identity.key, identity.repo, 33): snapshot(identity, 33)}, **authority(store),
    )
    projection.apply(
        {identity.key: {"outcome": "complete", "numbers": [], "pages": 1, "error": ""}},
        {(identity.key, identity.repo, 33): snapshot(identity, 33, pr_state="closed")},
        **authority(store),
    )
    scope = ConsumerScope("reopened", identity.key, identity.repo)
    store.execute(scope, "register", {
        "request_id": "reopened", "issued_at": store.clock(), "number": 33, "until": ("merged",),
    }, **authority(store))
    monkeypatch.setattr(observation, "fetch_inventory", lambda *_a, **_kw: {
        "outcome": "complete", "numbers": [], "pages": 1, "error": "",
    })
    reads = []
    def fetch(*_args, **_kwargs):
        reads.append(33)
        return snapshot(identity, 33, head_sha="reopened")
    monkeypatch.setattr(observation, "fetch_snapshot", fetch)
    owner = ReplayObserver(store, bindings=(bindings[0],))
    owner.cycle(**authority(store))
    result = owner.repositories.query(identity.key, number=33, **authority(store))["pulls"][0]
    assert result["facts"]["pr_state"] == "open" and result["facts"]["head_sha"] == "reopened"
    assert not result["member"] and reads == [33]
    assert any(event["number"] == 33 and event["kind"] == "change"
               for event in document(store)["events"])


def test_qualified_wait_is_serviced_before_unrelated_second_probe(tmp_path, monkeypatch):
    store, identities, bindings = selected(tmp_path)
    now = [100.0]
    store.clock = lambda: now[0]
    scope = ConsumerScope("first", identities[0].key, identities[0].repo)
    store.execute(scope, "register", {
        "request_id": "first-short", "issued_at": 100, "deadline_at": 105,
        "number": 7, "until": ("merged",),
    }, **authority(store))
    order = []
    actual = observation.qualify_native
    def qualify(binding, **kwargs):
        actual(binding, **kwargs)
        order.append(binding.repository_identity.provider)
        if binding.account == identities[1].key:
            now[0] += 10
    monkeypatch.setattr(observation, "qualify_native", qualify)
    monkeypatch.setattr(observation, "fetch_inventory", lambda *_a, **_kw: {
        "outcome": "complete", "numbers": [], "pages": 1, "error": "",
    })
    def fetch(*_args, **_kwargs):
        order.append("snapshot")
        return snapshot(identities[0], pr_state="closed", merged=True)
    monkeypatch.setattr(observation, "fetch_snapshot", fetch)
    owner = ReplayObserver(store, bindings=bindings)
    owner.cycle(expires_at=160)
    event = store.execute(scope, "pending", {}, **authority(store))["events"][0]
    assert not event["timed_out"] and event["transitions"] == ["merged"]
    assert order == ["github", "snapshot", "gitea"]
