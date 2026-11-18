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
    return module


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
    storage.write_subscriptions_state([], root)
    assert len(reads) == 1
    with pytest.raises(storage.StateWriteRefused, match=reason):
        guards[0]()
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
