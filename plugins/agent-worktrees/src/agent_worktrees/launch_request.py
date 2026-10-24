"""Target-owned admission for a structured cold-launch request."""

from __future__ import annotations

import base64
import binascii
import contextlib
import hashlib
import json
import re
import secrets
import sys
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

from . import config as cfg
from . import creation_allocation, launch_admission, launch_seed_state, tracking, tracking_write
from .creation_identity import IdentityMode, validate_mode
from .execution_spaces import ExecutionSpaceError, project_record_config, require_project_record_mutation
from .worktree_naming import parse_session_worktree_id, session_worktree_id

MAX_REQUEST_BYTES = 7 * 1024


def _protocol_fields(value: dict) -> dict:
    version = value.get("version", 1)
    if type(version) is not int or version not in (1, 2):
        raise ValueError("Invalid launch request protocol")
    if version == 1:
        if "attempt_id" in value or "native_seed_required" in value:
            raise ValueError("Legacy launch requests cannot claim native correlation")
        return {"version": 1}
    attempt_id = value.get("attempt_id")
    if (
        "attempt_id" not in value or value.get("native_seed_required") is not True
        or (attempt_id is not None and (
            not isinstance(attempt_id, str) or re.fullmatch(r"[0-9a-f]{32}", attempt_id) is None
        ))
    ):
        raise ValueError("Invalid native launch request correlation")
    return {"version": 2, "attempt_id": attempt_id, "native_seed_required": True}


def decode(encoded: str) -> dict:
    if len(encoded) > MAX_REQUEST_BYTES * 2:
        raise ValueError("Launch request exceeds the transport limit")
    try:
        raw = base64.b64decode(encoded, validate=True)
    except binascii.Error as exc:
        raise ValueError("Invalid Base64 launch request") from exc
    if len(raw) > MAX_REQUEST_BYTES:
        raise ValueError("Launch request exceeds the transport limit")
    def unique_object(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("Duplicate launch-request field")
            result[key] = value
        return result
    request = json.loads(raw.decode("utf-8"), object_pairs_hook=unique_object)
    if not isinstance(request, dict):
        raise ValueError("Invalid launch request")
    protocol = _protocol_fields(request)
    fields = {"version", "request_id", "kind", "worktree_id", "text", "no_mux"} | set(protocol)
    if (
        set(request) != fields
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


def _validate_admission(receipt: object, request_id: str) -> dict:
    required = {
        "version", "request_id", "fingerprint", "kind", "worktree_id",
        "timestamp", "suffix", "seed_id", "prior_revision",
    }
    if (
        not isinstance(receipt, dict) or not required.issubset(receipt)
        or receipt["request_id"] != request_id or receipt["seed_id"] != request_id
        or not isinstance(receipt["suffix"], str)
        or (
            receipt["suffix"] != request_id
            and re.fullmatch(r"[0-9a-f]{4}", receipt["suffix"]) is None
        )
        or receipt["kind"] not in ("new", "resume")
        or not isinstance(receipt["fingerprint"], str)
        or re.fullmatch(r"[0-9a-f]{64}", receipt["fingerprint"]) is None
        or not isinstance(receipt["timestamp"], str)
        or re.fullmatch(r"\d{8}-\d{6}", receipt["timestamp"]) is None
        or type(receipt["prior_revision"]) is not int or receipt["prior_revision"] < 0
        or any(
            key in receipt and type(receipt[key]) is not bool
            for key in ("accepted", "creation_started", "creation_complete")
        )
    ):
        raise ValueError("Invalid stored launch admission")
    _protocol_fields(receipt)
    wid = receipt["worktree_id"]
    if (
        not isinstance(wid, str) or not wid or "\0" in wid
        or Path(wid).name != wid or "/" in wid or "\\" in wid
    ):
        raise ValueError("Invalid stored launch admission worktree identity")
    if receipt["suffix"] != request_id:
        allocation = parse_session_worktree_id(wid)
        if allocation is None or allocation[1:] != (receipt["timestamp"], receipt["suffix"]):
            raise ValueError("Invalid stored launch admission allocation")
    if "allocation" in receipt:
        allocation = creation_allocation.AdmittedAllocation.from_fields(receipt["allocation"])
        if (
            receipt["kind"] != "new" or allocation.worktree_id != wid
            or allocation.timestamp != receipt["timestamp"] or allocation.suffix != receipt["suffix"]
        ):
            raise ValueError("Invalid stored launch admission complete allocation")
    if (
        ("allocation" in receipt and receipt.get("creation_complete") and (
            "creation_nonce" not in receipt or "record_identity" not in receipt
        ))
        or ("creation_nonce" in receipt and (
            "allocation" not in receipt or not receipt.get("creation_complete")
            or not isinstance(receipt["creation_nonce"], str) or not receipt["creation_nonce"]
        ))
    ):
        raise ValueError("Invalid stored launch admission creation nonce")
    if "record_identity" in receipt:
        if "allocation" not in receipt or not receipt.get("creation_complete"):
            raise ValueError("Invalid stored launch admission record identity")
        creation_allocation.validate_record_evidence(receipt["record_identity"])
    return receipt


def _read_admission(directory: Path, request_id: str) -> dict:
    receipt = json.loads(_admission_path(directory, request_id).read_text(encoding="utf-8"))
    return _validate_admission(receipt, request_id)


def _require_replay_identity(receipt: dict, args: dict) -> None:
    if (
        _protocol_fields(receipt) != _protocol_fields(args)
        or receipt["fingerprint"] != args["fingerprint"]
        or receipt["kind"] != args["kind"]
        or (args["kind"] == "resume" and receipt["worktree_id"] != args["worktree_id"])
    ):
        raise ValueError("Launch request identity was reused for different intent")


def _require_complete_record(receipt: dict, record: tracking.WorktreeRecord) -> None:
    if not receipt.get("creation_complete"):
        raise ValueError("Admitted New creation is incomplete; inspect before launch")
    allocation = creation_allocation.AdmittedAllocation.from_fields(receipt["allocation"])
    allocation.require_current(project_record_config(record))
    allocation.require_record(record, nonce=receipt["creation_nonce"])
    creation_allocation.require_record_evidence(record, receipt["record_identity"])
    if record.status in ("finalized", "archived") or not Path(allocation.path).is_dir():
        raise ValueError("Stored New allocation checkout is unavailable or retired; inspect, do not recreate")


def _apply_admit(args: dict) -> dict:
    from . import agent_policy

    protocol = _protocol_fields(args)
    if args["kind"] == "resume":
        target = tracking.load_record(
            Path(args["tracking_dir"]) / f"{args['worktree_id']}.yaml"
        ).worktree_path
    else:
        project = args.get("project")
        if not isinstance(project, str) or not project.strip():
            raise ValueError("Hosting-aware New admission requires an explicit project identity")
        config = project_record_config(SimpleNamespace(repo=project))
        target = config.default_repo.anchor
    agent_policy.require(target, operation="remote launch admission")
    directory = Path(args["tracking_dir"])
    path = _admission_path(directory, args["request_id"])
    path.parent.mkdir(parents=True, exist_ok=True)
    with tracking._RecordLock(path.with_suffix(".yaml"), require_sidecar=True):
        try:
            receipt = _read_admission(directory, args["request_id"])
        except FileNotFoundError:
            receipt = {
                **protocol, "request_id": args["request_id"],
                "fingerprint": args["fingerprint"], "kind": args["kind"],
                "worktree_id": args["worktree_id"], "timestamp": args["timestamp"],
                "suffix": args.get("suffix", args["request_id"]), "seed_id": args["request_id"],
                "prior_revision": 0,
            }
            if "allocation_request" in args:
                selection = args["allocation_request"]
                if (
                    args["kind"] != "new" or not isinstance(selection, dict)
                    or set(selection) != {"project", "identity_mode"}
                    or not isinstance(selection["project"], str) or not selection["project"]
                ):
                    raise ValueError("Invalid launch allocation request")
                config = project_record_config(SimpleNamespace(repo=selection["project"]))
                allocation = creation_allocation.admit(
                    config, receipt["timestamp"], receipt["suffix"], selection["identity_mode"],
                )
                receipt["allocation"] = allocation.to_fields()
                receipt["worktree_id"] = allocation.worktree_id
            _validate_admission(receipt, args["request_id"])
            record_path = directory / f"{receipt['worktree_id']}.yaml"
            if "allocation" not in receipt and record_path.exists():
                receipt["prior_revision"] = tracking.load_record(record_path).pending_seed_revision
            tracking._atomic_write(path, json.dumps(receipt) + "\n")
        _require_replay_identity(receipt, args)
        return receipt


def execute(config, request: dict, *, identity_mode: IdentityMode = "legacy") -> dict:
    """Admit once; recover the same worktree after a lost response."""
    from . import agent_policy

    target = config.default_repo.anchor
    if request["kind"] == "resume":
        target = tracking.load_record(
            cfg.tracking_dir() / f"{request['worktree_id']}.yaml"
        ).worktree_path
    agent_policy.require(target, operation="remote launch admission")
    directory = cfg.tracking_dir()
    validate_mode(identity_mode)
    detected_platform = cfg.detect_platform()
    request_id = request["request_id"]
    # Reuse a prior New admission's allocation when one already exists (a
    # retry or a concurrent racer), rather than generating a fresh
    # timestamp/suffix that would only be discarded once the receipt is read
    # back below -- keeps admission_args deterministic and auditable across
    # retries. Resume always carries its own worktree_id and a request_id
    # suffix, so its allocation is already deterministic; reusing a stored
    # value there would also mask a genuine reused-identity mismatch that
    # `_require_replay_identity` is meant to catch.
    existing = None
    if request["kind"] == "new":
        with contextlib.suppress(FileNotFoundError):
            existing = _read_admission(directory, request_id)
    if existing is not None:
        timestamp = existing["timestamp"]
        suffix = existing["suffix"]
        wid = existing["worktree_id"]
    else:
        timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        suffix = secrets.token_hex(2) if request["kind"] == "new" else request_id
        wid = request["worktree_id"] or session_worktree_id(
            config.machine, detected_platform, timestamp, suffix,
        )
    fingerprint = hashlib.sha256(
        json.dumps(request, sort_keys=True, ensure_ascii=False).encode("utf-8")
    ).hexdigest()
    admission_args = {
        **_protocol_fields(request),
        "project": config.repo_name,
        "tracking_dir": str(directory), "request_id": request_id,
        "fingerprint": fingerprint, "kind": request["kind"],
        "worktree_id": wid, "timestamp": timestamp, "suffix": suffix,
    }
    if request["kind"] == "new":
        admission_args["allocation_request"] = {
            "project": config.repo_name, "identity_mode": identity_mode,
        }
    receipt = _dispatch("launch_request_admit", admission_args)
    wid = receipt["worktree_id"]
    path = directory / f"{wid}.yaml"
    execution_lock = _admission_path(directory, request_id).with_suffix(".execution.yaml")
    with tracking._RecordLock(execution_lock, require_sidecar=True):
        receipt = _read_admission(directory, request_id)
        _require_replay_identity(receipt, admission_args)
        admitted = (
            creation_allocation.AdmittedAllocation.from_fields(receipt["allocation"])
            if "allocation" in receipt else None
        )
        if admitted is not None:
            admitted.require_current(config)
        if not path.exists():
            if receipt.get("accepted") or receipt.get("creation_started"):
                raise ValueError(
                    "Admitted creation was started but its record is unavailable; "
                    "inspect the fixed allocation instead of recreating it"
                )
            if request["kind"] != "new":
                raise ValueError("Requested Resume worktree does not exist")
            if admitted is None:
                # Old receipts contain only the historical spelling and allocation
                # stub. Preserve that format; a conflict is not a new admission.
                expected_wid = session_worktree_id(
                    config.machine, detected_platform, receipt["timestamp"], receipt["suffix"],
                    legacy=len(receipt["suffix"]) == 32,
                )
                if wid != expected_wid:
                    raise ValueError(
                        "Stored New allocation no longer matches this machine/platform; "
                        "inspect its original worktree identity instead of creating another"
                    )
            work_dir = Path(admitted.path) if admitted else Path(config.default_repo.worktree_root) / wid
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
                **({"admitted_allocation": admitted} if admitted is not None else {
                    "allocation": (receipt["timestamp"], receipt["suffix"]),
                }),
                pending_seed=request["text"], pending_seed_id=request_id,
            )
            _dispatch("launch_request_creation_finish", {
                "tracking_dir": str(directory), "request_id": request_id,
                "fingerprint": fingerprint,
            })
        elif request["kind"] == "new" and not receipt.get("creation_complete"):
            raise ValueError("Admitted New creation is incomplete; inspect before launch")
        if admitted is not None:
            record = tracking.load_record(path)
            completed = _read_admission(directory, request_id)
            _require_complete_record(completed, record)
        _dispatch("launch_request_stage", {
            "tracking_dir": str(directory), "request_id": request_id,
            "fingerprint": fingerprint, "text": request["text"],
        })
        return {
            **_protocol_fields(receipt), "request_id": request_id, "worktree_id": wid,
            "seed_id": request_id, "seed_kind": request["kind"],
        }


def _apply_stage(args: dict) -> dict:
    from . import agent_policy

    directory = Path(args["tracking_dir"])
    admission = _admission_path(directory, args["request_id"])
    initial = _read_admission(directory, args["request_id"])
    agent_policy.require_record(directory / f"{initial['worktree_id']}.yaml")
    with tracking._RecordLock(admission.with_suffix(".yaml"), require_sidecar=True):
        receipt = _read_admission(directory, args["request_id"])
        if receipt["fingerprint"] != args["fingerprint"]:
            raise ValueError("Launch request identity mismatch")
        path = directory / f"{receipt['worktree_id']}.yaml"
        with launch_admission.guard(path, timeout=2.0), tracking._RecordLock(
            path, require_sidecar=True,
        ):
            record = tracking.load_record(path)
            agent_policy.require(record.worktree_path)
            require_project_record_mutation(record)
            if "allocation" in receipt:
                _require_complete_record(receipt, record)
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
        receipt = _read_admission(Path(args["tracking_dir"]), args["request_id"])
        if receipt["fingerprint"] != args["fingerprint"]:
            raise ValueError("Launch request identity mismatch")
        if "allocation" in receipt:
            allocation = creation_allocation.AdmittedAllocation.from_fields(receipt["allocation"])
            allocation.require_current(project_record_config(SimpleNamespace(repo=allocation.project)))
        receipt["creation_started"] = True
        tracking._atomic_write(path, json.dumps(receipt) + "\n")
    return {"started": True}


def _apply_creation_finish(args: dict) -> dict:
    path = _admission_path(Path(args["tracking_dir"]), args["request_id"])
    with tracking._RecordLock(path.with_suffix(".yaml"), require_sidecar=True):
        receipt = _read_admission(Path(args["tracking_dir"]), args["request_id"])
        if receipt["fingerprint"] != args["fingerprint"] or not receipt.get("creation_started"):
            raise ValueError("Launch creation identity mismatch")
        if "allocation" in receipt:
            allocation = creation_allocation.AdmittedAllocation.from_fields(receipt["allocation"])
            record_path = Path(args["tracking_dir"]) / f"{allocation.worktree_id}.yaml"
            with tracking._RecordLock(record_path, require_sidecar=True):
                record = tracking.load_record(record_path)
                if receipt.get("creation_complete"):
                    _require_complete_record(receipt, record)
                    return {"created": True}
                require_project_record_mutation(record)
                allocation.require_current(project_record_config(record))
                allocation.require_record(record)
                if not record.creation_nonce:
                    raise ValueError("Created allocation has no authoritative creation nonce")
                receipt["creation_nonce"] = record.creation_nonce
                receipt["record_identity"] = creation_allocation.record_evidence(record)
        receipt["creation_complete"] = True
        tracking._atomic_write(path, json.dumps(receipt) + "\n")
    return {"created": True}


def cmd_request(args) -> int:
    """The request stays local on the target; never recursively SSH it."""
    from . import output
    request_id = None
    try:
        protocol_options = {
            "command", "json", "launch_request_b64", "launch_request_status",
            "require_agent_hosting", "require_native_seed",
            "require_launch_attempt",
        }
        if not args.json or any(
            value is not None and value is not False and value != []
            for key, value in vars(args).items() if key not in protocol_options
        ):
            raise ValueError("Structured launch requests require local --json execution")
        admission_present = args.launch_request_b64 is not None
        status_present = args.launch_request_status is not None
        if admission_present == status_present:
            raise ValueError("Specify admission or status, not both")
        if status_present:
            if getattr(args, "require_native_seed", False):
                raise ValueError("Native seed capability requires a launch request envelope")
            if getattr(args, "require_launch_attempt", False):
                raise ValueError("Launch attempt capability requires a launch request envelope")
            if not args.launch_request_status:
                raise ValueError("Launch request identity must not be empty")
            request_id = args.launch_request_status
            print(json.dumps(inspect(args.launch_request_status)))
            return 0
        if not args.launch_request_b64:
            raise ValueError("Launch request envelope must not be empty")
        request = decode(args.launch_request_b64)
        request_id = request["request_id"]
        if bool(getattr(args, "require_native_seed", False)) != (request["version"] == 2):
            raise ValueError("Native seed capability and launch request protocol must agree")
        if getattr(args, "require_launch_attempt", False):
            from .launch_attempt import identity

            identity(request.get("attempt_id"))
            if request["version"] != 2:
                raise ValueError("Launch attempt receipts require native request protocol v2")
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
    receipt = _read_admission(directory, request_id)
    path = directory / f"{receipt['worktree_id']}.yaml"
    if "allocation" in receipt and receipt.get("creation_complete"):
        _require_complete_record(receipt, tracking.load_record(path))
    seed = launch_seed_state.peek(path)
    creation_complete = receipt["kind"] == "resume" or bool(receipt.get("creation_complete"))
    available = (
        creation_complete and seed is not None
        and seed.seed_id == receipt["seed_id"] and seed.kind == receipt["kind"]
    )
    return {
        **_protocol_fields(receipt), "request_id": request_id, "worktree_id": receipt["worktree_id"],
        "seed_id": receipt["seed_id"], "seed_kind": receipt["kind"],
        "ready": available and not seed.handoff_id,
        "state": (
            "handoff-unconfirmed" if available and seed.handoff_id else
            "staged" if available else
            "completed-or-superseded" if receipt.get("accepted") else
            "creation-incomplete" if not creation_complete else "admission-incomplete"
        ),
    }


def _dispatch(verb: str, args: dict) -> dict:
    result = launch_seed_state._dispatch(
        verb, args, min_version=8,
    )
    if result.get("error"):
        raise ValueError(result["error"])
    return result


def _guard(handler):
    def apply(args):
        try:
            return handler(args)
        except (ValueError, OSError, TimeoutError, ExecutionSpaceError) as exc:
            # Domain refusals must reach the client, not close the daemon socket.
            return {"error": str(exc)}
    return apply


tracking_write.register_verb("launch_request_admit", _guard(_apply_admit), version=8)
tracking_write.register_verb("launch_request_stage", _guard(_apply_stage), version=8)
tracking_write.register_verb("launch_request_creation_start", _guard(_apply_creation_start), version=8)
tracking_write.register_verb("launch_request_creation_finish", _guard(_apply_creation_finish), version=8)
