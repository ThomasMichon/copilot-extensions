#!/usr/bin/env python3
"""Require a pending changefile (``tools/changefile.py``) for every plugin a
PR touches -- the changefile-based replacement for
``check-version-bump.py``'s manual three-file version bump, once a PR
targets ``dev`` under the dev-branch-release-pipeline design
(ThomasMichon/copilot-extensions#3336).

Reuses ``check-version-bump.py``'s plugin-diff detection (which plugin(s) a
diff touches, including the shared-``libs/<lib>``-fans-out-to-every-consumer
rule) via a direct file load -- its filename has a hyphen, so it can't be a
normal ``import`` -- rather than duplicating that logic.

**Not yet wired into CI.** This tool exists ahead of the actual `dev` cutover
so it's built, tested, and ready; nothing currently requires a changefile
for any PR. See the dev-branch-release-pipeline effort's Plan for the
remaining cutover steps (CI wiring, CONTRIBUTING.md/AGENTS.md going live,
branch protection -- all deliberately gated on Phase 3's promotion pipeline
existing, not shipped here).

Usage::

    python tools/check-changefile-presence.py                 # diff vs origin/main
    python tools/check-changefile-presence.py --base <sha>     # diff vs an explicit base
"""
from __future__ import annotations

import argparse
import importlib.util
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))
from changefile import read_changefiles


def _load_check_version_bump():
    path = REPO / "tools" / "check-version-bump.py"
    spec = importlib.util.spec_from_file_location("check_version_bump_shared", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def touched_plugins(base_ref: str, head_ref: str = "HEAD") -> set[str]:
    """Every plugin the ``base_ref..head_ref`` diff touches, per
    check-version-bump.py's existing, tested plugin-diff rule."""
    cvb = _load_check_version_bump()
    head = cvb._rev_parse(head_ref)
    if head is None:
        return set()
    base = cvb._rev_parse(base_ref)
    if base is None:
        return set()
    mbase = cvb._merge_base(base, head) or base
    changed = cvb._changed_files(mbase, head)
    if not changed:
        return set()
    consumers = cvb._vendored_consumers()
    return set(cvb._plugins_needing_bump(changed, consumers))


def plugins_with_pending_changefiles() -> set[str]:
    plugins: set[str] = set()
    for _path, data in read_changefiles():
        for change in data.get("changes", []):
            plugins.add(change["plugin"])
    return plugins


def check(base_ref: str, head_ref: str = "HEAD") -> tuple[int, list[str]]:
    plugins = touched_plugins(base_ref, head_ref)
    if not plugins:
        return 0, []
    pending = plugins_with_pending_changefiles()
    missing = sorted(plugins - pending)
    return (1 if missing else 0), missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main",
                    help="base ref to diff against (default: origin/main)")
    ap.add_argument("--head", default="HEAD", help="head ref (default: HEAD)")
    args = ap.parse_args(argv)

    code, missing = check(args.base, args.head)
    if missing:
        print("check-changefile-presence: FAILED", file=sys.stderr)
        for plugin in missing:
            print(f"  - {plugin}: content changed but no pending changefile names it",
                  file=sys.stderr)
        print(
            "\nRun `python tools/changefile.py add --plugin <name> --type "
            "<major|minor|patch|dev> --comment \"...\"` for each plugin you touched.",
            file=sys.stderr,
        )
        return 1
    print("check-changefile-presence: OK (every touched plugin has a pending changefile).")
    return code


if __name__ == "__main__":
    raise SystemExit(main())
