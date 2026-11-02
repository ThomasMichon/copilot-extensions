"""One-shot repository PR census and selected-cell claim observation."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys
import unicodedata

import yaml

from . import config as cfg, pr_sweep, pr_sweep_transport as transport
from .providers.base import ProviderError


def _print_human(message: str, *, file=None) -> None:
    safe = "".join(
        character.encode("unicode_escape").decode("ascii")
        if unicodedata.category(character) in ("Cc", "Cf", "Cs", "Zl", "Zp")
        else character
        for character in message
    )
    print(safe, file=file)


def emit_human(result: dict) -> None:
    summary = result["summary"]
    count = summary["open_count"]
    _print_human(f"{result['repo']}: {count if count is not None else 'unknown'} open PRs"
          f" ({'complete' if result['complete'] else 'partial'} observation)")
    provider = result["provider"]
    _print_human(f"Provider: {provider['status']}; checked {provider['checked_utc']}")
    if provider.get("error"):
        _print_human("  " + provider["error"])
    if summary["author_filter"]:
        _print_human(f"Author filter: {summary['author_filter']}; "
              f"all-author census: {summary['all_author_open_count']}")
        if not summary["author_filter_complete"]:
            _print_human(f"Author attribution is incomplete; known matches: {summary['known_matching_open_count']}")
    oldest = summary["oldest"]
    if oldest:
        _print_human(f"Oldest: {oldest['url']} created {oldest['created_at']}")
    for cell in result["cells"]:
        errors = (cell.get("observation") or {}).get("errors", [])
        _print_human(f"Cell {cell['cell']}: {cell['status']}; checked {cell['checked_utc']}"
              + (f"; {len(errors)} unresolved reads/edges" if errors else ""))
        if cell.get("error"):
            _print_human("  " + cell["error"])
    author = summary["author_filter"]
    for pr in result["prs"] or []:
        if author and (pr["author"] or "").casefold() != author.casefold():
            continue
        _print_human(f"{pr['url']} {(pr['head_sha'] or 'unknown head')[:12]} {pr['title'] or '(title unavailable)'}")
        if not pr["claimants"]:
            _print_human("  No observed graph claimant; unavailable cells remain unknown.")
        for claim in pr["claimants"]:
            _print_human(f"  {claim['cell']} {claim['project']}/{claim['worktree_id']}"
                  f" [{claim['evidence']}; {claim['checked_utc']}]"
                  f" {' -> '.join(claim['path'])}")
        for candidate in pr["candidates"]:
            _print_human(f"  Candidate only: {candidate['cell']} "
                  f"{candidate['project']}/{candidate['worktree_id']}; not a held-claim proof.")
    retained = [claim for claim in result["associations"] if claim["provider_state"] != "open"]
    for claim in retained:
        label = "Retained claim" if claim["evidence"] == "unsettled-transitive-claim" else "Candidate only"
        _print_human(f"{label} {claim['url']}: {claim['cell']} "
              f"{claim['project']}/{claim['worktree_id']} [{claim['provider_state']}]")


def _command_pairs(pairs: list[tuple[str, object]]) -> dict:
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate cell command key: {key}")
        result[key] = value
    return result


def _repository(value: str) -> str:
    parts = value.split("/")
    if (
        len(parts) != 2 or any(not part or part.strip() != part or part in (".", "..") for part in parts)
        or any(character in value for character in "\0\r\n?#\\")
    ):
        raise argparse.ArgumentTypeError("Use an exact owner/repository target")
    return value


def _run_pr_sweep(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-worktrees pr-sweep",
        description="Read open PRs for a registered repository and their original worktree claimants.",
    )
    parser.add_argument("repo", type=_repository)
    parser.add_argument("--fleet", action="store_true", help="Describe all configured machine/environment cells")
    parser.add_argument("--include", action="append", default=[], metavar="MACHINE/ENV")
    parser.add_argument("--exclude", action="append", default=[], metavar="MACHINE/ENV",
                        help="Exclude before any cell execution; retain visible coverage")
    parser.add_argument("--commands", type=Path,
                        help="JSON object mapping remote cells to their absolute catalog commands")
    parser.add_argument("--author", help="Filter summary/presentation, not the all-author provider census")
    parser.add_argument("--timeout", type=float, default=120.0, help="Per-remote-cell owned-process deadline, seconds")
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--list-cells", action="store_true",
                        help="Describe configured selections and stop; no forge or cell observation")
    parser.add_argument("--collect-only", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    if not math.isfinite(args.timeout) or args.timeout <= 0:
        parser.error("--timeout must be finite and positive")
    if args.collect_only and (args.fleet or args.commands or args.include or args.exclude or args.author or args.list_cells):
        parser.error("--collect-only cannot compose another sweep or provider filter")
    try:
        config = cfg.load_config()
        if args.collect_only:
            print(json.dumps(pr_sweep.collect_local(args.repo, config), ensure_ascii=True))
            return 0
        cells = transport.targets(config, args.fleet)
        selected = transport.select_targets(cells, args.include, args.exclude)
        commands = transport.validate_command_map(
            json.loads(args.commands.read_text(encoding="utf-8-sig"), object_pairs_hook=_command_pairs)
            if args.commands else {},
            cells,
        )
    except (OSError, ValueError, RuntimeError, TypeError, KeyError, yaml.YAMLError) as error:
        if args.json:
            print(json.dumps({"schema": pr_sweep.SCHEMA, "version": 1, "error": str(error)}))
        else:
            _print_human(f"pr-sweep: {error}", file=sys.stderr)
        return 2
    if args.list_cells:
        if args.json:
            print(json.dumps({
                "schema": pr_sweep.SCHEMA, "version": 1, "operation": "list-cells",
                "checked_utc": pr_sweep.utc_now(), "cells": selected,
            }, ensure_ascii=True))
        else:
            for cell in selected:
                _print_human(f"{cell['cell']}: {'selected' if cell['selected'] else 'excluded'}; "
                      f"{'local' if cell['local'] else 'configured remote'}; not probed")
        return 0
    try:
        provider = pr_sweep.provider_inventory(args.repo, config)
    except (ProviderError, OSError, ValueError, RuntimeError) as error:
        provider = {
            "status": "failed", "complete": False, "checked_utc": pr_sweep.utc_now(),
            "error": str(error), "prs": None,
        }
    observations = []
    for cell in selected:
        result = {"cell": cell["cell"], "checked_utc": pr_sweep.utc_now()}
        if not cell["selected"]:
            result.update(status="excluded", error="Not selected; no cell command executed")
        elif not cell["available"]:
            result.update(status="unavailable", error="Configured cell has no available SSH route")
        elif cell["local"]:
            try:
                result.update(status="ok", observation=pr_sweep.collect_local(args.repo, config))
            except (OSError, ValueError, RuntimeError, TypeError, KeyError, yaml.YAMLError) as error:
                result.update(status="failed", error=str(error))
        elif cell["cell"] not in commands:
            result.update(status="unsupported", error="No attributable cell catalog command supplied")
        else:
            result = transport.observe_remote(
                cell, commands[cell["cell"]]["command"], project=commands[cell["cell"]]["project"],
                repo=args.repo, timeout=args.timeout,
            )
        observations.append(result)
    result = pr_sweep.join_inventory(args.repo, provider, observations, args.author)
    if args.json:
        print(json.dumps(result, ensure_ascii=True))
    else:
        emit_human(result)
    return 0 if result["complete"] else 1


def run_pr_sweep(argv: list[str]) -> int:
    try:
        return _run_pr_sweep(argv)
    except KeyboardInterrupt:
        print("pr-sweep: cancelled", file=sys.stderr)
        return 130
