"""Optional trusted workspace binding for tracked comment/review consumers."""

from __future__ import annotations

import argparse
import json
from functools import partial
from pathlib import Path
from typing import Protocol

from . import config as cfg, output, pr_ops, tracking
from .config import Config
from .pr_nudge_ops import _provider_mismatch_error

PROTOCOL = "agent-worktrees.selected-publication/v1"


class Publication(Protocol):
    protocol: str
    phase: str

    def execute(
        self, *, worktree_id: str, target: dict | None,
        command: str, body: str, event: str,
    ) -> dict: ...


def dispatch(argv: list[str], publication: Publication) -> int:
    from . import __main__ as core, pr_state_cli

    argv, project = core._extract_project_flag(argv)
    handlers = {
        "pr-comment": pr_state_cli.cmd_pr_comment, "pr-review": pr_state_cli.cmd_pr_review,
    }
    if not argv or argv[0] not in handlers:
        output.err("Selected publication applies only to pr-comment or pr-review.")
        return 1
    if publication.phase != "recover":
        try:
            if not core.front_door_cli.resolve_invocation_project(
                argv, project, core._resolve_active_project, core._cwd_is_inside_project,
                core._is_no_project_invocation,
            ):
                raise ValueError("selected publication requires its resolved invocation project")
        except (OSError, ValueError, RuntimeError) as exc:
            return output._json_error(str(exc)) if "--json" in argv else (output.err(str(exc)) or 1)
    command = argv[0]
    commands = {
        **(getattr(core, "COMMAND_MAP", None) or {}),
        command: partial(handlers[command], publication=publication),
    }
    return core._dispatch_lazy(command, argv, commands=commands)


def publish(
    worktree_id: str, *, publication: Publication, command: str,
    body: str, event: str = "", config: Config | None = None,
) -> dict:
    """Never reconcile, acquire credentials or fall back to a legacy write."""
    base = {"worktree_id": worktree_id}
    try:
        if (
            publication.protocol != PROTOCOL
            or publication.phase not in ("preview", "submit", "recover")
        ):
            raise ValueError("selected publication binding unsupported")
        target = None
        if publication.phase != "recover":
            record = pr_ops._load_record_or_none(worktree_id)
            if record is None:
                raise ValueError("selected publication requires a tracked PR")
            config = cfg.load_config() if config is None else config
            active = record.active_pr()
            mismatch = _provider_mismatch_error(active, config.default_repo.pr)
            if mismatch:
                raise ValueError(mismatch)
            if active is None or active.number is None or tracking._pr_is_terminal(active):
                raise ValueError("selected publication requires an open tracked PR")
            target = {
                "provider": active.provider or config.default_repo.pr.provider,
                "api_base": config.default_repo.pr.api_base or "",
                "repo": active.repo or record.repo or "",
                "number": active.number,
            }
        return {**base, **publication.execute(
            worktree_id=worktree_id, target=target, command=command, body=body, event=event,
        )}
    except Exception as exc:
        return {
            **base, "error": str(exc),
            "publication": {"phase": "unresolved", "effect_completion": "not_inferred"},
        }


def run(args: argparse.Namespace, publication: Publication, *, command: str) -> int:
    """Embedding seam using the existing parser; no new CLI syntax or owner loader."""
    from . import pr_reviewer_ops

    try:
        if not args.worktree_id:
            raise ValueError("selected publication requires the explicit original worktree ID")
        config = None
        if publication.phase != "recover":
            config = cfg.load_config(Path(args.config) if args.config else None)
        if command == "pr-comment":
            result = pr_reviewer_ops.pr_comment(
                args.worktree_id, args.body, config=config, publication=publication,
            )
        else:
            result = pr_reviewer_ops.pr_review(
                args.worktree_id, event=args.event, body=args.body,
                config=config, publication=publication,
            )
    except Exception as exc:
        result = {
            "error": str(exc),
            "publication": {"phase": "unresolved", "effect_completion": "not_inferred"},
        }
    if getattr(args, "json", False):
        output._json_output(result)
    else:
        print(json.dumps(result, sort_keys=True, allow_nan=False))
    return 1 if "error" in result else 0
