"""Naming compatibility inside an already-proven completed creation receipt."""

from __future__ import annotations

from machine_transport import resolve_local_execution_context

from . import config as cfg, creation_allocation, execution_spaces, tracking
from .legacy_wsl_record_locality import record_namespace_matches
from .worktree_machine_identity import WorktreeMachineIdentity


def execution_origin(config: cfg.Config) -> dict:
    context = execution_spaces.current_execution_context(config)
    return {
        "version": 1, "hostname": context.hostname,
        "machine": context.machine, "environment": context.environment,
        "execution_machine": context.execution_machine, "legacy_machine": config.machine,
        "configured_machine": config.configured_machine,
    }


def validate_origin(origin: object) -> dict:
    names = {"hostname", "machine", "environment", "execution_machine", "legacy_machine"}
    if (
        not isinstance(origin, dict) or set(origin) != names | {"version", "configured_machine"}
        or type(origin["version"]) is not int or origin["version"] != 1
        or any(
            not isinstance(origin[key], str) or not origin[key].strip()
            or any(char in origin[key] for char in "\r\n\0")
            for key in names
        )
        or origin["environment"] not in ("windows", "wsl", "linux")
        or (origin["configured_machine"] is not None and (
            not isinstance(origin["configured_machine"], str)
            or not origin["configured_machine"].strip()
        ))
    ):
        raise ValueError("Invalid retained creation execution origin")
    return origin


def require_completed_record(
    record: tracking.WorktreeRecord, config: cfg.Config, receipt: dict,
) -> None:
    """Called only after journal facts, completion, nonce and record proof.

    This is read-only replay compatibility, never mutation or fresh-allocation
    authority. A stored owner or arbitrary allocation cannot enter this path.
    """
    context = execution_spaces.current_execution_context(config)
    if (
        record.repo != config.repo_name or record.platform != cfg.detect_platform()
        or (record.machine_identity is not None and record.platform != config.platform)
        or not record_namespace_matches(record, config)
    ):
        raise execution_spaces.ExecutionSpaceError(
            "Retained creation conflicts with the receiving project/platform namespace",
        )
    observed = resolve_local_execution_context(
        execution_spaces.registry_for_config(config), hostname=context.hostname,
        environment=context.environment, configured_machine=config.configured_machine,
    )
    unchanged_binding = False
    if "execution_origin" not in receipt:
        # Old receipts did not retain observations. Only the literal OS-derived
        # identity can be independently reconstructed, not a former custom alias.
        original = resolve_local_execution_context(
            {}, hostname=context.hostname, environment=context.environment,
        )
        expected = WorktreeMachineIdentity(
            original.machine, original.environment, original.execution_machine,
        )
        if (
            (record.machine_identity is not None and record.machine_identity != expected)
            or (record.machine_identity is None and record.machine not in {
                original.hostname.casefold(), original.execution_machine,
            })
            or config.configured_machine is not None
        ):
            if config.machine.casefold() not in {
                observed.hostname.casefold(), observed.execution_machine.casefold(),
                (config.configured_machine or "").casefold(),
            }:
                raise execution_spaces.ExecutionSpaceError(
                    "Retained creation has no observed receiving execution binding",
                )
            if not execution_spaces.require_owner_identity(record.execution_machine, config):
                raise execution_spaces.ExecutionSpaceError(
                    "Retained creation has a different execution owner",
                )
            execution_spaces.require_record_mutation(record, config)
            return
    else:
        origin = validate_origin(receipt["execution_origin"])
        expected = WorktreeMachineIdentity(
            origin["machine"], origin["environment"], origin["execution_machine"],
        )
        if (
            origin["hostname"].casefold() != context.hostname.casefold()
            or origin["environment"] != context.environment
            or origin["configured_machine"] != config.configured_machine
            or (record.machine_identity is not None and record.machine_identity != expected)
            or (record.machine_identity is None and record.machine != origin["legacy_machine"])
        ):
            raise execution_spaces.ExecutionSpaceError(
                "Retained creation conflicts with its original execution origin",
            )
        unchanged_binding = (
            config.machine == origin["legacy_machine"]
            and context.machine == origin["machine"]
            and context.execution_machine == origin["execution_machine"]
        )
    if config.configured_machine is None and config.machine.casefold() not in {
        observed.hostname.casefold(), observed.execution_machine.casefold(),
    } and not unchanged_binding:
        raise execution_spaces.ExecutionSpaceError(
            "Retained creation has no observed receiving execution binding",
        )
    if "record_identity" not in receipt:
        execution_spaces.require_record_mutation(record, config)
        return
    creation_allocation.require_record_evidence(record, receipt["record_identity"])
