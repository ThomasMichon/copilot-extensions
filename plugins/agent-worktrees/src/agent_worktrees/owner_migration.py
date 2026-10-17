"""Explicit, single-record repair of a proven local historical owner label."""

from __future__ import annotations

import hashlib
import json
import re
from contextlib import ExitStack
from pathlib import Path

import yaml

from . import config as cfg, execution_spaces, output, pr_authority, repos, tracking
from . import legacy_wsl_record_locality, tracking_claims, tracking_yaml_repair


class MigrationAuditError(RuntimeError):
    """The atomic record change committed but its final audit did not."""


def _names(entry) -> set[str]:
    return {
        value.casefold() for value in (
            entry.key, entry.hostname, entry.alias, *entry.compatibility_aliases,
            *(environment.alias for environment in entry.ssh_environments),
        ) if value
    }


def _require_identity(config, old: str, new: str) -> None:
    entries = execution_spaces.registry_for_config(config)
    current = entries.get(new)
    actual = cfg.detect_platform()
    if (
        current is None or current.legacy_key or config.machine != new
        or current.key != new or current.execution_platform != actual
        or config.platform != actual
    ):
        raise ValueError("new machine must be the configured canonical execution-space key and actual platform")
    for label in (old, new):
        matches = [entry.key for entry in entries.values() if label.casefold() in _names(entry)]
        foreign_transport = any(
            environment.alias.casefold() == label.casefold() and environment.name != actual
            for entry in entries.values() for environment in entry.ssh_environments
            if environment.alias
        )
        if matches != [new] or foreign_transport:
            raise ValueError(f"machine label {label!r} is foreign, unregistered or ambiguous")
    execution_spaces.require_current_execution_space(config)


def _inventory(project: str) -> dict[Path, tuple[str, bytes, dict]]:
    """Include knowledge-only repositories, which need not be adopted projects."""
    ledgers = pr_authority._ledgers(project)
    registry = repos.read_registry(strict=True)
    for name, entry in registry.repos.items():
        if entry.local_path(cfg.detect_platform()):
            if not cfg._PROJECT_NAME_RE.fullmatch(name) or name in (".", ".."):
                raise ValueError("invalid registered project name")
            if cfg.tracking_dir(name).is_symlink():
                raise ValueError("tracking directory symlink refused")
            directory = cfg.tracking_dir(name).resolve()
            if directory in ledgers and ledgers[directory] != name:
                raise ValueError("registered projects share a tracking namespace")
            ledgers[directory] = name
    result = {}
    for directory, name in sorted(ledgers.items(), key=lambda item: str(item[0])):
        if directory.is_symlink():
            raise ValueError(f"tracking directory symlink refused: {directory}")
        try:
            paths = sorted(path for path in directory.iterdir() if path.suffix == ".yaml")
        except FileNotFoundError:
            continue
        for path in paths:
            if path.is_symlink():
                raise ValueError(f"tracking record symlink refused: {path}")
            raw = path.read_bytes()
            result[path] = (name, raw, tracking_yaml_repair.strict_mapping(raw.decode("utf-8")))
    return result


def _references(value, prefix: str = ""):
    if isinstance(value, dict):
        project = value.get("project", value.get("repo"))
        if prefix and all(isinstance(item, str) and item for item in (
            value.get("machine"), project, value.get("worktree_id"),
        )):
            yield prefix, tracking_claims.ClaimRef(
                machine=value["machine"], project=project, worktree_id=value["worktree_id"],
            )
        for key, item in value.items():
            yield from _references(item, f"{prefix}.{key}" if prefix else str(key))
    elif isinstance(value, list):
        for index, item in enumerate(value):
            yield from _references(item, f"{prefix}[{index}]")
    elif isinstance(value, str):
        decoded = tracking_claims.decanonicalize_ref(value)
        parsed = tracking.parse_claim_ref(decoded)
        if parsed is not None and parsed.is_qualified:
            yield prefix, parsed


def _embedded_old_label(value, old: str, prefix: str = "") -> str | None:
    if isinstance(value, dict):
        for key, item in value.items():
            location = f"{prefix}.{key}" if prefix else str(key)
            if (
                location != "machine" and isinstance(item, str)
                and item.casefold() == old.casefold()
                and (key == "machine" or str(key).endswith("_machine") or key == "owner")
            ):
                return location
            found = _embedded_old_label(item, old, location)
            if found:
                return found
    elif isinstance(value, list):
        for index, item in enumerate(value):
            found = _embedded_old_label(item, old, f"{prefix}[{index}]")
            if found:
                return found
    return None


def _require_unlinked(data: dict, inventory, project: str, record_id: str, old: str, new: str) -> None:
    # These relationships have other durable owners, including provider/session
    # projections outside the scanned local tracking ledgers. Do not rewrite one
    # half of their protocol during a record-local identity repair.
    for field in (
        "owner_ref", "owner_refs", "pair_ref", "pair_refs", "pair_id", "controllers", "caller_worktree",
        "dispatch_attempt", "execution_leg", "session_backend", "owner",
    ):
        if data.get(field):
            raise ValueError(f"dependent {field} requires its owning relationship migration; record-local repair refused")
    embedded = _embedded_old_label(data, old)
    if embedded:
        raise ValueError(f"embedded historical machine identity at {embedded}; record-local repair refused")
    for field in ("resources", "last_finalize_released"):
        claims = data.get(field, [])
        if not isinstance(claims, list) or any(
            not isinstance(claim, dict)
            or claim.get("kind") not in ("pr", "bug", "issue", "effort")
            or claim.get("handoff_id")
            for claim in claims
        ):
            raise ValueError(f"dependent {field} requires resource-owner migration; record-local repair refused")
    for path, (store, _, candidate) in inventory.items():
        if (
            candidate is not data and candidate.get("repo") == project
            and candidate.get("worktree_id") == record_id
        ):
            raise ValueError(f"duplicate or misplaced record identity in {store}/{path.stem}; relocation is not owner migration")
        for field, ref in _references(candidate):
            if (
                ref.project == project and ref.worktree_id == record_id
                and ref.machine.casefold() in (old.casefold(), new.casefold())
            ):
                raise ValueError(f"inbound reference in {store}/{path.stem}:{field}; migrate the relationship through its owner")
        if candidate is data:
            for field, ref in _references(candidate):
                if ref.machine.casefold() == old.casefold():
                    raise ValueError(f"embedded historical reference at {field}; record-local repair refused")


def _replacement(raw: bytes, data: dict, new: str) -> str:
    text = raw.decode("utf-8")
    node = yaml.compose(text, Loader=pr_authority._StrictLoader)
    machine = [value for key, value in node.value if key.value == "machine"]
    if len(machine) != 1 or not isinstance(machine[0], yaml.ScalarNode):
        raise ValueError("record must have one explicit scalar machine field")
    scalar = machine[0]
    fixed = text[:scalar.start_mark.index] + json.dumps(new) + text[scalar.end_mark.index:]
    if tracking_yaml_repair.strict_mapping(fixed) != {**data, "machine": new}:
        raise ValueError("machine scalar cannot be changed without altering other record data")
    return fixed


def _inspect(project: str, record_id: str, old: str, new: str):
    config = cfg.load_project_config(project, include_control_plane_related_pr=False)
    registry = repos.read_registry(strict=True)
    registered = registry.repos.get(project)
    if registered is None or registered.repo_class not in ("worktree", "knowledge"):
        raise ValueError("receiving project must be a registered worktree or knowledge repository")
    local_root = registered.local_path(cfg.detect_platform())
    if not local_root or Path(local_root).resolve() != Path(config.default_repo.anchor).resolve():
        raise ValueError("receiving project configuration does not match its registered local root")
    _require_identity(config, old, new)
    inventory = _inventory(project)
    path = cfg.tracking_dir(project).resolve() / f"{record_id}.yaml"
    if path not in inventory:
        raise ValueError("exact record is absent from the receiving project's tracking store")
    _, raw, data = inventory[path]
    if data.get("worktree_id") != record_id or data.get("repo") != project:
        raise ValueError("record identity/project does not match its receiving tracking store; relocation is not owner migration")
    if data.get("machine") not in (old, new):
        raise ValueError("record machine does not match either exact requested label")
    if data["machine"] == new:
        for audit in path.parent.glob(f"{path.name}.owner-migration-*.json"):
            if audit.is_symlink():
                raise ValueError("owner migration audit symlink refused")
            receipt = json.loads(audit.read_text(encoding="utf-8"))
            if not isinstance(receipt, dict):
                raise ValueError("owner migration audit must be an object")
            if receipt.get("state") == "prepared":
                raise ValueError(f"prior owner migration has an incomplete audit: {audit}; inspect its backup before proceeding")
    record = tracking.load_record(path)
    if (
        data.get("platform") != cfg.detect_platform()
        or not legacy_wsl_record_locality.record_namespace_matches(record, config)
    ):
        raise ValueError("record lacks actual-platform and registered checkout-namespace provenance")
    _require_unlinked(data, inventory, project, record_id, old, new)
    return config, inventory, path, raw, data


def _provenance(config, data, new: str) -> dict:
    return {
        "configured_machine": config.machine, "platform": config.platform,
        "registered_anchor": config.default_repo.anchor,
        "checkout_namespace": config.default_repo.worktree_root,
        "checkout_path": data["worktree_path"],
        "old_label_matches": sorted(_names(execution_spaces.registry_for_config(config)[new])),
    }


def _apply(project: str, record_id: str, old: str, new: str, observed):
    config, inventory, path, raw, data = observed
    fixed = _replacement(raw, data, new)
    digest = hashlib.sha256(raw).hexdigest()
    audit_path = path.with_name(f"{path.name}.owner-migration-{digest[:16]}.json")
    evidence = {
        "version": 1, "scope": "local-owner-migration", "project": project,
        "worktree_id": record_id, "from_machine": old, "to_machine": new,
        "record": str(path), "before_sha256": digest,
        "after_sha256": hashlib.sha256(fixed.encode("utf-8")).hexdigest(),
        "scanned_records": len(inventory), "state": "prepared",
        "provenance": _provenance(config, data, new),
    }
    with pr_authority.guard(timeout=0), ExitStack() as stack:
        for candidate in sorted(inventory, key=str):
            lock = stack.enter_context(tracking._RecordLock(candidate, blocking=False))
            if not lock.acquired:
                raise TimeoutError("tracking record is busy; owner migration refused")
        fresh = _inspect(project, record_id, old, new)
        if fresh[1] != inventory:
            raise ValueError("tracking inventory changed; inspect again before owner migration")
        if audit_path.exists() or audit_path.is_symlink():
            raise ValueError("a prior migration audit exists; inspect it before retrying")
        # Prepare the durable audit before the one atomic record replacement.
        # An interrupted 'prepared' audit is not an assertion that migration ran.
        tracking._atomic_write(audit_path, json.dumps(evidence, indent=2) + "\n")
        backup = tracking_yaml_repair.apply_repair(path, raw, fixed)
        evidence.update(state="committed", backup=str(backup))
        try:
            tracking._atomic_write(audit_path, json.dumps(evidence, indent=2) + "\n")
        except OSError as exc:
            # The single record write already committed. Never report this as a
            # harmless refusal or retry an unrecorded apply automatically.
            raise MigrationAuditError(
                f"owner migrated but final audit failed: {audit_path}; original bytes: {backup}"
            ) from exc
    return backup, audit_path


def targeted_doctor(args) -> int:
    """Report first; only this dedicated scope can request an owner migration."""
    report = {
        "version": 1, "scope": "local-owner-migration",
        "mode": "apply" if args.fix else "report",
        "status": "blocked", "changed": False, "error": None,
        "worktree_id": args.migrate_owner,
        "from_machine": args.from_machine, "to_machine": args.to_machine,
    }
    try:
        if any(getattr(args, field, False) for field in (
            "gc_sessions", "prune_pivots", "apply_daemon_health", "guest_distribution",
            "repair_record", "bound_agent_end_field", "repair_creation", "seed_id",
        )):
            raise ValueError("owner migration cannot be combined with other doctor repair scopes")
        if (
            not isinstance(args.migrate_owner, str)
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]*", args.migrate_owner)
            or ".." in args.migrate_owner
        ):
            raise ValueError("owner migration requires one exact worktree ID, not a path or anchor")
        if not args.from_machine or not args.to_machine or args.from_machine == args.to_machine:
            raise ValueError("owner migration requires distinct exact --from-machine and --to-machine labels")
        project = cfg.project_name()
        if not project:
            raise ValueError("owner migration requires an explicitly selected receiving project")
        report["project"] = project
        observed = _inspect(project, args.migrate_owner, args.from_machine, args.to_machine)
        config, inventory, path, raw, data = observed
        report.update(
            scanned_records=len(inventory), record=str(path),
            before_sha256=hashlib.sha256(raw).hexdigest(),
            provenance=_provenance(config, data, args.to_machine),
            checkout_exists=Path(data["worktree_path"]).exists(),
            status="unchanged" if data["machine"] == args.to_machine else "eligible",
        )
        if args.fix and report["status"] == "eligible":
            backup, audit = _apply(project, args.migrate_owner, args.from_machine, args.to_machine, observed)
            report.update(status="migrated", changed=True, backup=str(backup), audit=str(audit))
    except MigrationAuditError as exc:
        report.update(status="migrated-audit-failed", changed=True, error=str(exc))
    except (OSError, ValueError, TypeError, KeyError, RuntimeError, yaml.YAMLError) as exc:
        report.update(status="blocked", error=str(exc))
    if args.json:
        print(json.dumps(report, indent=2))
    elif report["error"]:
        output.err(report["error"])
    else:
        output.ok(f"Owner migration {args.migrate_owner}: {report['status']}")
    return 1 if report["error"] else 0
