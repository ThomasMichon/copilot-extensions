"""Cross-machine claim-handoff accept support helpers."""

from __future__ import annotations

import base64
import shlex
import subprocess
from collections.abc import Callable

from agent_procutil import no_window_kwargs
import yaml

from . import claimant, tracking
from . import config as cfg
from .execution_spaces import (
    ExecutionSpaceError, registry_for_config, require_owner_identity,
    require_project_record_mutation,
)
from .lease_config import load_lease_settings
from .lease_store import GitLeaseStore, LeaseConflict, LeaseLost, LeaseSnapshot

_BUNDLE_FENCE_KIND = "claim-handoff"
_REMOTE_ACCEPT_TIMEOUT = 15.0


def same_machine(a: str, b: str, *, project: str | None = None) -> bool:
    """Authorize canonical local refs through receiving-side project settings."""
    try:
        config = cfg.load_project_config(project) if project else cfg.load_config()
    except (OSError, ValueError) as exc:
        raise ExecutionSpaceError(f"cannot establish handoff execution-space authority: {exc}") from exc
    return require_owner_identity(a, config) and require_owner_identity(b, config)


def require_handoff_record(record, error_type: type[Exception]) -> None:
    """Fence the freshly loaded source, consumer, or child before mutation."""
    try:
        require_project_record_mutation(record)
    except ExecutionSpaceError as exc:
        raise error_type(str(exc)) from exc


def load_handoff_record(path, *, role: str, error_type: type[Exception]):
    """Read and authorize a lock-held handoff record without changing status rules."""
    try:
        record = tracking.load_record(path)
    except (OSError, ValueError, TypeError, KeyError, yaml.YAMLError) as exc:
        raise error_type(f"cannot read {role} worktree record {path}: {exc}") from exc
    require_handoff_record(record, error_type)
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
        config = cfg.load_project_config(consumer.project)
        if not require_owner_identity(consumer.machine, config) or not require_owner_identity(machine, config):
            raise error_type("claim bundle consumer is not the current registered execution space")
        if (
            any(entry.execution_platform for entry in registry_for_config(config).values())
            and source.machine != consumer.machine
            and any(claim["kind"] == "worktree" for claim in bundle.claims)
        ):
            raise error_type("cross-space owned worktree handoff remains unsupported; source claims are retained")
    except (OSError, ValueError, ExecutionSpaceError) as exc:
        raise error_type(f"cannot establish claim-bundle consumer authority: {exc}") from exc


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
