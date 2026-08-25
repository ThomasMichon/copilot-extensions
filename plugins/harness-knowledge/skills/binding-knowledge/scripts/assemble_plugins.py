#!/usr/bin/env python3
"""Compatibility entrypoint for the agent-worktrees knowledge-plugin composer.

The implementation and public ownership boundary moved to
``agent-worktrees knowledge compose-plugins``.  The binding skill keeps this
thin delegate so existing setup invocations continue to work without carrying
a second composition implementation.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys


class KnowledgePluginError(RuntimeError):
    """The public agent-worktrees composer could not produce a summary."""


def _require_type(
    summary: dict,
    field: str,
    expected_type: type,
    *,
    action: str,
):
    value = summary.get(field)
    if not isinstance(value, expected_type) or (
        expected_type is int and isinstance(value, bool)
    ):
        raise KnowledgePluginError(
            "agent-worktrees knowledge compose-plugins returned an invalid "
            f"{action!r} summary: {field!r} must be "
            f"{expected_type.__name__}"
        )
    return value


def _require_string_list(summary: dict, field: str, *, action: str) -> list[str]:
    value = _require_type(summary, field, list, action=action)
    if not all(isinstance(item, str) for item in value):
        raise KnowledgePluginError(
            "agent-worktrees knowledge compose-plugins returned an invalid "
            f"{action!r} summary: {field!r} must contain only strings"
        )
    return value


def _require_conflict_lists(summary: dict, field: str, *, action: str) -> None:
    value = _require_type(summary, field, dict, action=action)
    for nested in ("marketplaces", "enabled_plugins"):
        _require_string_list(value, nested, action=action)


def _validate_summary(summary: dict) -> dict:
    action = summary.get("action")
    if not isinstance(action, str) or action not in {
        "composed",
        "retired",
        "no-op",
    }:
        raise KnowledgePluginError(
            "agent-worktrees knowledge compose-plugins returned an invalid "
            f"summary action: {action!r}"
        )

    _require_type(summary, "paired", bool, action=action)
    _require_type(summary, "changed", bool, action=action)
    if action == "composed":
        for field in ("settings_local", "harness_path", "knowledge_path"):
            _require_type(summary, field, str, action=action)
        _require_string_list(summary, "marketplaces", action=action)
        _require_string_list(summary, "enabled_plugins", action=action)
        count = _require_type(summary, "count", int, action=action)
        if count < 0:
            raise KnowledgePluginError(
                "agent-worktrees knowledge compose-plugins returned an invalid "
                "'composed' summary: 'count' must be non-negative"
            )
        _require_conflict_lists(summary, "conflicts", action=action)
    elif action == "retired":
        _require_type(summary, "retired", bool, action=action)
        for field in ("settings_local", "harness_path", "pair_error"):
            _require_type(summary, field, str, action=action)
        _require_conflict_lists(summary, "retired_entries", action=action)
        _require_conflict_lists(summary, "preserved_modified", action=action)
        _require_type(summary, "file_removed", bool, action=action)
    else:
        _require_type(summary, "retired", bool, action=action)
        _require_type(summary, "pair_error", str, action=action)
        for field in ("settings_local", "harness_path"):
            if field in summary:
                _require_type(summary, field, str, action=action)
    return summary


def _resolve_command() -> str:
    command = shutil.which("agent-worktrees")
    if command is None:
        raise KnowledgePluginError(
            "agent-worktrees executable was not found on PATH; install or "
            "enable the agent-worktrees plugin"
        )
    return command


def _delegate(arguments: list[str]) -> dict:
    command = [
        _resolve_command(),
        "knowledge",
        "compose-plugins",
        *arguments,
        "--json",
    ]
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise KnowledgePluginError(
            f"could not execute {command[0]!r}: {exc}"
        ) from exc

    if result.returncode != 0:
        detail = result.stderr.strip()
        try:
            error_summary = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            error_summary = None
        if isinstance(error_summary, dict) and error_summary.get("error"):
            detail = str(error_summary["error"])
        elif not detail:
            detail = result.stdout.strip() or "no error details"
        raise KnowledgePluginError(
            "agent-worktrees knowledge compose-plugins exited with status "
            f"{result.returncode}: {detail}"
        )

    try:
        summary = json.loads(result.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise KnowledgePluginError(
            "agent-worktrees knowledge compose-plugins returned invalid JSON"
        ) from exc
    if not isinstance(summary, dict):
        raise KnowledgePluginError(
            "agent-worktrees knowledge compose-plugins returned a JSON "
            "value that is not an object"
        )
    if summary.get("action") == "error":
        raise KnowledgePluginError(
            str(summary.get("error") or "composer reported an unspecified error")
        )
    return _validate_summary(summary)


def assemble(
    harness_path: os.PathLike[str] | str,
    knowledge_path: os.PathLike[str] | str,
) -> dict:
    """Compose plugins for an explicit harness/knowledge checkout pair."""
    return _delegate(
        [
            "--harness-path",
            os.fspath(harness_path),
            "--knowledge-path",
            os.fspath(knowledge_path),
        ]
    )


def assemble_from_pair() -> dict:
    """Compose plugins using the pair containing the current directory."""
    return _delegate([])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="assemble_plugins",
        description=(
            "Compatibility wrapper for 'agent-worktrees knowledge "
            "compose-plugins'."
        ),
    )
    parser.add_argument("--harness-path")
    parser.add_argument("--knowledge-path")
    parser.add_argument("--from-pair", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    try:
        if args.from_pair:
            summary = assemble_from_pair()
        else:
            if not args.harness_path or not args.knowledge_path:
                parser.error(
                    "--harness-path and --knowledge-path are required unless "
                    "--from-pair is given"
                )
            summary = assemble(args.harness_path, args.knowledge_path)
        summary = _validate_summary(summary)
    except KnowledgePluginError as exc:
        if args.json:
            print(json.dumps({"paired": False, "error": str(exc)}, indent=2))
        else:
            print(f"Knowledge plugin overlay not composed: {exc}", file=sys.stderr)
        return 3

    if args.json:
        print(json.dumps(summary, indent=2))
    else:
        outcome = summary["action"]
        if outcome == "composed":
            action = "Updated" if summary["changed"] else "Verified"
            print(f"{action} knowledge plugin overlay: {summary['settings_local']}")
            print(f"  knowledge: {summary['knowledge_path']}")
            if summary["marketplaces"]:
                print(f"  marketplaces: {', '.join(summary['marketplaces'])}")
            if summary["enabled_plugins"]:
                print(f"  enabled: {', '.join(summary['enabled_plugins'])}")
            conflicts = summary["conflicts"]
            if conflicts["marketplaces"] or conflicts["enabled_plugins"]:
                print(
                    "  preserved conflicting harness/unmanaged settings: "
                    f"{len(conflicts['marketplaces'])} marketplace(s), "
                    f"{len(conflicts['enabled_plugins'])} plugin enable(s)",
                    file=sys.stderr,
                )
        elif outcome == "retired":
            print(
                "Retired stale knowledge plugin overlay: "
                f"{summary['settings_local']}"
            )
            print(f"  pair error: {summary['pair_error']}")
        else:
            print(f"Knowledge plugin preflight: no-op ({summary['pair_error']})")
        print("Canonical command: agent-worktrees knowledge compose-plugins")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
