"""One bounded producer request consumed by the existing native launch owner."""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from pathlib import Path

from plugin_resolve.preparation import _git
from plugin_resolve.registration import atomic_json

from .acquisition_contract import AcquisitionRefused
from .plugin_readiness import PluginReadinessError, prepared_readiness
from .acquisition_contract import DEFAULT_CLI_WORKER_TARGET
from .models import StartSessionRequest
from .transport import SpawnTarget


async def prepare_target(runtime, req, row, *, target_factory, bound_charter, apply_preferences):
    if req.mode == "cli" and req.target == DEFAULT_CLI_WORKER_TARGET:
        # The worktree owner, not an ACP profile, owns CLI launch defaults.
        target = target_factory(
            type="local", cwd=row["path"], worktree_id=req.worktree_id,
            explicit_cwd=True, project=req.project or row.get("repo"),
        )
        return await runtime._local_preparation_target(req, row, target), {}
    try:
        target = await runtime.resolver.resolve_async(req.target)
    except KeyError as exc:
        raise AcquisitionRefused(
            "target_unavailable", f"Acquisition target '{req.target}' is not registered",
        ) from exc
    if target.type != "local":
        raise AcquisitionRefused("venue_unsupported", "this acquisition adapter is local only")
    bound = (row.get("bound_agent") or "").strip()
    if bound and bound != req.target:
        from types import SimpleNamespace

        target = bound_charter(
            target,
            runtime.resolver,
            SimpleNamespace(bound_agent=bound),
            req.worktree_id,
        )
    request_env, _source = apply_preferences(
        StartSessionRequest(model=req.model, effort=req.effort),
        runtime.state,
        target,
    )
    return await runtime._local_preparation_target(req, row, target), request_env


async def local_preparation_target(req, row, target: SpawnTarget, *, replace_target):
    from .venue_plugins import with_controller_origin

    # Existing structured exclusions remain authoritative. Only an ordinary
    # unstructured local target needs the provenance introduced here.
    ordinary = not any((
        target.elevated, target.spawn_command, target.host, target.user,
        target.codespace, target.container, target.remote_acp,
        target.execution_space_key, target.remote_acp_capabilities,
    ))
    if target.static_plugin_args is None and ordinary:
        target = replace_target(
            target, static_plugin_args={"base": list(target.copilot_args), "automatic": []},
            local_plugin_preparation=True,
        )
    target = replace_target(
        target, project=target.project or req.project or row.get("repo"),
        caller_worktree=req.caller_worktree if req.caller_worktree is not None else target.caller_worktree,
        caller_owner_ref=req.caller_owner_ref if req.caller_owner_ref is not None else target.caller_owner_ref,
    )
    if target.local_plugin_preparation:
        from .session_manager import _REQUEST_OVERRIDES_KEY

        provenance = (target.venue or {}).get(_REQUEST_OVERRIDES_KEY, {})
        origin = "explicit" if req.caller_worktree is not None else provenance.get(
            "controller_worktree_origin",
            "explicit" if target.caller_worktree is not None else "affinity",
        )
        target = with_controller_origin(
            target, origin,
            caller_session_id=req.caller_session_id,
        )
        if req.mode == "cli":
            import os
            from .venue_plugins import local_plugin_request

            checkout = Path(row["path"]).resolve()
            if any((parent / ".git").exists() for parent in (checkout, *checkout.parents)):
                target = replace_target(
                    target,
                    plugin_preparation_request=await local_plugin_request(
                        target, row["path"], dict(os.environ),
                    ),
                )
    return target


def prepared_context(runtime, target, row, native_session_id, recovery_context, *, require_prepared):
    from .plugin_readiness import PluginReadinessError, prepared_readiness

    context = target.prepared_plugins
    if context is None:
        context = recovery_context
    if context is None and target.worktree_id:
        context = runtime.store.prepared_context(target.worktree_id, native_session_id, "acp")
    checkout = Path(row["path"]).resolve()
    if context is None:
        if require_prepared and target.local_plugin_preparation and any(
            (parent / ".git").exists() for parent in (checkout, *checkout.parents)
        ):
            raise AcquisitionRefused(
                "plugins_not_prepared", "the ACP process has no verified prepared selection",
            )
        return None
    try:
        prepared_readiness(context, cwd=row["path"], session_id=native_session_id)
    except PluginReadinessError as exc:
        raise AcquisitionRefused("plugins_not_prepared", str(exc)) from exc
    return context


async def cli_native(runtime, req, driver, decision, *, verify_worktree, clock, sleep):
    deadline = clock.monotonic() + (req.verify_timeout or 180)
    while True:
        expected = (
            decision.conversation.native_session_id
            if decision and decision.conversation
            else None
        )
        rows = [
            row
            for row in runtime.db.list_fresh_live_sessions(req.worktree_id, now=clock.time())
            if (row.get("driven_by") == driver or (
                decision and decision.retirement_recovered and decision.acquired == "reused"
            ))
            and (expected is None or row["session_id"] == expected)
        ]
        if len(rows) == 1:
            native = rows[0]["session_id"]
            _, head = await verify_worktree(req)
            if head != native:
                if clock.monotonic() >= deadline:
                    raise AcquisitionRefused(
                        "native_head_pending", "CLI registration has not become the native worktree head",
                    )
                await sleep(0.2)
                continue
            if (
                decision
                and decision.conversation
                and native != decision.conversation.native_session_id
            ):
                raise AcquisitionRefused(
                    "native_identity_changed", "CLI registered another native conversation"
                )
            reservation = runtime.db.get_cli_mode_reservation(req.worktree_id)
            if reservation and reservation.get("claimed_by_session_id") == native:
                runtime.db.release_cli_mode_reservation(
                    req.worktree_id,
                    reservation_id=reservation["reservation_id"],
                )
            return native, native
        if len(rows) > 1:
            raise AcquisitionRefused(
                "multiple_live_holders", "CLI launch registered multiple holders"
            )
        if clock.monotonic() >= deadline:
            raise AcquisitionRefused(
                "native_registration_pending", "CLI native registration remains unproven"
            )
        await sleep(0.2)


async def launch_prepared(runtime, req, row, target, args, owner_command, *, reservation_id: str):
    repo = Path(row["path"]).resolve(strict=True)
    git_dir = Path(_git(repo, "rev-parse", "--absolute-git-dir")).resolve(strict=True)
    root = git_dir / "copilot-venue-plugins" / "launch-requests" / uuid.uuid4().hex
    root.mkdir(parents=True, mode=0o700)
    path = root / "request.json"
    atomic_json(path, {
        "version": 1, "worktree_id": req.worktree_id, "cwd": str(repo),
        "plan": target.plugin_preparation_request, "deadline": time.time() + 240,
    })

    async def operation():
        failure = None
        try:
            result = await owner_command(
                [*args, "--require-plugin-preparation", "--bridge-plugin-request", str(path)], req.project,
            )
            if result.get("worktree_id") != req.worktree_id or Path(result["work_dir"]).resolve() != repo:
                raise AcquisitionRefused("owner_response_invalid", "plugin preparation checkout changed")
            return result
        except AcquisitionRefused as exc:
            failure = exc
            raise
        finally:
            prepared = path.with_suffix(".prepared.json")
            if prepared.is_file():
                context = json.loads(prepared.read_text(encoding="utf-8"))
                try:
                    prepared_readiness(context, cwd=str(repo), session_id=None)
                except PluginReadinessError as exc:
                    raise AcquisitionRefused("plugins_not_prepared", str(exc)) from exc
                _token, stage, data = runtime.store.recovery(req)
                runtime.store.stage(req, stage, **{**data, "prepared_context": context})
            if failure is not None and failure.code == "plugin_preparation_failed":
                token, _stage, data = runtime.store.recovery(req)
                runtime.store.stage(req, "prepared", **data)
                runtime.store.journal.refuse(req, token, failure.code)
                runtime.store.release_gate(req)
                runtime.db.release_cli_mode_reservation(
                    req.worktree_id, reservation_id=reservation_id,
                )

    task = asyncio.create_task(operation())
    try:
        try:
            result = await asyncio.shield(task)
        except asyncio.CancelledError:
            path.with_suffix(".cancel").touch()
            while not task.done():
                try:
                    await asyncio.shield(task)
                except asyncio.CancelledError:
                    continue
                except AcquisitionRefused:
                    break
            try:
                task.result()
            except AcquisitionRefused:
                pass
            raise
        if result.get("prepared_context") is None or not path.with_suffix(".prepared.json").is_file():
            raise AcquisitionRefused("plugins_not_prepared", "native owner returned no compiled selection")
        return result
    finally:
        for item in root.iterdir():
            item.unlink()
        root.rmdir()
