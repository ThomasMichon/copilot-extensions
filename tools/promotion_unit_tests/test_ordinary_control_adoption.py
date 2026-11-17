"""Actor-free owner-lineage checks for the reservation's control-epoch adapter."""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from .test_ordinary_writer_admission import admission as admission


@pytest.fixture
def control(admission: Any, monkeypatch: Any) -> Any:
    owner, _, _, original, observed, _, _ = admission
    root = Path(__file__).resolve().parents[2] / "plugins/agent-dispatch/src/agent_dispatch"
    name = "promotion_ordinary_writer.ordinary_control_adoption"
    spec = importlib.util.spec_from_file_location(name, root / "ordinary_control_adoption.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    previous = {
        **original, "worktree_writer_requirement": owner.requirement,
        "startup_invocation": {"original": "immutable"}, "last_send": {"outcome": "unknown"},
    }
    digest = hashlib.sha256(json.dumps(["project", "creation-key"]).encode()).hexdigest()
    incoming = {
        **original, "session_id": "adopted",
        "worktree_writer_requirement": owner.requirement,
        module.ADOPTION: {
            "version": 1, "predecessor_id": original["session_id"], "session_id": "adopted",
            "history": {
                "status": "unavailable", "predecessor_id": original["session_id"],
                "event_epoch": "adopted", "turn_epoch": "adopted",
            },
            "request": {
                "version": 1, "operation_id": "ordinary-resume:original", "journal_key": digest,
                "native_session_id": "native", "head_revision": 5, "bridge_installation": "/bridge",
            },
            "prior_operations": ["unknown-original", "settled-not-sent"],
        },
        "worktree_writer_observation": {
            **observed, "key_digest": digest, "session_id": "adopted",
            "executing_writer_identity": {
                "installation_root": "/worktrees", "runtime_generation": "b" * 32,
            },
            "automatic_recovery_stopped": False,
        },
    }
    row = {
        "creation_key": "creation-key", "session_handle": f"local-body:{original['session_id']}",
        "worktree_writer_requirement": json.dumps(owner.requirement),
    }
    return module, previous, incoming, row


def test_control_epoch_retains_unknown_and_excluded_history_without_rewriting_birth(control: Any) -> None:
    module, previous, incoming, row = control
    before = copy.deepcopy(previous)
    module.retain_control(previous, incoming, row, resumed=True)
    assert incoming[module.HISTORY]["predecessor"] == previous == before
    assert incoming[module.HISTORY]["adoption"] == incoming[module.ADOPTION]
    refresh = {name: value for name, value in incoming.items() if name not in {module.HISTORY, module.ADOPTION}}
    module.retain_control(incoming, refresh, row, resumed=True)
    assert refresh[module.HISTORY] == incoming[module.HISTORY]
    assert refresh[module.ADOPTION] == incoming[module.ADOPTION]
    assert refresh[module.HISTORY] is not incoming[module.HISTORY]


@pytest.mark.parametrize("change", ["native", "epoch", "nonce", "key", "predecessor", "holder", "version"])
def test_control_epoch_refuses_unqualified_lineage_without_partial_history(control: Any, change: str) -> None:
    module, previous, incoming, row = control
    if change == "native":
        incoming.update(acp_session_id="other", durable_session_id="other")
    elif change == "epoch":
        incoming[module.ADOPTION]["history"]["event_epoch"] = previous["session_id"]
    elif change == "nonce":
        incoming["worktree_writer_observation"]["worktree_writer"] = {
            **incoming["worktree_writer_requirement"], "creation_nonce": "other",
        }
    elif change == "key":
        row["creation_key"] = "other"
    elif change == "predecessor":
        incoming[module.ADOPTION]["predecessor_id"] = "foreign"
    elif change == "holder":
        incoming["worktree_writer_observation"]["automatic_recovery_stopped"] = True
    else:
        incoming[module.ADOPTION]["version"] = True
    before = copy.deepcopy(previous)
    with pytest.raises(RuntimeError):
        module.retain_control(previous, incoming, row, resumed=True)
    assert previous == before and module.HISTORY not in incoming
