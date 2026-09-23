#!/usr/bin/env python3
"""The local dev-slot override: let an impatient agent/operator preview an
unreleased ``dev``-branch change live, by overwriting their own installed
Copilot plugin payload with a locally built preview (``tools/preview_release.py``),
instead of waiting for the real promotion cycle + ``agent-worktrees update``.

Safety model (per the dev-branch-release-pipeline effort's design):

* **Never silent.** Every install writes a claim file
  (``.dev-slot-claim.json``) inside the installed plugin directory recording
  who installed it, when, from which source commit, and that a real update
  supersedes it.
* **Never irreversible.** The real installed payload is backed up alongside
  itself (``<plugin>.dev-slot-backup/``) before being overwritten; ``clean``
  restores it.
* **The installing agent owns cleanup.** ``clean`` is the explicit, required
  teardown step -- this tool does not attempt to auto-expire a slot itself
  (no background process to do so); the operator/agent that installed it is
  responsible for running ``clean`` when done, exactly as the operator asked.

Usage::

    python tools/dev_slot.py install agent-worktrees --preview-dir /tmp/preview/agent-worktrees --yes
    python tools/dev_slot.py status agent-worktrees
    python tools/dev_slot.py clean agent-worktrees
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path

CLAIM_FILE = ".dev-slot-claim.json"
BACKUP_SUFFIX = ".dev-slot-backup"


def _default_target_root() -> Path:
    return Path.home() / ".copilot" / "installed-plugins" / "copilot-extensions"


def _target_dir(plugin: str, target_root: Path) -> Path:
    return target_root / plugin


def _backup_dir(target: Path) -> Path:
    return target.with_name(target.name + BACKUP_SUFFIX)


def install(plugin: str, preview_dir: Path, target_root: Path, *, claimant: str) -> Path:
    if not preview_dir.is_dir():
        raise FileNotFoundError(f"no such preview directory: {preview_dir}")
    target = _target_dir(plugin, target_root)
    backup = _backup_dir(target)

    if backup.exists():
        raise RuntimeError(
            f"a dev-slot backup already exists at {backup} -- run 'clean {plugin}' first "
            "(never overwrite a backup, or the real payload underneath it is lost)"
        )

    if target.exists():
        shutil.move(str(target), str(backup))

    shutil.copytree(preview_dir, target)

    manifest_path = target / "PREVIEW.json"
    source_commit = "unknown"
    if manifest_path.exists():
        try:
            source_commit = json.loads(manifest_path.read_text()).get("source_commit", "unknown")
        except (json.JSONDecodeError, OSError):
            pass

    claim = {
        "plugin": plugin,
        "claimant": claimant,
        "source_commit": source_commit,
        "installed_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "note": (
            "Local preview override. The next real 'agent-worktrees update' (or "
            "'copilot plugin update') supersedes this automatically. The "
            "installing agent/operator is responsible for running "
            f"'python tools/dev_slot.py clean {plugin}' when done previewing."
        ),
    }
    (target / CLAIM_FILE).write_text(json.dumps(claim, indent=2) + "\n", encoding="utf-8")
    return target


def status(plugin: str, target_root: Path) -> dict | None:
    target = _target_dir(plugin, target_root)
    claim_path = target / CLAIM_FILE
    if not claim_path.exists():
        return None
    try:
        return json.loads(claim_path.read_text())
    except (json.JSONDecodeError, OSError):
        return {"plugin": plugin, "note": "claim file present but unreadable"}


def clean(plugin: str, target_root: Path) -> bool:
    """Restore the backed-up real payload (if any) and remove the claim.

    Returns True if a dev-slot override was actually found and cleaned."""
    target = _target_dir(plugin, target_root)
    backup = _backup_dir(target)
    claim_path = target / CLAIM_FILE
    if not claim_path.exists() and not backup.exists():
        return False
    if target.exists():
        shutil.rmtree(target)
    if backup.exists():
        shutil.move(str(backup), str(target))
    return True


def cmd_install(args: argparse.Namespace) -> int:
    if not args.yes:
        print(
            "dev-slot install: refusing without --yes -- this overwrites your real "
            f"installed '{args.plugin}' plugin payload (backed up, but not silent).",
            file=sys.stderr,
        )
        return 1
    try:
        target = install(args.plugin, args.preview_dir, args.target_root, claimant=args.claimant)
    except (FileNotFoundError, RuntimeError) as exc:
        print(f"dev-slot install: {exc}", file=sys.stderr)
        return 1
    print(f"Installed dev-slot preview for '{args.plugin}' at {target}")
    print(f"Remember to run: python tools/dev_slot.py clean {args.plugin}")
    return 0


def cmd_status(args: argparse.Namespace) -> int:
    claim = status(args.plugin, args.target_root)
    if claim is None:
        print(f"{args.plugin}: no active dev-slot override.")
        return 0
    print(json.dumps(claim, indent=2))
    return 0


def cmd_clean(args: argparse.Namespace) -> int:
    cleaned = clean(args.plugin, args.target_root)
    if cleaned:
        print(f"{args.plugin}: dev-slot override removed; real payload restored (if backed up).")
    else:
        print(f"{args.plugin}: no active dev-slot override to clean.")
    return 0


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--target-root", type=Path, default=_default_target_root(),
                     help="installed-plugins root (default: ~/.copilot/installed-plugins/copilot-extensions)")
    sub = ap.add_subparsers(dest="command", required=True)

    inst = sub.add_parser("install", help="overwrite a local installed plugin with a preview")
    inst.add_argument("plugin")
    inst.add_argument("--preview-dir", type=Path, required=True)
    inst.add_argument("--claimant", default="local-agent",
                       help="who/what installed this (for the claim file)")
    inst.add_argument("--yes", action="store_true", help="required to actually proceed")
    inst.set_defaults(func=cmd_install)

    stat = sub.add_parser("status", help="show the active claim for a plugin, if any")
    stat.add_argument("plugin")
    stat.set_defaults(func=cmd_status)

    cln = sub.add_parser("clean", help="restore the real payload and remove the claim")
    cln.add_argument("plugin")
    cln.set_defaults(func=cmd_clean)

    args = ap.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
