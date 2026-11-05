#!/usr/bin/env python3
"""Reconcile privately configured label names without publishing their values."""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import urllib.parse

POLICY_ENV = "COPILOT_EXTENSIONS_FORBIDDEN_LABELS"


def forbidden_names(raw: str) -> set[str]:
    if not raw.strip():
        return set()
    try:
        values = json.loads(raw)
    except ValueError:
        raise ValueError("Invalid private label policy.") from None
    if not isinstance(values, list) or any(
        not isinstance(value, str) or not value.strip()
        or any(ord(char) < 32 for char in value)
        for value in values
    ):
        raise ValueError("Invalid private label policy.")
    return {value.casefold() for value in values}


def _gh(args: list[str]) -> str:
    env = os.environ.copy()
    env.pop(POLICY_ENV, None)
    env.pop("GH_DEBUG", None)
    env["GH_PROMPT_DISABLED"] = "1"
    result = subprocess.run(
        ["gh", "api", "--hostname", "github.com", *args],
        capture_output=True, text=True, check=False, timeout=60, env=env,
    )
    if result.returncode:
        raise RuntimeError("GitHub label API request failed.")
    return result.stdout


def label_plan(repo: str, forbidden: set[str]) -> list[str]:
    try:
        pages = json.loads(_gh([
            "--paginate", "--slurp", f"repos/{repo}/labels?per_page=100",
        ]))
    except ValueError:
        raise ValueError("Invalid label inventory.") from None
    if not isinstance(pages, list) or not pages or any(
        not isinstance(page, list) for page in pages
    ):
        raise ValueError("Invalid label inventory.")
    names: dict[str, str] = {}
    for page in pages:
        for label in page:
            name = label.get("name") if isinstance(label, dict) else None
            if not isinstance(name, str) or not name or name.casefold() in names:
                raise ValueError("Invalid label inventory.")
            names[name.casefold()] = name
    return sorted(names[key] for key in names.keys() & forbidden)


def sweep(repo: str, raw_policy: str, *, execute: bool = False) -> dict:
    forbidden = forbidden_names(raw_policy)
    result = {
        "configured": bool(raw_policy.strip()),
        "forbidden_count": len(forbidden),
        "selected_count": 0,
        "deleted_count": 0,
        "execute": execute,
    }
    if not forbidden:
        return result
    if (
        not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo or "")
        or any(part in {".", ".."} for part in repo.split("/"))
    ):
        raise ValueError("An explicit owner/repository is required.")
    if execute and not (os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN")):
        raise ValueError("An explicit GitHub token is required for execution.")
    selected = label_plan(repo, forbidden)
    result["selected_count"] = len(selected)
    if execute:
        for name in selected:
            encoded = urllib.parse.quote(name, safe="")
            _gh(["--method", "DELETE", f"repos/{repo}/labels/{encoded}"])
            result["deleted_count"] += 1
    return result


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY", ""))
    parser.add_argument("--execute", action="store_true")
    args = parser.parse_args(argv)
    try:
        result = sweep(args.repo, os.environ.get(POLICY_ENV, ""), execute=args.execute)
    except (OSError, ValueError, RuntimeError, subprocess.TimeoutExpired):
        print("Forbidden-label sweep failed; no policy values are logged.", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
