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
