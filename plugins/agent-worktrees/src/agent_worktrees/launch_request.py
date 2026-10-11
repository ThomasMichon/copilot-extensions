"""Target-owned admission for a structured cold-launch request."""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import re
import sys
from datetime import datetime
from pathlib import Path

from . import config as cfg
from . import launch_seed_state, tracking, tracking_write

MAX_REQUEST_BYTES = 8192


def decode(encoded: str) -> dict:
    if len(encoded) > MAX_REQUEST_BYTES * 2:
        raise ValueError("Launch request exceeds the transport limit")
    raw = base64.b64decode(encoded, validate=True)
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Launch request exceeds the transport limit")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate launch-request field")
            result[key] = value
        return result
    request = json.loads(raw, object_pairs_hook=unique_object)
    if (
        not isinstance(request, dict) or type(request.get("version")) is not int or request["version"] != 1
        or set(request) != {"version", "request_id", "kind", "worktree_id", "text", "no_mux"}
        or not isinstance(request["request_id"], str)
        or re.fullmatch(r"[0-9a-f]{32}", request["request_id"]) is None
        or request["kind"] not in ("new", "resume")
        or not isinstance(request["text"], str) or not request["text"].strip()
        or "\0" in request["text"] or type(request["no_mux"]) is not bool
    ):
        raise ValueError("Invalid launch request")
    wid = request["worktree_id"]
    if request["kind"] == "new":
        if wid is not None:
            raise ValueError("New launch request cannot name an existing worktree")
    elif not isinstance(wid, str) or not wid or Path(wid).name != wid or "/" in wid or "\\" in wid:
        raise ValueError("Resume launch request requires a worktree identity")
    return request


def _admission_path(directory: Path, request_id: str) -> Path:
    if re.fullmatch(r"[0-9a-f]{32}", request_id) is None:
        raise ValueError("Invalid launch request identity")
    return directory / "launch-requests" / f"{request_id}.json"


def _apply_admit(args: dict) -> dict:
    directory = Path(args["tracking_dir"])
    path = _admission_path(directory, args["request_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with tracking._RecordLock(path.with_suffix(".yaml"), require_sidecar=True):
        try:
            receipt = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            record_path = directory / f"{args['worktree_id']}.yaml"
            prior_revision = (
                tracking.load_record(record_path).pending_seed_revision
                if record_path.exists() else 0
            )
            receipt = {
                "version": 1, "request_id": args["request_id"],
                "fingerprint": args["fingerprint"], "kind": args["kind"],
                "worktree_id": args["worktree_id"], "timestamp": args["timestamp"],
                "suffix": args["request_id"], "seed_id": args["request_id"],
                "prior_revision": prior_revision,
            }
            tracking._atomic_write(path, json.dumps(receipt) + "\n")
        if (
            not isinstance(receipt, dict) or receipt.get("version") != 1
            or receipt.get("request_id") != args["request_id"]
            or receipt.get("fingerprint") != args["fingerprint"]
        ):
            raise ValueError("Launch request identity was reused for different intent")
        return receipt


def execute(config, request: dict) -> dict:
    """Admit once; recover the same worktree after a lost response."""
    directory = cfg.tracking_dir()
    timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    platform = "win" if cfg.detect_platform() == "windows" else cfg.detect_platform()
    request_id = request["request_id"]
    wid = request["worktree_id"] or f"{config.machine}-{platform}-{timestamp}-{request_id}"
    fingerprint = hashlib.sha256(
        json.dumps(request, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    receipt = _dispatch("launch_request_admit", {
        "tracking_dir": str(directory), "request_id": request_id,
        "fingerprint": fingerprint, "kind": request["kind"],
        "worktree_id": wid, "timestamp": timestamp,
    })
    wid = receipt["worktree_id"]
    path = directory / f"{wid}.yaml"
    execution_lock = _admission_path(directory, request_id).with_suffix(".execution.yaml")
    with tracking._RecordLock(execution_lock, require_sidecar=True):
        receipt = json.loads(_admission_path(directory, request_id).read_text(encoding="utf-8"))
        if not path.exists():
            if receipt.get("accepted") or receipt.get("creation_started"):
                raise ValueError(
                    "Admitted creation was started but its record is unavailable; "
                    "inspect the fixed allocation instead of recreating it"
                )
            if request["kind"] != "new":
                raise ValueError("Requested Resume worktree does not exist")
            work_dir = Path(config.default_repo.worktree_root) / wid
            if work_dir.exists():
                raise RuntimeError(
                    f"Creation {request_id} is incomplete at {wid}; inspect that existing "
                    "worktree instead of issuing another New request"
                )
            from .worktree_creation import _create_worktree_core

            _dispatch("launch_request_creation_start", {
                "tracking_dir": str(directory), "request_id": request_id,
                "fingerprint": fingerprint,
            })
            _create_worktree_core(
                config, no_mux=request["no_mux"], inherit_parent_session=False,
                allocation=(receipt["timestamp"], receipt["suffix"]),
                pending_seed=request["text"], pending_seed_id=request_id,
            )
        _dispatch("launch_request_stage", {
            "tracking_dir": str(directory), "request_id": request_id,
            "fingerprint": fingerprint, "text": request["text"],
        })
        return {
            "version": 1, "request_id": request_id, "worktree_id": wid,
            "seed_id": request_id, "seed_kind": request["kind"],
        }


def _apply_stage(args: dict) -> dict:
    directory = Path(args["tracking_dir"])
    admission = _admission_path(directory, args["request_id"])
    with tracking._RecordLock(admission.with_suffix(".yaml"), require_sidecar=True):
        receipt = json.loads(admission.read_text(encoding="utf-8"))
        if receipt["fingerprint"] != args["fingerprint"]:
            raise ValueError("Launch request identity mismatch")
        path = directory / f"{receipt['worktree_id']}.yaml"
        with tracking._RecordLock(path, require_sidecar=True):
            seed = launch_seed_state.peek(path)
            if receipt.get("accepted"):
                if seed is None or seed.seed_id != receipt["seed_id"]:
                    raise ValueError("Admitted launch intent is completed or superseded")
            elif seed is None or seed.seed_id != receipt["seed_id"]:
                revision = tracking.load_record(path).pending_seed_revision
                try:
                    durable = json.loads(launch_seed_state.state_path(path).read_text(encoding="utf-8"))
                except FileNotFoundError:
                    durable = None
                if durable is not None:
                    if (
                        not isinstance(durable, dict) or durable.get("version") != 1
                        or type(durable.get("revision")) is not int
                    ):
                        raise ValueError("Invalid durable launch-seed revision fence")
                    revision = max(revision, durable["revision"])
                if revision != receipt["prior_revision"]:
                    raise ValueError("Launch intent changed after admission; refusing replacement")
                launch_seed_state._apply_stage({
                    "yaml_path": str(path), "worktree_id": path.stem,
                    "seed_id": receipt["seed_id"], "kind": receipt["kind"],
                    "text": args["text"],
                })
            receipt["accepted"] = True
            tracking._atomic_write(admission, json.dumps(receipt) + "\n")
        return {"accepted": True}


def _apply_creation_start(args: dict) -> dict:
    path = _admission_path(Path(args["tracking_dir"]), args["request_id"])
    with tracking._RecordLock(path.with_suffix(".yaml"), require_sidecar=True):
        receipt = json.loads(path.read_text(encoding="utf-8"))
        if receipt["fingerprint"] != args["fingerprint"]:
            raise ValueError("Launch request identity mismatch")
        receipt["creation_started"] = True
        tracking._atomic_write(path, json.dumps(receipt) + "\n")
    return {"started": True}


def cmd_request(args) -> int:
    """The request stays local on the target; never recursively SSH it."""
    from . import output
    request_id = None
    try:
        forbidden = ("machine", "seed", "seed_id", "dry_run", "new_worktree",
                     "worktree_id", "codename", "base", "bare_resume", "restore", "auto")
        if not args.json or any(getattr(args, key, None) for key in forbidden):
            raise ValueError("Structured launch requests require local --json execution")
        if args.launch_request_status:
            if args.launch_request_b64:
                raise ValueError("Specify admission or status, not both")
            print(json.dumps(inspect(args.launch_request_status)))
            return 0
        request = decode(args.launch_request_b64)
        request_id = request["request_id"]
        config = cfg.load_config()
        # Creation helpers have human-readable output; stdout is this protocol only.
        with contextlib.redirect_stdout(sys.stderr):
            result = execute(config, request)
        print(json.dumps(result))
        return 0
    except (ValueError, OSError, RuntimeError, TimeoutError, tracking_write.AmbiguousWriteOutcome) as exc:
        return output._json_error(
            f"Launch request {request_id or '<invalid>'} failed: {exc}. "
            "Do not issue another New intent after an uncertain admission; "
            "inspect or retry this exact request identity.", exit_code=3,
        )


def inspect(request_id: str) -> dict:
    """Read-only recovery: no prompt or new creation is submitted."""
    directory = cfg.tracking_dir()
    admission = _admission_path(directory, request_id)
    receipt = json.loads(admission.read_text(encoding="utf-8"))
    path = directory / f"{receipt['worktree_id']}.yaml"
    seed = launch_seed_state.peek(path)
    available = seed is not None and seed.seed_id == receipt["seed_id"]
    return {
        "version": 1, "request_id": request_id, "worktree_id": receipt["worktree_id"],
        "seed_id": receipt["seed_id"], "seed_kind": receipt["kind"],
        "ready": available and not seed.handoff_id,
        "state": (
            "handoff-unconfirmed" if available and seed.handoff_id else
            "staged" if available else
            "completed-or-superseded" if receipt.get("accepted") else "creation-incomplete"
        ),
    }


def _dispatch(verb: str, args: dict) -> dict:
    result = launch_seed_state._dispatch(verb, args)
    if result.get("error"):
        raise ValueError(result["error"])
    return result


def _guard(handler):
    def apply(args):
        try:
            return handler(args)
        except (ValueError, OSError, TimeoutError) as exc:
            # Domain refusals must reach the client, not close the daemon socket.
            return {"error": str(exc)}
    return apply


tracking_write.register_verb("launch_request_admit", _guard(_apply_admit))
tracking_write.register_verb("launch_request_stage", _guard(_apply_stage))
tracking_write.register_verb("launch_request_creation_start", _guard(_apply_creation_start))
