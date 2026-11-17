"""Adopt a new managed epoch for an unchanged, owner-proved native conversation."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from functools import partial
import json
import sqlite3
import time
from typing import Any
import uuid

from .acquisition_contract import AcquisitionRefused, WorktreeWriterRequirement
from .acquisition_executor import executor
from .creation_worktree_writer import JOURNAL_KEY, _binding, _read, retained
from .models import RetainedRecoveryRequest
from .retained_metadata_import import MetadataImport
from .retained_metadata_lineage import (
    CAPABILITY as CAPABILITY, COLUMN as COLUMN, CONFIG_KEY as CONFIG_KEY,
    EPOCH_KEY as EPOCH_KEY, adoption as adoption, history,
)
from .spawn_target import SpawnTarget


def _refuse(message: str) -> AcquisitionRefused:
    return AcquisitionRefused("retained_metadata_held", message)


def _inputs(row: dict[str, Any]) -> tuple[SpawnTarget, str | None, dict[str, Any]]:
    try:
        scope, _writer = _binding(row)
        facts = json.loads(row["creation_writer_json"])["facts"]
        options = facts["options"]
        if not isinstance(options, dict) or not options.keys() <= {
            "worktree_writer", "agent_name", "caller_owner_ref", "model", "effort",
            "mcp_servers", "copilot_args", "env_overrides", "dispatch_origin",
        }:
            raise ValueError("unsupported launch options")
        target = SpawnTarget.from_json(json.dumps(facts["target"]))
        if (
            target.type != "local" or target.host or target.elevated
            or target.spawn_command or target.container or target.codespace or target.remote_acp
            or target.native_resume_id
        ):
            raise ValueError("unsupported launch target")
        args = options.get("copilot_args") or []
        env = options.get("env_overrides") or {}
        if (
            not isinstance(args, list) or any(not isinstance(arg, str) for arg in args)
            or not isinstance(env, dict) or any(target.env.get(key) != value for key, value in env.items())
        ):
            raise ValueError("incomplete launch configuration")
        target = replace(
            target, cwd=scope.worktree_path, worktree_id=scope.worktree_id, explicit_cwd=True,
            copilot_args=[*target.copilot_args, *args],
            caller_owner_ref=target.caller_owner_ref or options.get("caller_owner_ref"),
            venue={**(target.venue or {}), JOURNAL_KEY: scope.key_digest},
        )
        from .session_core import _SessionCoreMixin
        from .spawn_target import REQUEST_OVERRIDES_KEY
        from .privileged_plugins import relay_agent

        previous = (target.venue or {}).get(REQUEST_OVERRIDES_KEY)
        previous = dict(previous) if isinstance(previous, dict) else {}
        controller_origin = previous.get(
            "controller_worktree_origin", "explicit" if target.caller_worktree is not None else "affinity",
        )
        privileged = relay_agent(target) is not None
        if _SessionCoreMixin._provider_backed_target(target) or privileged:
            request_args = previous.get("copilot_args") if options.get("copilot_args") is None else args
            request_env = previous.get("env") if options.get("env_overrides") is None else env
            request_args = [] if request_args is None else request_args
            request_env = {} if request_env is None else request_env
            if (
                not isinstance(request_args, list) or any(not isinstance(arg, str) for arg in request_args)
                or not isinstance(request_env, dict)
                or any(not isinstance(key, str) or not isinstance(value, str) or target.env.get(key) != value
                       for key, value in request_env.items())
            ):
                raise ValueError("incomplete provider override provenance")
            previous.update(
                env=dict(request_env), copilot_args=list(request_args),
                **({"mcp_servers": options.get("mcp_servers")} if privileged else {}),
            )
        target = replace(
            target, caller_worktree=(
                f"acquisition:creation:{scope.key_digest}"
                if target.caller_worktree is None and controller_origin != "explicit"
                else target.caller_worktree
            ),
            venue={
                **(target.venue or {}), REQUEST_OVERRIDES_KEY: {
                    **previous, "controller_worktree_origin": controller_origin,
                },
            },
        )
        servers = options.get("mcp_servers")
        servers = target.mcp_servers if servers is None else servers
        if not isinstance(servers, list) or any(not isinstance(server, dict) for server in servers):
            raise ValueError("incomplete tool configuration")
        config: dict[str, Any] = {"mcp_servers": servers}
        for source, destination in (("model", "model_override"), ("effort", "effort_override")):
            value = options.get(source)
            if value is not None:
                if not isinstance(value, str) or not value:
                    raise ValueError("invalid launch preference")
                config[destination] = value
        from .dispatch_origin import capture

        origin = capture(options.get("dispatch_origin"), target)
        if origin is not None:
            config["dispatch_origin"] = origin
        agent = options.get("agent_name")
        if agent is not None and (not isinstance(agent, str) or not agent):
            raise ValueError("invalid original agent")
        return target, agent, config
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise _refuse("Original launch configuration is incomplete or unsupported") from exc


def _unheld(manager: Any, row: dict[str, Any], *, imported_id: str | None = None) -> None:
    from .cold_outages import latest
    from .retained_session_recovery import _operations

    if (
        row["state"] != "complete" or not row["native_session_id"]
        or json.loads(row["creation_handoffs_json"] or "{}")
        or any(item["state"] not in {"complete", "not-started"} for item in _operations(row).values())
        or latest(manager.db, row["session_id"]) is not None
        or manager.get_session(row["session_id"]) is not None
        or manager.db.get_session(row["session_id"]) is not None
    ):
        raise _refuse("Original metadata, launch, handoff or recovery custody requires reconciliation")
    scope, _writer = _binding(row)
    if any(
        session.target.worktree_id == scope.worktree_id and session.session_id != imported_id
        for session in manager.list_sessions()
    ) or manager.db.execute_read(
        "SELECT 1 FROM sessions WHERE id<>? AND "
        "(acp_session_id=? OR json_extract(target_json,'$.worktree_id')=?) LIMIT 1",
        (imported_id or "", row["native_session_id"], scope.worktree_id),
    ):
        raise _refuse("Another managed record retains the native workspace")
    if manager.db.execute_read(
        "SELECT 1 FROM acquisition_launch_gates WHERE worktree_id=? UNION ALL "
        "SELECT 1 FROM live_sessions WHERE worktree_id=? AND status='live' AND updated_at>=?",
        (scope.worktree_id, scope.worktree_id, time.time() - 120),
    ):
        raise _refuse("A launch or represented native holder retains the workspace")
    inventory = manager._host_index.locked_inventory() if manager._host_index is not None else None
    if inventory is None or not inventory.storage_complete:
        raise _refuse("Metadata adoption requires a complete retained Host inventory")
    for host in inventory.records:
        managed = manager.get_session(host.session_id)
        if managed is None or managed.target.worktree_id == scope.worktree_id:
            raise _refuse("Metadata adoption cannot replace unknown or retained Host custody")


async def prepare(
    manager: Any, predecessor_id: str, request: RetainedRecoveryRequest,
    requirement: WorktreeWriterRequirement | None, ready: Callable[[], bool],
) -> tuple[Any, dict[str, Any]]:
    """Commit metadata and its new epoch atomically; never launch or send."""
    from .acquisition_authority import read_worktree_record, worktree_command
    from .acquisition_writer_inspection import _instance

    row = _read(manager.db, request.journal_key)
    if row is None or requirement is None:
        raise _refuse("Metadata adoption requires the original keyed journal and writer")
    scope, _writer = _binding(row)
    if (
        row["session_id"] != predecessor_id or row["native_session_id"] != request.native_session_id
        or scope.worktree_writer != requirement
    ):
        raise _refuse("Metadata adoption changed the original native, key or writer")
    with executor(manager.db, f"creation-writer:{scope.key_digest}"), executor(
        manager.db, scope.worktree_id,
    ), executor(manager.db, f"retained-recovery:{scope.worktree_id}"):
        row = _read(manager.db, request.journal_key)
        if row is None:
            raise _refuse("Metadata adoption lost its original journal")
        boundary = adoption(row)
        if boundary is not None:
            if boundary["request"] != request.model_dump() or boundary["predecessor_id"] != predecessor_id:
                raise _refuse("Original metadata adoption must be inspected without replacement")
            session = manager.get_session(boundary["session_id"])
            if (
                session is None or session.acp_session_id != request.native_session_id
                or (session.target.venue or {}).get(JOURNAL_KEY) != request.journal_key
                or manager.db.get_session(session.session_id) is None
            ):
                raise _refuse("Committed metadata adoption is unavailable; its epoch remains excluded")
            return session, boundary
        _unheld(manager, row)
        instance = _instance()
        if (
            not ready() or manager.is_draining
            or instance.get("installation_root") != request.bridge_installation
        ):
            raise _refuse("Metadata adoption requires its ready receiving installation")
        target, agent_name, config = _inputs(row)
        from .hosting_policy import require_target_hosting

        await require_target_hosting(target, operation="retained metadata adoption")
        async with retained(manager.db, row):
            from .acquisition_worktree_writer import current

            selected = current()
            if selected is None:
                raise _refuse("Metadata adoption lost its compatible executing writer")
            command = partial(worktree_command, owner_prefix=selected.prefix)

            async def observe() -> dict[str, Any]:
                record = await read_worktree_record(scope.worktree_id, scope.project, command=command)
                head = await command(
                    ["head-session", "--worktree-id", scope.worktree_id, "--json"], scope.project,
                )
                if (
                    any(record.get(key) != json.loads(row["worktree_json"]).get(key)
                        for key in ("id", "repo", "path", "creation_nonce"))
                    or record.get("status") in {"finalizing", "finalized", "archived", "complete", "completed"}
                    or head.get("tracked") is not True or head.get("state") != "active"
                    or head.get("head_session") != request.native_session_id or head.get("pending_handoffs")
                    or type(head.get("head_revision")) is not int
                    or head["head_revision"] != request.head_revision
                ):
                    raise _refuse("Authoritative native head or workspace incarnation changed")
                return head

            before = await observe()
            admission = await command([
                "embody", "--worktree-id", scope.worktree_id, "--json",
                "--bridge-acquisition", request.native_session_id, "--require-native-admission", "--dry-run",
            ], scope.project)
            if not (
                admission.get("ok") is True and admission.get("dry_run") is True
                and type(admission.get("native_admission_version")) is int
                and admission["native_admission_version"] == 1
                and admission.get("worktree_id") == scope.worktree_id
                and admission.get("work_dir") == scope.worktree_path
                and admission.get("resume_session") == request.native_session_id
            ):
                raise _refuse("Native owner did not prove dormant original-conversation admission")
            if await observe() != before:
                raise _refuse("Native owner changed during metadata preparation")
            _unheld(manager, row)
            session_id = str(uuid.uuid4())
            target = replace(target, venue={**(target.venue or {}), EPOCH_KEY: session_id})
            boundary = {
                "version": 1, "request": request.model_dump(),
                "predecessor_id": predecessor_id, "session_id": session_id,
                "history": history(predecessor_id, session_id),
                "prior_operations": sorted(json.loads(row["creation_recoveries_json"] or "{}")),
            }
            config[CONFIG_KEY] = boundary

            def commit(conn: sqlite3.Connection) -> None:
                actual = conn.execute(
                    "SELECT * FROM workspace_embodiments WHERE key_digest=?", (request.journal_key,),
                ).fetchone()
                if (
                    actual is None or dict(actual) != row or manager.is_draining or not ready()
                    or _instance() != instance
                    or conn.execute("SELECT 1 FROM sessions WHERE id=?", (predecessor_id,)).fetchone()
                    or conn.execute(
                        "SELECT 1 FROM acquisition_launch_gates WHERE worktree_id=?",
                        (scope.worktree_id,),
                    ).fetchone()
                ):
                    raise _refuse("Original metadata custody changed before atomic adoption")
                conn.execute(
                    f"UPDATE workspace_embodiments SET {COLUMN}=? WHERE key_digest=?",
                    (json.dumps(boundary, sort_keys=True), request.journal_key),
                )

            imported = MetadataImport(
                session_id, target.to_json(), request.native_session_id, agent_name,
                f"acquisition:creation:{request.journal_key}", config, commit,
            )
            session = manager.import_native_session(
                target, request.native_session_id, agent_name=agent_name, _metadata=imported,
            )
            return session, boundary
