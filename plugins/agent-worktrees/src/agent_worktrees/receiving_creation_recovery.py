"""Resume only the journaled receiving child; never allocate a replacement."""

from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path

from . import config as cfg, creation_allocation, creation_replay, creation_requests
from . import execution_spaces, resource_producer as producer, tracking
from .receiving_allocation import ReceivingAllocation, parsed_receipt


def pairing_facts(config: cfg.Config) -> dict:
    from . import state_root

    resolved = state_root.resolve_state_root(config, cwd=config.default_repo.anchor)
    return {
        "repo": resolved.repo, "path": str(resolved.path) if resolved.path is not None else None,
        "source": resolved.source, "requires_external": resolved.requires_external,
        "bound": resolved.bound,
    }


def original_no_pair(config: cfg.Config, key: str, requested: bool) -> bool:
    """Replay the original effective option, not a later process environment."""
    row = creation_requests.inspect(config, key)
    if row is None:
        return requested or bool(os.environ.get("AGENT_WORKTREES_NO_PAIR"))
    value = row["facts"].get("no_pair")
    if type(value) is not bool:
        raise ValueError("receiving allocation has no valid original pairing option")
    return value


def requires_pair(record: tracking.WorktreeRecord, config: cfg.Config) -> bool:
    """Keep the original companion requirement despite later lookup/opt-out drift."""
    admission = parsed_receipt(record)
    row = creation_requests.inspect(config, admission.key)
    if (
        row is None or row["status"] != "pending"
        or producer.canonical_json(row["facts"].get("receiving_allocation"))
        != producer.canonical_json(admission.to_fields())
        or not isinstance(row["worktree"], dict)
        or row["worktree"].get("creation_nonce") != record.creation_nonce
    ):
        raise ValueError("receiving pairing has no original pending operation")
    recovery = row["worktree"].get("receiving_recovery")
    if not isinstance(recovery, dict) or "pairing" not in recovery:
        raise ValueError("receiving pairing has no original configuration")
    pairing = recovery["pairing"]
    if row["facts"].get("no_pair") is True or row["facts"].get("kind") != "session":
        return False
    if not isinstance(pairing, dict) or set(pairing) != {
        "repo", "path", "source", "requires_external", "bound",
    }:
        raise ValueError("invalid original receiving pairing configuration")
    return bool(pairing["requires_external"] and pairing["bound"] and pairing["path"])


def _update_pending(config: cfg.Config, admission: ReceivingAllocation, row: dict, result: dict, *, complete: bool) -> None:
    path = cfg.tracking_dir(config.repo_name).parent / "workspace-creation.sqlite3"
    connection = creation_requests._connection(path)
    try:
        with connection:
            connection.execute("BEGIN IMMEDIATE")
            digest = hashlib.sha256(admission.key.encode()).hexdigest()
            current = creation_requests.inspect(config, admission.key)
            if current != row or row["status"] != "pending":
                raise ValueError("receiving operation changed during original-child recovery")
            connection.execute(
                "UPDATE creations SET status=?, worktree=? WHERE key_digest=?",
                ("complete" if complete else "pending", producer.canonical_json(result), digest),
            )
    finally:
        connection.close()


def _receipt(record: tracking.WorktreeRecord, config: cfg.Config) -> dict:
    from .worktree_creation import _core

    return {
        "worktree": _core()._worktree_to_dict(record),
        "creation_nonce": record.creation_nonce,
        "record_identity": creation_allocation.record_evidence(record),
        "execution_origin": creation_replay.execution_origin(config),
        "receiving_phase": record.receiving_allocation["phase"],
    }


def note_ready(record: tracking.WorktreeRecord, config: cfg.Config) -> None:
    """Checkpoint the completed tail before the ordinary journal's final commit."""
    admission = parsed_receipt(record)
    row = creation_requests.inspect(config, admission.key)
    if row is None or row["status"] != "pending" or not isinstance(row["worktree"], dict):
        raise ValueError("receiving completion has no original pending operation")
    saved = row["worktree"]
    if saved.get("creation_nonce") != record.creation_nonce:
        raise ValueError("receiving completion changed its original nonce")
    _update_pending(
        config, admission, row, {**saved, "receiving_ready": _receipt(record, config)},
        complete=False,
    )


def _resume_composition(record: tracking.WorktreeRecord, config: cfg.Config, recovery: dict) -> None:
    from . import knowledge_plugins, paired_claims

    pairing = recovery["pairing"]
    if (
        not isinstance(pairing, dict) or record.pair_role != "harness"
        or record.pair_id != f"{recovery['timestamp']}-{recovery['suffix']}"
        or record.pair_kind not in {"worktree", "anchor"}
    ):
        raise ValueError("receiving companion is not the original creation pair")
    if record.pair_kind == "worktree":
        sibling = paired_claims.reciprocal_sibling(record)
        if (
            sibling is None or sibling.worktree_id != record.worktree_id + "-k"
            or sibling.owner_ref != record.receiving_allocation["identity"]["ref"]
            or not sibling.creation_nonce
            or sibling.status in {"finalizing", "finalized", "archived"}
        ):
            raise ValueError("receiving companion has no active original reciprocal record")
    pair = knowledge_plugins.resolve_pair(cwd=record.worktree_path, config=config)
    if (
        pair.pair_id != record.pair_id or pair.pair_kind != record.pair_kind
        or pair.harness_path.resolve() != Path(record.worktree_path).resolve()
        or pair.knowledge_repo != pairing["repo"]
    ):
        raise ValueError("receiving companion differs from its original binding")
    result = knowledge_plugins.compose_from_pair(cwd=record.worktree_path, config=config)
    if result.get("pair_error") or result.get("action") == "error":
        raise ValueError("receiving companion composition remains incomplete")


def recover(record: tracking.WorktreeRecord, config: cfg.Config, row: dict) -> bool:
    """Called under the original operation lock, after exact local record admission."""
    if row["status"] != "pending" or record.status in {"finalizing", "finalized", "archived"}:
        return False
    admission = parsed_receipt(record)
    saved = row["worktree"]
    if not isinstance(saved, dict) or not Path(record.worktree_path).is_dir():
        return False
    recovery = saved.get("receiving_recovery")
    if not isinstance(recovery, dict) or set(recovery) != {
        "timestamp", "suffix", "execution_origin", "pairing",
    }:
        return False
    if (
        not isinstance(recovery["timestamp"], str)
        or re.fullmatch(r"\d{8}-\d{6}", recovery["timestamp"]) is None
        or not isinstance(recovery["suffix"], str)
        or re.fullmatch(r"[0-9a-f]{4}", recovery["suffix"]) is None
    ):
        raise ValueError("invalid original receiving creation coordinates")
    facts = row["facts"]
    if (
        facts.get("anchor") != creation_requests._canonical_path(config.default_repo.anchor)
        or facts.get("worktree_root") != creation_requests._canonical_path(config.default_repo.worktree_root)
        or facts.get("remote") != config.default_repo.remote
        or facts.get("default_branch") != config.default_repo.default_branch
        or (
            not facts.get("no_pair") and facts.get("kind") == "session"
            and recovery["pairing"] != pairing_facts(config)
        )
    ):
        raise ValueError("original receiving creation configuration changed")
    creation_replay.require_completed_record(
        record, config, {"execution_origin": recovery["execution_origin"]},
    )
    ready = saved.get("receiving_ready")
    if ready is not None:
        if not isinstance(ready, dict) or set(ready) != {
            "worktree", "creation_nonce", "record_identity", "execution_origin", "receiving_phase",
        }:
            raise ValueError("invalid receiving completion checkpoint")
        creation_allocation.require_record_evidence(record, ready.get("record_identity"))
        if (
            ready["creation_nonce"] != record.creation_nonce
            or ready["execution_origin"] != recovery["execution_origin"]
            or ready["receiving_phase"] not in {"created", "linked"}
            or not isinstance(ready["worktree"], dict)
            or any(ready["worktree"].get(key) != value for key, value in {
                "id": record.worktree_id, "repo": record.repo,
                "path": record.worktree_path, "branch": record.branch,
            }.items())
        ):
            raise ValueError("receiving completion checkpoint has a different child identity")
    else:
        from . import worktree_creation

        # The companion ID derives from this original child, never a new random
        # allocation. A partially carved companion fails closed in ordinary Git.
        if record.pair_id or record.pair_ref:
            _resume_composition(record, config, recovery)
        else:
            worktree_creation._complete_created_pair(
                config, record, timestamp=recovery["timestamp"], suffix=recovery["suffix"],
                no_pair=facts.get("no_pair") is True,
            )
        note_ready(record, config)
        row = creation_requests.inspect(config, admission.key)
    with tracking._RecordLock(record.yaml_path, require_sidecar=True):
        fresh = tracking.load_record(record.yaml_path)
        execution_spaces.require_record_mutation(fresh, config)
        creation_allocation.require_record_evidence(fresh, creation_allocation.record_evidence(record))
        if fresh.creation_nonce != record.creation_nonce:
            raise ValueError("receiving child changed during original-operation recovery")
        if fresh.status in {"finalizing", "finalized", "archived"}:
            return False
        _update_pending(config, admission, row, _receipt(fresh, config), complete=True)
    return True
