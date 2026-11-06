"""Retain ordinary keyed creation's serializer in its own embodiment journal."""

from __future__ import annotations

import contextlib
from collections.abc import AsyncIterator, Awaitable, Callable
from dataclasses import dataclass, replace
from functools import wraps
import hashlib
import json
import re
import uuid
from typing import Any

from .acquisition_contract import AcquisitionRefused, WorktreeWriterRequirement
from . import acquisition_worktree_writer as writers

CAPABILITY = "retained_workspace_creation_writer"
RESUME_CAPABILITY = "retained_workspace_creation_writer_resume"
JOURNAL_KEY = "_agent_bridge_creation_writer"


@dataclass(frozen=True)
class CreationWriterScope:
    key_digest: str
    project: str
    worktree_id: str
    worktree_path: str
    worktree_writer: WorktreeWriterRequirement


def _refuse(message: str) -> AcquisitionRefused:
    return AcquisitionRefused("worktree_writer_unconfirmed", message)


def require_form(target: Any, *, key: str, provider_instance: bool) -> None:
    if (
        not isinstance(key, str) or not key.strip() or len(key) > 1024
        or provider_instance or target.type != "local" or not target.worktree_id
        or not target.project or target.explicit_cwd or target.spawn_command
        or target.container or target.codespace or target.remote_acp
    ):
        raise AcquisitionRefused(
            "worktree_writer_form_unsupported",
            "retained creation writer requires a prepared local ACP worktree",
            effect="not_started",
        )


def _read(db: Any, digest: str) -> dict | None:
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
        return None
    rows = db.execute_read("SELECT * FROM workspace_embodiments WHERE key_digest=?", (digest,))
    return dict(rows[0]) if rows else None


def _binding(row: dict) -> tuple[CreationWriterScope, writers.RetainedWriter]:
    try:
        data = json.loads(row["creation_writer_json"])
        if set(data) != {"version", "facts", "worktree_writer", "binding"} or (
            type(data["version"]) is not int or data["version"] != 1
        ):
            raise ValueError("invalid binding")
        facts = data["facts"]
        digest = hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest()
        requirement = WorktreeWriterRequirement.model_validate(data["worktree_writer"])
        saved = json.loads(row["worktree_json"])
        target = facts["target"]
        if (
            digest != row["facts_digest"] or target["type"] != "local"
            or target["worktree_id"] != saved["id"] or target["project"] != saved["repo"]
            or saved["creation_nonce"] != requirement.creation_nonce
            or facts["options"]["worktree_writer"] != requirement.model_dump()
            or row["runtime_custody_json"] is not None or row["creation_facts_json"] is not None
        ):
            raise ValueError("changed facts")
        scope = CreationWriterScope(
            row["key_digest"], saved["repo"], saved["id"], saved["path"], requirement,
        )
        writer = writers.RetainedWriter(scope, writers._prefix(data["binding"]))
    except (ValueError, TypeError, KeyError) as exc:
        raise _refuse("original keyed creation writer journal is unavailable") from exc
    return scope, writer


async def _verify(scope: CreationWriterScope, saved: dict) -> None:
    from .workspace_creation import _workspace

    await _workspace(scope.project, scope.worktree_id, expected=saved)


@contextlib.asynccontextmanager
async def retained(db: Any, row: dict) -> AsyncIterator[CreationWriterScope]:
    """Requalify the original command, never the currently selected writer."""
    scope, writer = _binding(row)
    await writers._qualify(writer)
    token = writers._current.set(writer)
    try:
        await _verify(scope, json.loads(row["worktree_json"]))
        yield scope
    finally:
        writers._current.reset(token)


def creation_writer(method: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
    @wraps(method)
    async def run(
        manager: Any, target: Any, *, key: str, prompt: str | None = None,
        provider_instance: bool = False,
        worktree_writer: WorktreeWriterRequirement | dict | None = None, **options: Any,
    ) -> Any:
        from .acquisition_executor import executor
        from .workspace_creation import creation_facts

        digest = hashlib.sha256(json.dumps([target.project, key]).encode()).hexdigest()
        with executor(manager.db, f"creation-writer:{digest}"):
            original = _read(manager.db, digest)
            if worktree_writer is None:
                if original and original["creation_writer_json"] is not None:
                    raise _refuse("original creation writer requirement cannot be dropped")
                return await method(
                    manager, target, key=key, prompt=prompt,
                    provider_instance=provider_instance, **options,
                )
            requirement = WorktreeWriterRequirement.model_validate(worktree_writer)
            require_form(target, key=key, provider_instance=provider_instance)
            facts = creation_facts(target, prompt, {
                **options, "worktree_writer": requirement.model_dump(),
            })
            if original is None:
                if manager.db.execute_read(
                    "SELECT 1 FROM workspace_embodiments WHERE creation_writer_json IS NOT NULL "
                    "AND json_extract(worktree_json,'$.id')=? "
                    "AND json_extract(worktree_json,'$.repo')=? LIMIT 1",
                    (target.worktree_id, target.project),
                ):
                    raise _refuse("workspace writer belongs to its original creation key")
                from .session_lifecycle_cli import _agent_worktrees_pinned_selection
                from ._peer_launch import ContextRefused
                from .workspace_creation import _workspace

                try:
                    prefix, root, _python = _agent_worktrees_pinned_selection()
                except ContextRefused as exc:
                    raise _refuse(str(exc)) from exc
                if str(root) != requirement.identity.installation_root:
                    raise _refuse("selected Worktrees installation changed")
                # The exact record supplies the path; no caller command or new allocation is accepted.
                probe = CreationWriterScope(
                    digest, target.project, target.worktree_id, target.cwd, requirement,
                )
                writer = writers.RetainedWriter(probe, tuple(prefix))
                await writers._qualify(writer)
                token = writers._current.set(writer)
                try:
                    saved, _head = await _workspace(target.project, target.worktree_id)
                finally:
                    writers._current.reset(token)
                if saved["repo"] != target.project or saved["creation_nonce"] != requirement.creation_nonce:
                    raise _refuse("original workspace creation nonce changed")
                document = json.dumps({
                    "version": 1, "facts": facts, "worktree_writer": requirement.model_dump(),
                    "binding": writer.document(),
                }, sort_keys=True)
                with manager.db._runtime_transaction(write=True, full_sync=True) as conn:
                    conn.execute(
                        "INSERT INTO workspace_embodiments "
                        "(key_digest,facts_digest,state,session_id,worktree_json,creation_writer_json) "
                        "VALUES (?,?,'allocated',?,?,?)",
                        (digest, hashlib.sha256(json.dumps(facts, sort_keys=True).encode()).hexdigest(),
                         str(uuid.uuid4())[:12], json.dumps(saved), document),
                    )
                original = _read(manager.db, digest)
            scope, _writer = _binding(original)
            if (
                scope.worktree_writer != requirement
                or json.loads(original["creation_writer_json"])["facts"] != facts
            ):
                raise _refuse("creation key is bound to another writer or embodiment")
            async with retained(manager.db, original):
                return await method(
                    manager, target, key=key, prompt=prompt,
                    worktree_writer=requirement, **options,
                )
    return run


def require_journal(row: dict) -> None:
    if row["creation_writer_json"] is None:
        return
    scope, writer = _binding(row)
    actual = writers.current()
    if actual != writer or not isinstance(actual.request, CreationWriterScope):
        raise _refuse("keyed creation lost its original writer owner")
    if scope.key_digest != row["key_digest"]:
        raise _refuse("keyed creation writer changed journal")


def bind_target(target: Any) -> Any:
    writer = writers.current()
    if writer is None or not isinstance(writer.request, CreationWriterScope):
        return target
    return replace(target, venue={**(target.venue or {}), JOURNAL_KEY: writer.request.key_digest})


def observe_native(db: Any, session: Any) -> None:
    """Retain the observed native before its first delegated registration."""
    writer = writers.current()
    if writer is None or not isinstance(writer.request, CreationWriterScope):
        return
    writers.require_target(session.target)
    if not session.acp_session_id:
        raise _refuse("creation has no observed native identity")
    scope = writer.request
    with db._runtime_transaction(write=True, full_sync=True) as conn:
        row = conn.execute(
            "SELECT * FROM workspace_embodiments WHERE key_digest=?", (scope.key_digest,),
        ).fetchone()
        if row is None:
            raise _refuse("native observation lost its keyed creation")
        require_journal(dict(row))
        if row["session_id"] == session.session_id:
            if row["native_session_id"] not in (None, session.acp_session_id):
                raise _refuse("original creation native identity changed")
            conn.execute(
                "UPDATE workspace_embodiments SET native_session_id=? WHERE key_digest=?",
                (session.acp_session_id, scope.key_digest),
            )
        else:
            handoffs = json.loads(row["creation_handoffs_json"] or "{}")
            matches = [item for item in handoffs.values() if item["successor_id"] == session.session_id]
            if len(matches) != 1 or matches[0].get("native_session_id") not in (None, session.acp_session_id):
                raise _refuse("native observation lost its original successor")
            matches[0]["native_session_id"] = session.acp_session_id
            conn.execute(
                "UPDATE workspace_embodiments SET creation_handoffs_json=? WHERE key_digest=?",
                (json.dumps(handoffs, sort_keys=True), scope.key_digest),
            )


async def continue_session(
    manager: Any, session: Any, method: Callable[..., Awaitable[Any]],
    session_id: str, args: tuple[Any, ...], kwargs: dict[str, Any],
    *, requirement: WorktreeWriterRequirement | None = None,
) -> tuple[bool, Any]:
    """Recover one creation's writer through strict native resume and lineage."""
    digest = (session.target.venue or {}).get(JOURNAL_KEY) if session else None
    if digest is None:
        if requirement is not None:
            raise _refuse("required native continuation has no original keyed creation writer")
        rows = manager._db.execute_read(
            "SELECT 1 FROM workspace_embodiments WHERE session_id=? AND creation_writer_json IS NOT NULL",
            (session_id,),
        )
        if rows:
            raise _refuse("native continuation lost its keyed creation pointer")
        return False, None
    original = _read(manager._db, digest)
    if original is None:
        raise _refuse("native continuation lost its keyed creation journal")
    if requirement is not None and _binding(original)[0].worktree_writer != requirement:
        raise _refuse("native continuation changed its required creation writer")
    if original["session_id"] == session.session_id:
        native = original["native_session_id"]
    else:
        try:
            handoffs = json.loads(original["creation_handoffs_json"] or "{}")
            if not isinstance(handoffs, dict):
                raise ValueError("invalid handoffs")
            matches = [
                value for value in handoffs.values()
                if value["successor_id"] == session.session_id
            ]
            if len(matches) != 1:
                raise ValueError("unretained successor")
            native = matches[0].get("native_session_id")
        except (ValueError, TypeError, KeyError) as exc:
            raise _refuse("native continuation changed its original creation lineage") from exc
    if requirement is not None and not native:
        raise _refuse("required native continuation has no original native identity")
    if native is not None and native != session.acp_session_id:
        raise _refuse("native continuation changed its original creation identity")
    async with retained(manager._db, original):
        writers.require_target(session.target)
        if method.__name__ == "resume_session":
            kwargs["allow_recreate"] = False
        return True, await method(manager, session_id, *args, **kwargs)


def handoff_state(
    db: Any, scope: CreationWriterScope, predecessor: str, *, create: dict | None = None,
    stage: str | None = None, native_session_id: str | None = None,
) -> dict | None:
    with db._runtime_transaction(write=create is not None or stage is not None, full_sync=True) as conn:
        row = conn.execute(
            "SELECT * FROM workspace_embodiments WHERE key_digest=?", (scope.key_digest,),
        ).fetchone()
        if row is None:
            raise _refuse("handoff lost its original keyed creation")
        require_journal(dict(row))
        handoffs = json.loads(row["creation_handoffs_json"] or "{}")
        result = handoffs.get(predecessor)
        if create is not None:
            if result is not None:
                raise _refuse("original successor must be reconciled")
            result = handoffs[predecessor] = {**create, "stage": "prepared"}
        if stage is not None:
            if result is None:
                raise _refuse("original handoff is unavailable")
            if native_session_id is not None:
                if result.get("native_session_id", native_session_id) != native_session_id:
                    raise _refuse("original successor native identity changed")
                result["native_session_id"] = native_session_id
            result["stage"] = stage
        if create is not None or stage is not None:
            conn.execute(
                "UPDATE workspace_embodiments SET creation_handoffs_json=? WHERE key_digest=?",
                (json.dumps(handoffs, sort_keys=True), scope.key_digest),
            )
        return result
