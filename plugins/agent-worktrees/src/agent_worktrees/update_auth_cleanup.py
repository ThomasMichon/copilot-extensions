"""Consume the declared Git policy owner's preview, apply and rollback boundary."""

from __future__ import annotations

import json
import os
import re
from collections.abc import Callable
from pathlib import Path

from . import update_auth as auth
from .update_auth_process import run

_SHA = re.compile(r"[a-f0-9]{64}\Z")


class PolicyUnavailable(RuntimeError):
    pass


def _command(arguments: list[str]) -> tuple[list[str], dict[str, str]]:
    from . import peer_launch_adapter, registry_paths
    from ._peer_launch import launch_prefix, legacy_peer_environment
    from .claim_providers import _resolve_command, _windows_batch_argv

    try:
        if os.environ.get("COPILOT_EXTENSIONS_CONTEXT", ""):
            own = peer_launch_adapter.validate_context()
            if own is None:
                raise ValueError("explicit context unavailable")
            return [
                *launch_prefix(
                    "agent-worktrees", Path(own["pluginRoot"]),
                    str(own["installReceipt"]), "agent-machines",
                ),
                *arguments,
            ], dict(os.environ)
        root = registry_paths._payload_root().resolve()
        peer = (root.parent / "agent-machines").resolve(strict=True)
        if peer.parent != root.parent:
            raise ValueError("peer escaped selected marketplace")
        manifest = json.loads((peer / "plugin.json").read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or manifest.get("name") != "agent-machines":
            raise ValueError("peer ownership unavailable")
        command = _resolve_command(
            ("agent-machines", *arguments), root=peer,
        )
        env = legacy_peer_environment(dict(os.environ))
        env["AGENT_MACHINES_NO_SELFPROVISION"] = "1"
        return _windows_batch_argv(command), env
    except (OSError, ValueError, RuntimeError) as error:
        raise PolicyUnavailable("policy_owner_unavailable") from error


def _invoke(arguments: list[str], cwd: Path) -> dict:
    from .git_ops import repository_identity_env

    argv, env = _command(arguments)
    result = run(argv, env=repository_identity_env(env), cwd=cwd, timeout=30)
    if result.timed_out or result.missing or result.truncated or result.status is None:
        raise PolicyUnavailable("policy_owner_unconfirmed")
    try:
        payload = json.loads(result.stdout)
    except ValueError as error:
        raise PolicyUnavailable("policy_owner_invalid_response") from error
    if not isinstance(payload, dict):
        raise PolicyUnavailable("policy_owner_invalid_response")
    if result.status:
        # Retain mutation/recovery evidence, not the owner's arbitrary diagnostic output.
        payload["_failed"] = True
    return payload


def _resource(payload: dict, *, dry_run: bool) -> dict:
    items = payload.get("resources")
    if (
        payload.get("surfaces") != [] or payload.get("modules") != []
        or not isinstance(items, list) or len(items) != 1
        or not isinstance(items[0], dict) or items[0].get("type") != "git-config"
        or items[0].get("dry_run") is not dry_run
    ):
        raise PolicyUnavailable("policy_owner_invalid_scope")
    return items[0]


def offer(cwd: Path, *, include_values: bool = False) -> dict:
    try:
        payload = _invoke(["git-config", "restore", "--json"], cwd)
        if payload.get("resources") == [] and payload.get("ok") is True:
            return {"status": "not_configured"}
        item = _resource(payload, dry_run=True)
        plan = item.get("change_plan")
        if (
            payload.get("_failed") or payload.get("ok") is not True
            or not isinstance(plan, dict)
            or plan.get("schema") != "agent-machines.git-config-plan/v1"
            or not _SHA.fullmatch(str(plan.get("plan_sha256", "")))
        ):
            raise PolicyUnavailable("policy_preview_unavailable")
        if not include_values:
            plan = {
                **plan,
                "diff": (
                    f"- sha256:{plan.get('before_sha256', 'unavailable')}\n"
                    f"+ sha256:{plan.get('after_sha256', 'unavailable')}\n"
                ),
            }
        return {
            "status": "offered" if item.get("changed") else "current",
            "owner": "agent-machines", "plan": plan,
            "manual_steps": [],
        }
    except PolicyUnavailable as error:
        return {"status": "unavailable", "reason": str(error)}


def rollback(cwd: Path, receipt: str, digest: str | None = None) -> dict:
    if digest is not None and not _SHA.fullmatch(digest):
        return {"status": "blocked", "changed": False}
    arguments = ["git-config", "rollback", "--receipt", receipt, "--json"]
    if digest is not None:
        arguments += ["--apply", "--plan-sha", digest]
    try:
        payload = _invoke(arguments, cwd)
    except PolicyUnavailable as error:
        return {
            "status": "unconfirmed" if digest is not None else "unavailable",
            "changed": None if digest is not None else False, "reason": str(error),
            "backup_path": receipt,
        }
    if payload.get("_failed"):
        return {
            "status": "rollback_failed", "changed": payload.get("changed"),
            "backup_path": payload.get("backup_path") or receipt,
        }
    if payload.get("dry_run") is not (digest is None):
        return {"status": "unconfirmed", "changed": None, "backup_path": receipt}
    return {
        "status": "rollback_preview" if digest is None else "reverted",
        "changed": payload.get("changed"),
        "plan": payload.get("plan"), "backup_path": payload.get("backup_path") or receipt,
    }


def apply(
    cwd: Path, digest: str, before: auth.AuthReport,
    recheck: Callable[[], auth.AuthReport],
) -> dict:
    if not _SHA.fullmatch(digest):
        return {"status": "blocked", "changed": False}
    try:
        payload = _invoke([
            "git-config", "restore", "--json", "--apply", "--git-config-plan-sha", digest,
        ], cwd)
        item = _resource(payload, dry_run=False)
    except PolicyUnavailable as error:
        return {"status": "unconfirmed", "changed": None, "reason": str(error)}
    receipt = item.get("backup_path")
    result = {
        "status": "applied", "changed": item.get("changed"), "backup_path": receipt,
    }
    if payload.get("_failed") or payload.get("ok") is not True:
        result["status"] = "apply_failed"
        return result
    try:
        after = recheck()
        healthy = {item.source.id for item in before.results if item.status in {"ok", "anonymous"}}
        now = {item.source.id for item in after.results if item.status in {"ok", "anonymous"}}
        fallback = {
            item.source.id for item in before.results if item.status == "fixable_by_fallback"
        }
        available = now | {
            item.source.id for item in after.results if item.status == "fixable_by_fallback"
        }
        result["report"] = after.summary()
        regressed = not healthy <= now or not fallback <= available
    except Exception:
        regressed = True
    if not regressed:
        return result
    if not isinstance(receipt, str) or not receipt:
        result["status"] = "verification_failed"
        return result
    preview = rollback(cwd, receipt)
    plan = preview.get("plan")
    undo_digest = plan.get("plan_sha256") if isinstance(plan, dict) else None
    if preview["status"] != "rollback_preview" or not isinstance(undo_digest, str):
        result["status"] = "rollback_failed"
        return result
    undone = rollback(cwd, receipt, undo_digest)
    result["rollback"] = undone
    result["status"] = (
        "auth_cleanup_reverted" if undone["status"] == "reverted" else "rollback_failed"
    )
    if undone["status"] == "reverted":
        try:
            result["report"] = recheck().summary()
        except Exception:
            result["status"] = "verification_failed"
    return result
