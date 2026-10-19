"""Cross-machine claim-handoff accept support helpers."""

from __future__ import annotations

import base64
import json
import shlex
import subprocess
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from agent_procutil import no_window_kwargs
import yaml

from . import claimant, legacy_wsl_record_locality, tracking
from . import config as cfg
from .execution_spaces import (
    ExecutionSpaceError, registry_for_config, require_owner_identity,
    project_record_config, require_record_mutation,
)
from .lease_config import load_lease_settings
from .lease_store import GitLeaseStore, LeaseConflict, LeaseLost, LeaseSnapshot

if TYPE_CHECKING:
    from .claim_handoffs import ClaimBundle

_BUNDLE_FENCE_KIND = "claim-handoff"
_REMOTE_ACCEPT_TIMEOUT = 15.0


def ordered_lock_paths(*paths: Path) -> tuple[Path, ...]:
    unique = {str(path): path for path in paths}
    return tuple(unique[key] for key in sorted(unique))


def accepted_child_owner(bundle: ClaimBundle | None, snapshot: dict) -> str | None:
    if bundle is None or bundle.state != "accepted":
        return None
    return dict(bundle.source_owner_refs).get(snapshot["ref"], bundle.source)


def owner_worktree_ref(value: str) -> str:
    parsed = tracking.parse_claim_ref(value)
    return (
        tracking.format_claim_ref(parsed.machine, parsed.project, parsed.worktree_id)
        if parsed is not None and parsed.is_qualified else value
    )


def parse_remote_bundle(stdout: str) -> ClaimBundle | None:
    from .claim_handoffs import _bundle_from_dict

    if not stdout:
        return None
    start, end = stdout.find("{"), stdout.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        payload = json.loads(stdout[start:end + 1])
    except (TypeError, ValueError):
        return None
    return _bundle_from_dict(payload)


def same_machine(a: str, b: str, *, project: str | None = None) -> bool:
    """Authorize canonical local refs through receiving-side project settings."""
    try:
        config = (
            cfg.load_project_config(project, include_control_plane_related_pr=False)
            if project else cfg.load_config(include_control_plane_related_pr=False)
        )
    except (OSError, ValueError) as exc:
        raise ExecutionSpaceError(f"cannot establish handoff execution-space authority: {exc}") from exc
    return require_owner_identity(a, config) and require_owner_identity(b, config)


def require_handoff_record(record: tracking.WorktreeRecord, error_type: type[Exception]) -> None:
    """Require actual platform and receiving namespace for every handoff actor."""
    try:
        config = project_record_config(record)
        require_record_mutation(record, config)
        expected_record = cfg.project_dir(record.repo) / "worktrees" / f"{record.worktree_id}.yaml"
        if record.yaml_path != expected_record or record.yaml_path.is_symlink():
            raise ExecutionSpaceError("handoff record does not match its receiving project's record path")
        if (
            record.platform != cfg.detect_platform()
            or not legacy_wsl_record_locality.record_namespace_matches(record, config)
        ):
            raise ExecutionSpaceError("handoff record has unproven execution platform or namespace")
        if record.machine != config.machine:
            ref = tracking.parse_claim_ref(
                tracking.format_claim_ref(record.machine, record.repo, record.worktree_id),
            )
            if legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(ref, config) is None:
                raise ExecutionSpaceError("handoff record has no verified local legacy WSL authority")
    except ExecutionSpaceError as exc:
        raise error_type(str(exc)) from exc


def local_record_ref(
    ref: tracking.ClaimRef, *, machine: str, completion_owner: str | None = None,
) -> bool:
    """Resolve a complete actor under its own receiving project's authority."""
    if not ref.is_qualified:
        return False
    try:
        try:
            config = cfg.load_project_config(ref.project, include_control_plane_related_pr=False)
        except cfg.ProjectNotRegisteredError:
            project = cfg.project_dir(ref.project)
            evidence = [project / "config.yaml", project / "worktrees" / f"{ref.worktree_id}.yaml"]
            if any(path.exists() or path.is_symlink() for path in evidence):
                raise
            return False
        if not require_owner_identity(machine, config):
            return False
        legacy = legacy_wsl_record_locality.resolve_legacy_wsl_owner_ref(ref, config)
        if legacy is None and not require_owner_identity(ref.machine, config):
            return False
        path = legacy or cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml"
        if not path.is_file() or path.is_symlink():
            return False
        record = load_handoff_record(
            path, role="actor", error_type=ExecutionSpaceError, completion_owner=completion_owner,
        )
        if record.worktree_id != ref.worktree_id or record.repo != ref.project:
            return False
        return True
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError) as exc:
        raise ExecutionSpaceError(f"cannot establish handoff record authority: {exc}") from exc


def same_local_records(
    left: tracking.ClaimRef, right: tracking.ClaimRef, *, machine: str | None = None,
) -> bool:
    if machine is None:
        machine = cfg.load_project_config(
            right.project, include_control_plane_related_pr=False,
        ).machine
    return local_record_ref(right, machine=machine) and local_record_ref(left, machine=machine)


def local_transition_actor(ref: tracking.ClaimRef) -> bool:
    config = cfg.load_project_config(ref.project, include_control_plane_related_pr=False)
    if local_record_ref(ref, machine=config.machine):
        return True
    path = cfg.project_dir(ref.project) / "worktrees" / f"{ref.worktree_id}.yaml"
    # Cancelling intent after a vanished canonical source moves no claims.
    return not path.exists() and not path.is_symlink() and require_owner_identity(ref.machine, config)


def accept_child_path(
    snapshot: dict[str, str], *, consumer: tracking.ClaimRef, error_type: type[Exception],
    bundle: ClaimBundle | None = None,
) -> Path:
    parsed = tracking.parse_claim_ref(snapshot["ref"])
    if parsed is None or not parsed.is_qualified:
        raise error_type(
            "worktree claim ref must be qualified "
            f"machine/project/worktree_id (got {snapshot['ref']!r})"
        )
    completion_owner = accepted_child_owner(bundle, snapshot)
    if completion_owner is not None:
        config = cfg.load_project_config(consumer.project, include_control_plane_related_pr=False)
        local = (
            local_record_ref(consumer, machine=config.machine)
            and local_record_ref(parsed, machine=config.machine, completion_owner=completion_owner)
        )
    else:
        local = same_local_records(parsed, consumer)
    if not local:
        raise error_type(
            f"worktree claim {snapshot['ref']} targets machine {parsed.machine}, "
            f"but bundle consumer lives on {consumer.machine}"
        )
    return cfg.project_dir(parsed.project) / "worktrees" / f"{parsed.worktree_id}.yaml"


def load_handoff_record(
    path: Path, *, role: str, error_type: type[Exception], completion_owner: str | None = None,
) -> tracking.WorktreeRecord:
    """Read and authorize a lock-held handoff record without changing status rules."""
    try:
        record = tracking.load_record(path)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise error_type(f"cannot read {role} worktree record {path}: {exc}") from exc
    authority_view = (
        replace(record, owner_ref=None)
        if completion_owner is not None
        and owner_worktree_ref(record.owner_ref or "") == owner_worktree_ref(completion_owner)
        else record
    )
    try:
        require_handoff_record(authority_view, error_type)
    except error_type as exc:
        if completion_owner is not None:
            raise error_type(
                f"accepted alias completion requires durable source-owner proof: {exc}"
            ) from exc
        raise
    if record.status in {"finalizing", "orphaned"}:
        raise error_type(
            f"{role} worktree "
            f"{tracking.format_claim_ref(record.machine, record.repo, record.worktree_id)} "
            f"is {record.status}"
        )
    return record


def require_handoff_consumer(bundle, machine: str, error_type: type[Exception]) -> None:
    """Reject unsupported scoped ownership transfer before releasing source claims."""
    consumer = tracking.parse_claim_ref(bundle.consumer)
    source = tracking.parse_claim_ref(bundle.source)
    if consumer is None or source is None:
        raise error_type("claim bundle has invalid source or consumer identity")
    try:
        config = cfg.load_project_config(
            consumer.project, include_control_plane_related_pr=False,
        )
        if (
            bundle.state != "accepted"
            and any(entry.execution_platform for entry in registry_for_config(config).values())
            and not same_local_records(source, consumer, machine=machine)
            and any(claim["kind"] == "worktree" for claim in bundle.claims)
        ):
            raise error_type("cross-space owned worktree handoff remains unsupported; source claims are retained")
        if not local_record_ref(consumer, machine=machine):
            raise error_type("claim bundle consumer is not the current registered execution space")
    except (OSError, ValueError, ExecutionSpaceError) as exc:
        raise error_type(f"cannot establish claim-bundle consumer authority: {exc}") from exc


def capture_source_owner_refs(
    bundle: ClaimBundle, source: tracking.WorktreeRecord, *, same_worktree: Callable[[str, str], bool],
    error_type: type[Exception],
) -> tuple[tuple[str, str], ...]:
    """Persist only child-specific equivalence proved before source settlement."""
    machine = project_record_config(source).machine
    proofs: dict[str, str] = {}
    for snapshot in bundle.claims:
        if snapshot["kind"] != "worktree":
            continue
        child_ref = tracking.parse_claim_ref(snapshot["ref"])
        if child_ref is None or not local_record_ref(child_ref, machine=machine):
            continue
        path = cfg.project_dir(child_ref.project) / "worktrees" / f"{child_ref.worktree_id}.yaml"
        child = load_handoff_record(path, role="child", error_type=error_type)
        owner = child.owner_ref or ""
        owner_identity = owner_worktree_ref(owner)
        if same_worktree(owner_identity, bundle.source):
            if owner != bundle.source:
                proofs[snapshot["ref"]] = owner
        elif not same_worktree(owner_identity, bundle.consumer):
            raise error_type(f"child worktree {snapshot['ref']} is not owned by this bundle's source/consumer")
    return tuple(proofs.items())


def acquire_bundle_fence(
    *,
    bundle_id: str,
    source: str,
    consumer: str,
    actor: str,
    error_type: type[Exception],
) -> tuple[GitLeaseStore, LeaseSnapshot]:
    """Acquire the shared cross-machine mutation fence for one bundle."""
    store = GitLeaseStore(load_lease_settings())
    try:
        snapshot = store.acquire(
            _BUNDLE_FENCE_KIND,
            bundle_id,
            actor,
            context={
                "bundle_id": bundle_id,
                "source": source,
                "consumer": consumer,
                "actor": actor,
            },
        )
    except LeaseConflict as exc:
        raise error_type(
            f"claim bundle {bundle_id} is already being mutated by "
            f"{exc.snapshot.record.holder}"
        ) from exc
    except Exception as exc:
        raise error_type(
            f"cannot acquire claim-bundle fence {bundle_id}: {exc}"
        ) from exc
    return store, snapshot


def release_bundle_fence(
    store: GitLeaseStore,
    *,
    bundle_id: str,
    token: str,
    error_type: type[Exception],
) -> None:
    """Release a previously acquired claim-handoff fence."""
    try:
        store.release(_BUNDLE_FENCE_KIND, bundle_id, token)
    except LeaseLost:
        raise
    except Exception as exc:
        raise error_type(
            f"cannot release claim-bundle fence {bundle_id}: {exc}"
        ) from exc


def remote_accept_source(
    *,
    source_machine: str,
    source_project: str,
    bundle_id: str,
    actor: str,
    parse_bundle: Callable[[str], object | None],
    error_type: type[Exception],
    timeout: float = _REMOTE_ACCEPT_TIMEOUT,
):
    """Invoke the source machine's local settle verb over SSH."""
    resolved = claimant.resolve_machine_ssh(source_machine)
    if resolved is None:
        raise error_type(
            f"cannot resolve SSH target for source machine {source_machine}"
        )
    alias, shell = resolved
    inner = shlex.join(
        [
            source_project,
            "claims",
            "handoff",
            "accept-source",
            bundle_id,
            "--actor",
            actor,
            "--json",
        ]
    )
    if shell == "pwsh":
        enc = base64.b64encode(inner.encode("utf-16-le")).decode("ascii")
        remote_cmd = f"pwsh -NoProfile -WindowStyle Hidden -EncodedCommand {enc}"
    else:
        remote_cmd = f"bash -lc {shlex.quote(inner)}"
    try:
        proc = subprocess.run(
            [
                "ssh",
                "-o",
                "BatchMode=yes",
                "-o",
                f"ConnectTimeout={max(1, int(timeout))}",
                alias,
                remote_cmd,
            ],
            capture_output=True,
            text=True,
            timeout=timeout + 4,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise error_type(
            f"source-side accept timed out for claim bundle {bundle_id}: {exc}"
        ) from exc
    except (subprocess.SubprocessError, OSError) as exc:
        raise error_type(
            f"cannot run source-side accept for claim bundle {bundle_id}: {exc}"
        ) from exc
    if proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or f"exit {proc.returncode}").strip()
        raise error_type(
            f"source-side accept failed for claim bundle {bundle_id}: {detail}"
        )
    parsed = parse_bundle(proc.stdout)
    if parsed is None:
        raise error_type(
            f"source-side accept returned malformed JSON for claim bundle {bundle_id}"
        )
    return parsed


def load_accept_bundle(
    path,
    bundle_id: str,
    *,
    actor: str,
    load_registry: Callable,
    same_worktree: Callable[[str, str], bool],
    validate_bundle: Callable[[object], None],
    error_type: type[Exception],
):
    """Load and validate one bundle for acceptance."""
    bundles = load_registry(path)
    index = next(
        (i for i, bundle in enumerate(bundles) if bundle.bundle_id == bundle_id),
        None,
    )
    if index is None:
        raise error_type(f"claim bundle not found: {bundle_id}")
    bundle = bundles[index]
    if not same_worktree(actor, bundle.consumer):
        raise error_type(f"only bundle consumer {bundle.consumer} may accept it")
    if bundle.state in {"declined", "cancelled"}:
        raise error_type(f"claim bundle {bundle_id} is already {bundle.state}")
    if bundle.state not in {"offered", "accepting", "accepted"}:
        raise error_type(f"claim bundle {bundle_id} is {bundle.state}, not offered")
    validate_bundle(bundle)
    return bundles, index, bundle
