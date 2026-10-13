"""Explicit, record-scoped recovery of legacy unquoted agent text."""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path

import yaml

from . import config as cfg, output, pr_authority, tracking

_AGENT_TAIL_FIELDS = (
    "pending_seed", "pending_seed_revision", "pair_id", "pair_role", "pair_ref",
    "pair_kind", "reaped_at", "codename", "codename_source", "resources",
    "last_finalize_released", "follow_ups", "prs", "pr", "sessions",
)
_SERIALIZED_FIELDS = frozenset("""
active_effort activity activity_at bound_agent bound_live bound_live_at branch
caller_worktree checkout_managed codename codename_source completed_at
controller_revision controllers creation_nonce dispatch_attempt effort_revision
execution_leg finalized_checkout follow_up follow_ups git_state handoff_counter
handoffs head_revision head_session head_transitions interface kind
last_finalize_released last_finalized_at last_resumed_at lifecycle_revision
machine mux_live mux_live_at origin owner owner_ref pair_id pair_kind pair_ref
pair_role parent_session paused paused_revision pending_seed pending_seed_revision
platform pr profile_assignment_revision profile_assignments prs reaped_at repo
resources resume_count session_backend session_state_at session_summary session_turns
sessions started_at status status_note_at summary title title_asserted worktree_id
worktree_path
""".split())


def add_doctor_arguments(parser) -> None:
    parser.add_argument(
        "--repair-record", metavar="WORKTREE_ID",
        help="Inspect only this tracking record's YAML; --fix repairs only it.",
    )
    parser.add_argument(
        "--bound-agent-end-field", choices=_AGENT_TAIL_FIELDS,
        help="Explicit first field after legacy unquoted bound_agent text; "
        "requires --repair-record and refuses duplicate boundaries.",
    )


def strict_mapping(raw: str) -> dict:
    data = yaml.load(raw, Loader=pr_authority._StrictLoader)
    if not isinstance(data, dict):
        raise ValueError("tracking record must be a YAML mapping")
    return data


def repair_bound_agent(raw: str, end_field: str) -> str | None:
    """Quote only an explicitly delimited value, preserving surrounding bytes."""
    if end_field not in _AGENT_TAIL_FIELDS:
        return None
    headers = list(re.finditer(r"(?m)^bound_agent: ([^\r\n]*)(?:\r?\n|$)", raw))
    boundaries = list(re.finditer(rf"(?m)^{re.escape(end_field)}:", raw))
    if len(headers) != 1 or len(boundaries) != 1:
        return None
    header, boundary = headers[0], boundaries[0]
    if (
        not header.group(1) or header.group(1)[0] in "'\"|>"
        or boundary.start() < header.end()
    ):
        return None
    tail_headers = "|".join(re.escape(field) for field in _AGENT_TAIL_FIELDS)
    if re.search(rf"(?m)^(?:{tail_headers}):", raw[header.end():boundary.start()]):
        return None
    prefix, suffix = raw[:header.start()], raw[boundary.start():]
    try:
        before, after = strict_mapping(prefix), strict_mapping(suffix)
        if before.keys() & after.keys() or any(key not in _AGENT_TAIL_FIELDS for key in after):
            return None
        if not isinstance(before.get("worktree_id"), str) or not isinstance(before.get("branch"), str):
            return None
        value = raw[header.start() + len("bound_agent: "):boundary.start()]
        if value.endswith("\r\n"):
            value = value[:-2]
        elif value.endswith("\n"):
            value = value[:-1]
        fixed = prefix + "bound_agent: " + json.dumps(value, ensure_ascii=False) + "\n" + suffix
        parsed = strict_mapping(fixed)
        if parsed != {**before, "bound_agent": value, **after}:
            return None
        return fixed
    except (ValueError, TypeError, yaml.YAMLError):
        return None


def apply_repair(path: Path, observed: bytes, fixed: str) -> Path:
    """Exclude association and record writers; retain the exact pre-repair bytes."""
    strict_mapping(fixed)
    with pr_authority.guard(timeout=0), tracking._RecordLock(
        path, blocking=False,
    ) as lock:
        if not lock.acquired:
            raise TimeoutError("tracking record is busy; repair refused")
        if path.is_symlink() or path.read_bytes() != observed:
            raise ValueError("tracking record changed; inspect again before repair")
        digest = hashlib.sha256(observed).hexdigest()[:16]
        backup = path.with_name(f"{path.name}.repair-{digest}.bak")
        try:
            descriptor = os.open(backup, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(observed)
                handle.flush()
                os.fsync(handle.fileno())
        except FileExistsError:
            if backup.is_symlink() or backup.read_bytes() != observed:
                raise ValueError("repair backup cannot be verified") from None
            if os.name != "nt" and backup.stat().st_mode & 0o077:
                raise ValueError("existing repair backup permissions are not private") from None
        tracking._atomic_write(path, fixed)
        return backup


def targeted_doctor(args) -> int:
    """A targeted integrity check never runs lifecycle, session, or GC passes."""
    from . import health

    record_id = args.repair_record
    apply = getattr(args, "fix", False)
    end_field = getattr(args, "bound_agent_end_field", None)
    json_mode = getattr(args, "json", False)
    report = {
        "version": 1, "scope": "record", "worktree_id": record_id,
        "mode": "fix" if apply else "report", "repairable": False,
        "repaired": False, "error": None,
    }
    try:
        if (
            not isinstance(record_id, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", record_id)
            or ".." in record_id
        ):
            raise ValueError("repair requires an exact worktree ID, not a path")
        if any(getattr(args, field, False) for field in (
            "gc_sessions", "prune_pivots", "apply_daemon_health",
        )):
            raise ValueError("record repair cannot be combined with other repair passes")
        project = cfg.project_name()
        if not project:
            raise ValueError("record repair requires an explicitly selected project")
        report["project"] = project
        path = cfg.tracking_dir() / f"{record_id}.yaml"
        if path.is_symlink():
            raise ValueError("tracking record symlink refused")
        observed = path.read_bytes()
        raw = observed.decode("utf-8")
        try:
            parsed = strict_mapping(raw)
        except (ValueError, TypeError, yaml.YAMLError):
            parsed = None
        fixed = (
            repair_bound_agent(raw, end_field) if end_field else
            health.repair_yaml_text(raw) if parsed is None else None
        )
        if fixed is not None:
            parsed = strict_mapping(fixed)
            if parsed.keys() - _SERIALIZED_FIELDS:
                raise ValueError("record contains unknown fields outside the selected agent text")
            if parsed.get("worktree_id") != record_id:
                raise ValueError("record identity does not match the selected worktree")
            report["repairable"] = True
            if apply:
                report["backup"] = apply_repair(path, observed, fixed).name
                report["repaired"] = True
        else:
            if parsed is None or parsed.keys() - _SERIALIZED_FIELDS:
                raise ValueError("record has no verified repair; supply an exact agent-text boundary")
            if parsed.get("worktree_id") != record_id:
                raise ValueError("record identity does not match the selected worktree")
    except (OSError, ValueError, TypeError, RuntimeError, yaml.YAMLError) as exc:
        report["error"] = str(exc)
    if json_mode:
        print(json.dumps(report))
    elif report["error"]:
        output.err(report["error"])
    else:
        output.ok(f"Record {record_id}: " + (
            "repaired" if report["repaired"] else "repairable" if report["repairable"] else "valid"
        ))
    return 1 if report["error"] else 0
