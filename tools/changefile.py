#!/usr/bin/env python3
"""Changefiles: capture a plugin's version-bump intent at PR time without
picking an exact version number (CONTRIBUTING.md's manual three-file bump is
what this tool is meant to eventually replace -- see the dev-branch-release-
pipeline effort, ThomasMichon/copilot-extensions#3336).

A changefile is a small JSON file under ``.changefiles/`` naming which
plugin(s) a PR touches and how big the change is (``major`` / ``minor`` /
``patch`` / ``dev``), plus a human comment. Contributors add one per PR
instead of hand-editing three version fields; ``tools/accumulate_bumps.py``
later consumes every pending changefile and computes the real version.

Usage::

    python tools/changefile.py add --plugin agent-worktrees --type patch --comment "Fix X"
    python tools/changefile.py add --plugin agent-worktrees --type patch --plugin agent-bridge --type dev --comment "Shared fix"
    python tools/changefile.py list
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import time
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
CHANGEFILES_DIR = REPO / ".changefiles"
VALID_TYPES = ("major", "minor", "patch", "dev")
_SLUG_RE = re.compile(r"[^a-z0-9]+")


def _slugify(text: str) -> str:
    slug = _SLUG_RE.sub("-", text.lower()).strip("-")
    return slug[:40] or "change"


def write_changefile(changes: list[dict], comment: str) -> Path:
    CHANGEFILES_DIR.mkdir(parents=True, exist_ok=True)
    stamp = time.strftime("%Y%m%d%H%M%S")
    name = f"{stamp}-{_slugify(comment)}-{uuid.uuid4().hex[:6]}.json"
    path = CHANGEFILES_DIR / name
    payload = {"comment": comment, "changes": changes}
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    return path


def read_changefiles() -> list[tuple[Path, dict]]:
    if not CHANGEFILES_DIR.is_dir():
        return []
    out = []
    for f in sorted(CHANGEFILES_DIR.glob("*.json")):
        try:
            out.append((f, json.loads(f.read_text(encoding="utf-8"))))
        except (json.JSONDecodeError, OSError) as exc:
            print(f"changefile: skipping unreadable {f}: {exc}", file=sys.stderr)
    return out


def cmd_add(args: argparse.Namespace) -> int:
    if len(args.plugin) != len(args.type):
        print("changefile add: --plugin and --type must repeat in matching pairs",
              file=sys.stderr)
        return 1
    if not args.plugin:
        print("changefile add: at least one --plugin/--type pair is required",
              file=sys.stderr)
        return 1
    for t in args.type:
        if t not in VALID_TYPES:
            print(f"changefile add: invalid --type '{t}' (want one of {VALID_TYPES})",
                  file=sys.stderr)
            return 1
    changes = [{"plugin": p, "type": t} for p, t in zip(args.plugin, args.type, strict=True)]
    path = write_changefile(changes, args.comment)
    try:
        display = path.relative_to(REPO)
    except ValueError:
        display = path
    print(f"Wrote {display}")
    return 0


def cmd_list(_args: argparse.Namespace) -> int:
    pending = read_changefiles()
    if not pending:
        print("changefile: no pending changefiles.")
        return 0
    for path, data in pending:
        changes = ", ".join(f"{c['plugin']}={c['type']}" for c in data.get("changes", []))
        print(f"{path.name}: {changes} -- {data.get('comment', '')}")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="command", required=True)

    add = sub.add_parser("add", help="write a new changefile")
    add.add_argument("--plugin", action="append", default=[], help="plugin name (repeatable)")
    add.add_argument("--type", action="append", default=[],
                      help="major|minor|patch|dev, paired positionally with --plugin")
    add.add_argument("--comment", required=True, help="human-readable summary")
    add.set_defaults(func=cmd_add)

    lst = sub.add_parser("list", help="list pending changefiles")
    lst.set_defaults(func=cmd_list)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
