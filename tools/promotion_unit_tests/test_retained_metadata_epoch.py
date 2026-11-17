"""Actor-free controls for same-native metadata lineage and receipt separation."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[2]
SOURCE = ROOT / "plugins/agent-bridge/src/agent_bridge"
PACKAGE = "promotion_metadata_epoch"


class Refused(RuntimeError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@pytest.fixture
def epoch(monkeypatch: pytest.MonkeyPatch) -> Any:
    def module(name: str, **attributes: Any) -> ModuleType:
        value = ModuleType(f"{PACKAGE}.{name}")
        value.__dict__.update(attributes)
        monkeypatch.setitem(sys.modules, value.__name__, value)
        return value

    package = ModuleType(PACKAGE)
    package.__path__ = [str(SOURCE)]
    monkeypatch.setitem(sys.modules, PACKAGE, package)
    module("acquisition_contract", AcquisitionRefused=Refused)
    module("models", RetainedRecoveryRequest=SimpleNamespace(
        model_validate=lambda value: SimpleNamespace(**value),
    ))
    loaded: dict[str, ModuleType] = {}
    for name in ("retained_metadata_import", "retained_metadata_lineage"):
        spec = importlib.util.spec_from_file_location(f"{PACKAGE}.{name}", SOURCE / f"{name}.py")
        assert spec and spec.loader
        value = importlib.util.module_from_spec(spec)
        monkeypatch.setitem(sys.modules, value.__name__, value)
        spec.loader.exec_module(value)
        loaded[name] = value
    return SimpleNamespace(
        recovery=loaded["retained_metadata_lineage"],
        metadata=loaded["retained_metadata_import"],
    )


def original() -> dict[str, Any]:
    return {
        "key_digest": "original-key", "session_id": "missing-control",
        "native_session_id": "same-native", "creation_handoffs_json": None,
    }


def adopted(epoch: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    row = original()
    boundary = {
        "version": 1,
        "request": {"journal_key": row["key_digest"], "native_session_id": row["native_session_id"]},
        "predecessor_id": row["session_id"], "session_id": "new-control",
        "history": epoch.recovery.history(row["session_id"], "new-control"),
        "prior_operations": ["previous-recovery"],
    }
    row[epoch.recovery.COLUMN] = json.dumps(boundary)
    return row, boundary


def test_adopted_epoch_excludes_old_control_without_rewriting_native_history(epoch: Any) -> None:
    row, boundary = adopted(epoch)
    before = row.copy()
    assert epoch.recovery.native_for(row, "new-control") == "same-native"
    with pytest.raises(Refused, match="namespace is excluded"):
        epoch.recovery.native_for(row, "missing-control")
    assert epoch.recovery.adoption(row) == boundary
    assert boundary["history"] == {
        "status": "unavailable", "predecessor_id": "missing-control",
        "event_epoch": "new-control", "turn_epoch": "new-control",
    }
    assert row == before


def test_old_receipt_cannot_be_reinterpreted_as_adopted_execution(epoch: Any) -> None:
    row, _boundary = adopted(epoch)
    with pytest.raises(Refused, match="missing predecessor epoch"):
        epoch.recovery.require_operation_epoch(row, "new-control", "previous-recovery")
    epoch.recovery.require_operation_epoch(row, "new-control", "fresh-qualified-operation")
    assert epoch.recovery.adoption(row)["prior_operations"] == ["previous-recovery"]


@pytest.mark.parametrize(("field", "value"), [
    ("version", True), ("session_id", "missing-control"),
    ("predecessor_id", "another-control"),
    ("history", {"status": "complete"}),
    ("prior_operations", ["duplicate", "duplicate"]),
    ("request", {"journal_key": "different", "native_session_id": "same-native"}),
    ("request", {"journal_key": "original-key", "native_session_id": "fresh-native"}),
], ids=["version", "same-epoch", "predecessor", "history", "receipts", "key", "native"])
def test_corrupt_adoption_cannot_fall_back_to_birth_or_handoff(
    epoch: Any, field: str, value: Any,
) -> None:
    row, boundary = adopted(epoch)
    boundary[field] = value
    row[epoch.recovery.COLUMN] = json.dumps(boundary)
    for session_id in ("missing-control", "new-control", "unrelated"):
        with pytest.raises(Refused, match="adoption contract is unconfirmed"):
            epoch.recovery.native_for(row, session_id)


def test_unadopted_birth_and_unique_handoff_keep_existing_lineage(epoch: Any) -> None:
    row = original()
    assert epoch.recovery.native_for(row, "missing-control") == "same-native"
    row["creation_handoffs_json"] = json.dumps({
        "handoff": {"successor_id": "successor-control", "native_session_id": "successor-native"},
    })
    assert epoch.recovery.native_for(row, "successor-control") == "successor-native"
    with pytest.raises(Refused, match="original keyed lineage"):
        epoch.recovery.native_for(row, "unrelated")
    row["creation_handoffs_json"] = json.dumps({
        "first": {"successor_id": "successor-control", "native_session_id": "successor-native"},
        "second": {"successor_id": "successor-control", "native_session_id": "another-native"},
    })
    with pytest.raises(Refused, match="original keyed lineage"):
        epoch.recovery.native_for(row, "successor-control")


@pytest.mark.parametrize("changed", ["target", "native", "agent"])
def test_atomic_import_keeps_qualified_owner_inputs(epoch: Any, changed: str) -> None:
    commits: list[Any] = []
    imported = epoch.metadata.MetadataImport(
        "new-control", "target", "same-native", "agent", "caller", {}, commits.append,
    )
    imported.require("target", "same-native", "agent")
    args = {"target": "target", "native": "same-native", "agent": "agent"}
    args[changed] = "changed"
    with pytest.raises(ValueError, match="qualified owner inputs"):
        imported.require(args["target"], args["native"], args["agent"])
    assert commits == []
