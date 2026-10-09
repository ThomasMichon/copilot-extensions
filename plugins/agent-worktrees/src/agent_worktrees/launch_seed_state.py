"""One daemon-mediated, typed launch seed in a worktree's external state."""

from __future__ import annotations

import dataclasses
import json
import uuid
from pathlib import Path
from typing import Literal

from . import locks, status_monitor_runtime, tracking, tracking_write
from .worktree_status_daemon import _is_safe_identity_token


@dataclasses.dataclass(frozen=True)
class LaunchSeed:
    seed_id: str
    kind: Literal["new", "resume"]
    text: str
    revision: int
    handoff_id: str | None = None


def state_path(yaml_path: Path) -> Path:
    if not _is_safe_identity_token(yaml_path.stem) or yaml_path.suffix != ".yaml":
        raise ValueError("Invalid launch-seed worktree identity")
    return yaml_path.parent / yaml_path.stem / "launch-seed.json"


def _read(yaml_path: Path) -> LaunchSeed | None:
    try:
        data = json.loads(state_path(yaml_path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    if not isinstance(data, dict) or data.get("version") != 1:
        raise ValueError("Invalid launch-seed state schema")
    if data.get("finished") is True:
        return None
    seed_id, kind, text, revision = (
        data.get("seed_id"), data.get("kind"), data.get("text"), data.get("revision"),
    )
    if (
        not isinstance(seed_id, str) or not seed_id
        or kind not in ("new", "resume")
        or not isinstance(text, str) or not text
        or not isinstance(revision, int) or isinstance(revision, bool) or revision < 0
    ):
        raise ValueError("Invalid launch-seed state")
    handoff_id = data.get("handoff_id")
    if handoff_id is not None and (not isinstance(handoff_id, str) or not handoff_id):
        raise ValueError("Invalid launch-seed handoff identity")
    return LaunchSeed(seed_id, "new" if kind == "new" else "resume", text, revision, handoff_id)


def peek(yaml_path: Path) -> LaunchSeed | None:
    if not state_path(yaml_path).exists():
        return None
    with tracking._RecordLock(yaml_path, require_sidecar=True):
        current = _read(yaml_path)
        if current is None:
            return None
        try:
            record = tracking.load_record(yaml_path)
        except FileNotFoundError:
            return None
        if (
            not getattr(record, "pending_seed", None)
            and record.pending_seed_revision > current.revision
        ):
            return None
        return current


def _write(yaml_path: Path, seed: LaunchSeed) -> None:
    path = state_path(yaml_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tracking._atomic_write(
        path, json.dumps({"version": 1, **dataclasses.asdict(seed)}, ensure_ascii=False) + "\n",
    )


def _record(args: dict):
    path = Path(args["yaml_path"])
    state_path(path)
    record = tracking.load_record(path)
    if record.worktree_id != path.stem or args.get("worktree_id") != record.worktree_id:
        raise ValueError("Launch-seed record identity mismatch")
    return record


def _apply_stage(args: dict) -> dict:
    path = Path(args["yaml_path"])
    with tracking._RecordLock(path, require_sidecar=True):
        record = _record(args)
        current = peek(path)
        text = args.get("text")
        kind = args.get("kind")
        if text is None:
            legacy = getattr(record, "pending_seed", None)
            if current is not None and not (
                legacy and record.pending_seed_revision > current.revision
            ):
                if legacy or record.pending_seed_revision < current.revision:
                    record.pending_seed = None
                    record.pending_seed_revision = max(
                        record.pending_seed_revision, current.revision,
                    )
                    tracking.save_record(record, path)
                return {"seed": dataclasses.asdict(current)}
            if not legacy:
                return {"seed": None}
            text, kind = legacy, "new"
        if not isinstance(text, str) or not text or kind not in ("new", "resume"):
            raise ValueError("Launch seed requires nonempty text and New/Resume provenance")
        if current is not None and current.seed_id == args["seed_id"]:
            if current.text != text or current.kind != kind:
                raise ValueError("Launch-seed identity was reused for a different intent")
            return {"seed": dataclasses.asdict(current)}
        if not isinstance(args["seed_id"], str) or not args["seed_id"]:
            raise ValueError("Invalid launch-seed identity")
        revision = max(record.pending_seed_revision, current.revision if current else 0) + 1
        seed = LaunchSeed(
            args["seed_id"], "new" if kind == "new" else "resume", text, revision,
        )
        _write(path, seed)
        record.pending_seed = None
        record.pending_seed_revision = revision
        tracking.save_record(record, path)
        return {"seed": dataclasses.asdict(seed)}


def _apply_take(args: dict) -> dict:
    path = Path(args["yaml_path"])
    with tracking._RecordLock(path, require_sidecar=True):
        _record(args)
        current = peek(path)
        if current is None:
            return {"seed": None, "reason": "missing"}
        if args.get("seed_id") and current.seed_id != args["seed_id"]:
            return {"seed": None, "reason": "superseded"}
        if args.get("kind") and current.kind != args["kind"]:
            return {"seed": None, "reason": "different-launch-kind"}
        handoff_id = args["handoff_id"]
        if not isinstance(handoff_id, str) or not handoff_id:
            raise ValueError("Invalid launch-seed handoff identity")
        if current.handoff_id and current.handoff_id != handoff_id:
            return {"seed": None, "reason": "handoff-in-progress-or-unconfirmed"}
        claimed = dataclasses.replace(current, handoff_id=handoff_id)
        _write(path, claimed)
        return {"seed": dataclasses.asdict(claimed)}


def _apply_restore(args: dict) -> dict:
    path = Path(args["yaml_path"])
    with tracking._RecordLock(path, require_sidecar=True):
        _record(args)
        current = peek(path)
        original = LaunchSeed(**args["seed"])
        if not original.handoff_id:
            raise ValueError("A handoff receipt is required for restoration")
        if (
            current is None or current.seed_id != original.seed_id
            or current.handoff_id != original.handoff_id
        ):
            return {"restored": False, "reason": "superseded"}
        _write(path, dataclasses.replace(current, handoff_id=None))
        return {"restored": True}


def _apply_finish(args: dict) -> dict:
    path = Path(args["yaml_path"])
    with tracking._RecordLock(path, require_sidecar=True):
        record = _record(args)
        current = peek(path)
        original = LaunchSeed(**args["seed"])
        if not original.handoff_id:
            raise ValueError("A handoff receipt is required for completion")
        if current is None:
            return {"finished": True}
        if current.seed_id != original.seed_id or current.handoff_id != original.handoff_id:
            return {"finished": False, "reason": "superseded"}
        record.pending_seed_revision = max(record.pending_seed_revision, current.revision) + 1
        tracking._atomic_write(state_path(path), json.dumps({
            "version": 1, "finished": True, "seed_id": current.seed_id,
            "revision": record.pending_seed_revision,
        }) + "\n")
        tracking.save_record(record, path)
        state_path(path).unlink()
        return {"finished": True}


def _apply_remove(args: dict) -> dict:
    path = Path(args["yaml_path"])
    target = state_path(path)
    if args.get("worktree_id") != path.stem:
        raise ValueError("Launch-seed removal identity mismatch")
    with tracking._RecordLock(path, require_sidecar=True):
        target.unlink(missing_ok=True)
    return {"removed": True}


def _dispatch(verb: str, args: dict) -> dict:
    enabled = status_monitor_runtime._status_monitor_enabled()
    return tracking_write.dispatch(
        verb, args,
        read_lock_data=lambda: locks.read_lock(status_monitor_runtime._monitor_lock_path()),
        ensure_monitor=status_monitor_runtime._ensure_status_monitor if enabled else None,
        boot_wait_s=tracking_write.BOOT_WAIT_S if enabled else 0.0,
    )


def stage(
    yaml_path: Path, *, kind: Literal["new", "resume"] | None = None, text: str | None = None,
    seed_id: str | None = None,
) -> LaunchSeed | None:
    result = _dispatch("launch_seed_stage", {
        "yaml_path": str(yaml_path), "worktree_id": yaml_path.stem,
        "seed_id": seed_id or uuid.uuid4().hex, "kind": kind, "text": text,
    })
    return LaunchSeed(**result["seed"]) if result["seed"] is not None else None


def take(yaml_path: Path, *, seed_id: str | None = None, kind: str | None = None) -> dict:
    return _dispatch("launch_seed_take", {
        "yaml_path": str(yaml_path), "worktree_id": yaml_path.stem,
        "seed_id": seed_id, "kind": kind,
        "handoff_id": uuid.uuid4().hex,
    })


def restore(yaml_path: Path, receipt: dict) -> dict:
    return _dispatch("launch_seed_restore", {
        "yaml_path": str(yaml_path), "worktree_id": yaml_path.stem,
        "seed": receipt["seed"],
    })


def finish(yaml_path: Path, receipt: dict) -> dict:
    return _dispatch("launch_seed_finish", {
        "yaml_path": str(yaml_path), "worktree_id": yaml_path.stem,
        "seed": receipt["seed"],
    })


def remove(yaml_path: Path) -> dict:
    return _dispatch("launch_seed_remove", {
        "yaml_path": str(yaml_path), "worktree_id": yaml_path.stem,
    })


def pending(yaml_path: Path, record=None) -> LaunchSeed | None:
    current = peek(yaml_path)
    if record is None:
        record = tracking.load_record(yaml_path)
    if getattr(record, "pending_seed", None):
        return stage(yaml_path)
    return current


def claim_creation(yaml_path: Path) -> dict:
    current = pending(yaml_path)
    if current is None or current.kind != "new":
        return {"seed": None}
    return take(yaml_path, seed_id=current.seed_id, kind="new")


def creation_text(yaml_path: Path, record) -> str | None:
    current = peek(yaml_path)
    legacy = getattr(record, "pending_seed", None)
    if current is None or (legacy and record.pending_seed_revision > current.revision):
        return legacy
    return current.text if current.kind == "new" else None


def settle_creation(yaml_path: Path, receipt: dict, result: dict) -> dict:
    from .pending_seed import nothing_typed

    if not receipt.get("seed"):
        return {}
    if result.get("ok"):
        finish(yaml_path, receipt)
        return {}
    report = {"seed_reason": result.get("reason") or "not-attempted"}
    if not nothing_typed(result):
        finish(yaml_path, receipt)
        return {**report, "seed_unconfirmed": True}
    restored = restore(yaml_path, receipt)
    return {
        **report,
        **({"seed_deferred": True} if restored["restored"] else {"seed_superseded": True}),
    }


tracking_write.register_verb("launch_seed_stage", _apply_stage)
tracking_write.register_verb("launch_seed_take", _apply_take)
tracking_write.register_verb("launch_seed_restore", _apply_restore)
tracking_write.register_verb("launch_seed_finish", _apply_finish)
tracking_write.register_verb("launch_seed_remove", _apply_remove)
