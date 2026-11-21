"""Actor-free subscription-contract refusal; native storage has separate tests."""

from __future__ import annotations

import importlib.util
import json
import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def storage(monkeypatch: pytest.MonkeyPatch) -> ModuleType:
    path = ROOT / "plugins/agent-pull-requests/src/agent_pull_requests/watch_storage.py"
    spec = importlib.util.spec_from_file_location("promotion_watch_storage", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "_regular_bytes", lambda _path: None)

    def missing(_path: Path, **_kwargs: Any) -> Any:
        raise FileNotFoundError

    monkeypatch.setattr(Path, "stat", missing)
    return module


@pytest.mark.parametrize("phase", ["prepared", "sealed-legacy", "import-ready", "active"])
def test_recorded_transition_never_selects_either_subscription_authority(
    storage: Any, monkeypatch: pytest.MonkeyPatch, phase: str,
) -> None:
    raw = json.dumps({
        "schema": "agent-pull-requests.watch-state-transition", "version": 1, "phase": phase,
        "untrustedPath": "must-not-be-followed",
    }).encode()
    monkeypatch.setattr(storage, "_regular_bytes", lambda _path: raw)
    monkeypatch.setattr(
        Path, "read_text", lambda *_args, **_kwargs: pytest.fail("must not read a subscription file"),
    )
    monkeypatch.setattr(
        storage, "_atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("must not begin a subscription write"),
    )
    root = Path("fixture-state")
    assert storage.read_transition_phase(root) == phase
    with pytest.raises(ValueError, match="watch state transition"):
        storage.read_subscriptions_state(root)
    with pytest.raises(storage.StateWriteRefused, match="watch state transition"):
        storage.write_subscriptions_state([], root)


def test_prepared_version_two_is_observation_not_authority(
    storage: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    document = {
        "schema": "agent-pull-requests.watch-state-transition", "version": 2, "phase": "prepared",
    }
    monkeypatch.setattr(storage, "_regular_bytes", lambda _path: json.dumps(document).encode())
    monkeypatch.setattr(
        storage, "_read_subscriptions_state",
        lambda *_args, **_kwargs: pytest.fail("must not read subscription authority"),
    )
    monkeypatch.setattr(
        storage, "_atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("must not begin a subscription write"),
    )
    root = Path("fixture-state")
    for phase in ("prepared", "sealed-legacy"):
        document["phase"] = phase
        assert storage.read_transition_phase(root) == phase
        with pytest.raises(ValueError, match="watch state transition"):
            storage.read_subscriptions_state(root)
        with pytest.raises(storage.StateWriteRefused, match="watch state transition"):
            storage.write_subscriptions_state([], root)
    document["phase"] = "import-ready"
    with pytest.raises(ValueError, match="invalid watch state transition"):
        storage.read_transition_phase(root)


def test_transition_is_rechecked_at_the_final_storage_boundary(
    storage: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    current: list[bytes | None] = [None]
    monkeypatch.setattr(storage, "_regular_bytes", lambda _path: current[0])
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: "[]")

    def atomic(_path: Path, _entries: object, *, before_replace: Callable[[], None]) -> None:
        current[0] = json.dumps({
            "schema": "agent-pull-requests.watch-state-transition",
            "version": 1, "phase": "prepared",
        }).encode()
        before_replace()
        pytest.fail("a newly prepared transition must prevent replacement")

    monkeypatch.setattr(storage, "_atomic_write_json", atomic)
    with pytest.raises(storage.StateWriteRefused, match="watch state transition"):
        storage.write_subscriptions_state([], Path("fixture-state"))


@pytest.mark.parametrize("artifact", ["barrier", "retained", "staging", "reparse", "pin"])
def test_unjournaled_transition_is_not_legacy_absence(
    storage: Any, monkeypatch: pytest.MonkeyPatch, artifact: str,
) -> None:
    import stat
    from types import SimpleNamespace

    names = {
        "barrier": "agent-pull-requests-watch.lock",
        "retained": "watch-state-transition.legacy.lock",
        "staging": "watch-state",
        "reparse": "agent-pull-requests-watch.lock",
        "pin": "watch-state-transition.pin",
    }

    def info(path: Path, **_kwargs: Any) -> Any:
        if path.name != names[artifact]:
            raise FileNotFoundError
        return SimpleNamespace(
            st_mode=stat.S_IFREG if artifact in {"reparse", "pin"} else stat.S_IFDIR,
            st_file_attributes=0x400 if artifact == "reparse" else 0,
        )

    monkeypatch.setattr(Path, "stat", info)
    with pytest.raises(ValueError, match="watch state transition"):
        storage.read_subscriptions_state(Path("fixture-state"))


@pytest.mark.parametrize("document,reason", [
    pytest.param(
        {"schema": "agent-pull-requests.watch-subscriptions", "version": 2},
        "unsupported.*version", id="newer",
    ),
    pytest.param(
        {"schema": "agent-pull-requests.watch-subscriptions", "version": 999},
        "unsupported.*version", id="future",
    ),
    pytest.param(
        {"schema": "agent-pull-requests.watch-subscriptions", "version": True},
        "invalid.*version", id="invalid",
    ),
    pytest.param({"schema": "other-schema", "version": 2}, "unsupported.*schema", id="schema"),
    pytest.param(None, "invalid.*format", id="format"),
])
def test_incompatible_contract_refuses_read_and_final_write_guard(
    storage: Any, monkeypatch: pytest.MonkeyPatch, document: object, reason: str,
) -> None:
    reads: list[Path] = []

    def read(path: Path, **_kwargs: Any) -> str:
        reads.append(path)
        return json.dumps(document)

    monkeypatch.setattr(Path, "read_text", read)
    guards: list[Callable[[], None]] = []

    def atomic(_path: Path, _entries: object, *, before_replace: Callable[[], None]) -> None:
        guards.append(before_replace)

    monkeypatch.setattr(storage, "_atomic_write_json", atomic)
    root = Path("fixture-state")
    with pytest.raises(ValueError, match=reason):
        storage.read_subscriptions_state(root)
    with pytest.raises(storage.StateWriteRefused, match=reason):
        storage.write_subscriptions_state([], root)
    assert guards == []
    assert reads == [root / "watch-subscriptions.json"] * 2


@pytest.mark.parametrize(
    "raw", ["[]", '[{"subscriber_id": "retained"}]', None], ids=["empty", "legacy", "missing"],
)
def test_legacy_or_missing_state_allows_unchanged_list_write(
    storage: Any, monkeypatch: pytest.MonkeyPatch, raw: str | None,
) -> None:
    def read(_path: Path, **_kwargs: Any) -> str:
        if raw is None:
            raise FileNotFoundError
        return raw

    monkeypatch.setattr(Path, "read_text", read)
    expected = json.loads(raw) if raw is not None else []
    root = Path("fixture-state")
    assert storage.read_subscriptions_state(root) == expected
    written: list[object] = []

    def atomic(_path: Path, entries: object, *, before_replace: Callable[[], None]) -> None:
        before_replace()
        written.append(entries)

    monkeypatch.setattr(storage, "_atomic_write_json", atomic)
    storage.write_subscriptions_state(expected, root)
    assert written == [expected]


def test_guard_uses_latest_bytes_not_prior_read(
    storage: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    current = ["[]"]
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: current[0])
    root = Path("fixture-state")
    assert storage.read_subscriptions_state(root) == []

    def atomic(_path: Path, _entries: object, *, before_replace: Callable[[], None]) -> None:
        current[0] = '{"schema":"agent-pull-requests.watch-subscriptions","version":2}'
        before_replace()
        pytest.fail("incompatible state must not reach replacement")

    monkeypatch.setattr(storage, "_atomic_write_json", atomic)
    with pytest.raises(storage.StateWriteRefused, match="unsupported.*version"):
        storage.write_subscriptions_state([], root)


def test_selected_partition_update_preserves_opaque_versions_and_generation(
    storage: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = {
        "schema": "agent-pull-requests.watch-subscriptions", "version": 2,
        "engineGeneration": 1, "activeStateVersion": 1,
        "stateByVersion": {
            "1": {"subscriptions": [], "extension": ["retained"]},
            "2": {"unknown": {"pending": [{"event_id": "same-event"}]}},
        },
        "extension": {"retain": True},
    }
    current = [json.dumps(original)]
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: current[0])
    written: list[object] = []

    def atomic(_path: Path, entries: object, *, before_replace: Callable[[], None]) -> None:
        before_replace()
        written.append(entries)
        current[0] = json.dumps(entries)

    monkeypatch.setattr(storage, "_atomic_write_json", atomic)
    root = Path("fixture-state")
    assert storage.read_subscriptions_state(root) == []
    entries = [{"subscriber_id": "retained", "pending": {"event_id": "pending-id"}}]
    storage.write_subscriptions_state(entries, root, validate=lambda _entries: None)
    expected = json.loads(json.dumps(original))
    expected["stateByVersion"]["1"]["subscriptions"] = entries
    assert written == [expected]
    assert storage.read_subscriptions_state(root) == entries
    storage.write_subscriptions_state([], root, validate=lambda _entries: None)
    assert written[-1] == original


def test_final_guard_preserves_a_newer_opaque_partition(
    storage: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    document: dict[str, Any] = {
        "schema": "agent-pull-requests.watch-subscriptions", "version": 2,
        "engineGeneration": 1, "activeStateVersion": 1,
        "stateByVersion": {"1": {"subscriptions": []}, "2": {"pending": "earlier"}},
    }
    current = [json.dumps(document)]
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: current[0])

    def atomic(_path: Path, _entries: object, *, before_replace: Callable[[], None]) -> None:
        document["stateByVersion"]["2"]["pending"] = "later"
        current[0] = json.dumps(document)
        before_replace()
        pytest.fail("must not replace concurrently changed state")

    monkeypatch.setattr(storage, "_atomic_write_json", atomic)
    with pytest.raises(storage.StateWriteRefused, match="changed before replacement"):
        storage.write_subscriptions_state(
            [], Path("fixture-state"), validate=lambda _entries: None,
        )
    assert json.loads(current[0])["stateByVersion"]["2"]["pending"] == "later"


@pytest.mark.parametrize("field,value", [
    ("engineGeneration", 2), ("engineGeneration", True), ("engineGeneration", None),
    ("activeStateVersion", 2), ("activeStateVersion", True),
    ("stateByVersion", {"01": {"subscriptions": []}}),
    ("stateByVersion", {"1": {"subscriptions": {}}}),
], ids=[
    "newer-engine", "boolean-engine", "missing-engine", "newer-state",
    "boolean-state", "noncanonical-key", "non-list-partition",
])
def test_invalid_or_newer_executor_state_refuses_without_replacement(
    storage: Any, monkeypatch: pytest.MonkeyPatch, field: str, value: object,
) -> None:
    document = {
        "schema": "agent-pull-requests.watch-subscriptions", "version": 2,
        "engineGeneration": 1, "activeStateVersion": 1,
        "stateByVersion": {"1": {"subscriptions": []}},
        field: value,
    }
    raw = json.dumps(document)
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(
        storage, "_atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("refused state must not begin replacement"),
    )
    with pytest.raises(ValueError, match="persisted watch subscriptions"):
        storage.read_subscriptions_state(Path("fixture-state"))
    with pytest.raises(storage.StateWriteRefused, match="persisted watch subscriptions"):
        storage.write_subscriptions_state([], Path("fixture-state"))


def test_duplicate_partition_identity_is_not_silently_overwritten(
    storage: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = (
        '{"schema":"agent-pull-requests.watch-subscriptions","version":2,'
        '"engineGeneration":1,"activeStateVersion":1,'
        '"stateByVersion":{"1":{"subscriptions":[]},"1":{"subscriptions":[]}}}'
    )
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: raw)
    with pytest.raises(ValueError, match="invalid.*JSON"):
        storage.read_subscriptions_state(Path("fixture-state"))


def test_nonfinite_opaque_state_refuses_as_a_definite_write_failure(
    storage: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    raw = (
        '{"schema":"agent-pull-requests.watch-subscriptions","version":2,'
        '"engineGeneration":1,"activeStateVersion":1,'
        '"stateByVersion":{"1":{"subscriptions":[]},"2":{"opaque":NaN}}}'
    )
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(
        storage, "_atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("invalid JSON must refuse before serialization"),
    )
    with pytest.raises(storage.StateWriteRefused, match="invalid.*JSON"):
        storage.write_subscriptions_state([], Path("fixture-state"))


@pytest.mark.parametrize("number", [
    "0.1234567890123456789", "1e400", "1e-400",
], ids=["precision-loss", "overflow", "underflow"])
def test_opaque_number_without_lossless_round_trip_refuses_before_write(
    storage: Any, monkeypatch: pytest.MonkeyPatch, number: str,
) -> None:
    raw = (
        '{"schema":"agent-pull-requests.watch-subscriptions","version":2,'
        '"engineGeneration":1,"activeStateVersion":1,'
        '"stateByVersion":{"1":{"subscriptions":[]},"2":{"opaque":' + number + '}}}'
    )
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: raw)
    monkeypatch.setattr(
        storage, "_atomic_write_json",
        lambda *_args, **_kwargs: pytest.fail("unrepresentable state must not reach serialization"),
    )
    with pytest.raises(ValueError, match="invalid.*JSON"):
        storage.read_subscriptions_state(Path("fixture-state"))
    with pytest.raises(storage.StateWriteRefused, match="invalid.*JSON"):
        storage.write_subscriptions_state([], Path("fixture-state"))


def test_round_trippable_numbers_remain_supported_in_retained_partitions(
    storage: Any, monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = [0.1, 1.0, 1e100, -0.0, 9007199254740993]
    raw = json.dumps({
        "schema": "agent-pull-requests.watch-subscriptions", "version": 2,
        "engineGeneration": 1, "activeStateVersion": 1,
        "stateByVersion": {"1": {"subscriptions": []}, "2": {"opaque": values}},
    })
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: raw)
    written: list[object] = []

    def atomic(_path: Path, data: object, *, before_replace: Callable[[], None]) -> None:
        before_replace()
        written.append(data)

    monkeypatch.setattr(storage, "_atomic_write_json", atomic)
    storage.write_subscriptions_state(
        [], Path("fixture-state"), validate=lambda _entries: None,
    )
    assert written == [json.loads(raw)]


@pytest.mark.parametrize("stage", ["missing", "existing", "candidate", "final"])
def test_versioned_write_requires_semantic_validation_at_every_boundary(
    storage: Any, monkeypatch: pytest.MonkeyPatch, stage: str,
) -> None:
    document: dict[str, Any] = {
        "schema": "agent-pull-requests.watch-subscriptions", "version": 2,
        "engineGeneration": 1, "activeStateVersion": 1,
        "stateByVersion": {"1": {"subscriptions": []}},
    }
    if stage == "existing":
        document["stateByVersion"]["1"]["subscriptions"] = [{"bad": True}]
    current = [json.dumps(document)]
    monkeypatch.setattr(Path, "read_text", lambda *_args, **_kwargs: current[0])

    def validate(entries: list[dict[str, Any]]) -> None:
        assert isinstance(entries, storage.VersionedSubscriptions)
        if any(entry.get("bad") for entry in entries):
            raise ValueError("invalid persisted watch subscriptions registration")

    def atomic(_path: Path, _entries: object, *, before_replace: Callable[[], None]) -> None:
        assert stage == "final"
        document["stateByVersion"]["1"]["subscriptions"] = [{"bad": True}]
        current[0] = json.dumps(document)
        before_replace()
        pytest.fail("malformed registrations must never reach replacement")

    monkeypatch.setattr(storage, "_atomic_write_json", atomic)
    entries = [{"bad": True}] if stage == "candidate" else []
    with pytest.raises(storage.StateWriteRefused, match="persisted watch subscriptions"):
        storage.write_subscriptions_state(
            entries, Path("fixture-state"), validate=None if stage == "missing" else validate,
        )
