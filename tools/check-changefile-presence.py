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

**Wired into CI** (`ci.yml`'s `guards + lint` job, `PR-into-dev only`) since
the dev-branch-release-pipeline cutover; the paragraph below describing it
as not-yet-wired is historical and predates that cutover.

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


def _resolve_mbase(cvb, base_ref: str, head_ref: str) -> tuple[str, str] | None:
    """The ``(merge_base, head_sha)`` pair for ``base_ref``/``head_ref``, or
    ``None`` when either ref is unresolvable OR they share no common
    ancestor at all.

    The latter is a real, confirmed condition for this repo: ``main`` is a
    wholesale-regenerated promotion artifact (see
    ``tools/promote_release.py``'s own docstring), so its commit graph has
    always been disjoint from ``dev``'s except for the repo's original fork
    point -- and a deliberate `main` history rewrite (docs/pipelines.md's
    "If main's history is force-rewritten") changes every commit's SHA on
    `main`'s own line, severing even that shared ancestor. Callers must
    treat ``None`` as "nothing determinable here," never silently fall back
    to diffing raw ``base_ref`` directly -- that previously produced a
    large, misleading "changed" set spanning everything that differs
    between `main`'s last promotion snapshot and the current branch, not
    this branch's own actual changes (confirmed live immediately after the
    2026-10-04 rewrite)."""
    head = cvb._rev_parse(head_ref)
    if head is None:
        return None
    base = cvb._rev_parse(base_ref)
    if base is None:
        return None
    mbase = cvb._merge_base(base, head)
    if mbase is None:
        print(
            f"check-changefile-presence: base '{base_ref}' shares no common "
            "history with HEAD (e.g. after a main history rewrite); skipping.",
            file=sys.stderr,
        )
        return None
    return mbase, head


def touched_plugins(base_ref: str, head_ref: str = "HEAD") -> set[str]:
    """Every plugin the ``base_ref..head_ref`` diff touches, per
    check-version-bump.py's existing, tested plugin-diff rule."""
    cvb = _load_check_version_bump()
    resolved = _resolve_mbase(cvb, base_ref, head_ref)
    if resolved is None:
        return set()
    mbase, head = resolved
    changed = cvb._changed_files(mbase, head)
    if not changed:
        return set()
    consumers = cvb._vendored_consumers()
    return set(cvb._plugins_needing_bump(changed, consumers))


def added_changefile_names(base_ref: str, head_ref: str = "HEAD") -> set[str]:
    """Basenames of ``.changefiles/*.json`` files THIS diff (``base_ref..
    head_ref``) itself adds.

    Deliberately narrower than "every changefile currently sitting in the
    repo": `dev` is a rolling pre-release branch, so unrelated already-merged
    PRs routinely leave their own still-pending changefiles in place while
    they wait for the next promotion. A PR whose own changefile omits a
    plugin it touches can still pass if that plugin happens to already have
    an unrelated pending changefile from a DIFFERENT PR -- coincidental
    coverage, not this PR's own. If that unrelated changefile is consumed by
    a promotion before this PR merges, the plugin ships this PR's content
    with no bump at all: exactly the silent stale-deploy failure this whole
    guard exists to prevent (dotfiles #1025), just one level removed
    (PR #4942 review).

    Restricted to TOP-LEVEL ``.changefiles/*.json`` paths -- the only shape
    ``changefile.read_changefiles()`` itself ever reads (it globs
    ``.changefiles/*.json`` non-recursively). Reducing to a bare basename
    without that restriction let an added nested path (e.g.
    ``.changefiles/archive/pending.json``) coincidentally match an existing,
    unrelated TOP-LEVEL file of the same name (``.changefiles/pending.json``)
    that ``read_changefiles()`` would actually consume -- passing a PR whose
    diff never added a changefile ``read_changefiles()`` can see at all
    (PR #4954 review)."""
    cvb = _load_check_version_bump()
    resolved = _resolve_mbase(cvb, base_ref, head_ref)
    if resolved is None:
        return set()
    mbase, head = resolved
    r = cvb._git("diff", "--name-only", "--diff-filter=A", f"{mbase}..{head}",
                 "--", ".changefiles")
    names = set()
    for ln in r.stdout.splitlines():
        ln = ln.strip()
        if not ln:
            continue
        p = Path(ln)
        if p.parent.as_posix() == ".changefiles" and p.suffix == ".json":
            names.add(p.name)
    return names


def plugins_with_pending_changefiles(base_ref: str, head_ref: str = "HEAD") -> set[str]:
    """Plugins named by a changefile THIS PR's own diff adds -- see
    :func:`added_changefile_names` for why this is scoped to the diff rather
    than every changefile currently pending in the repo."""
    added = added_changefile_names(base_ref, head_ref)
    plugins: set[str] = set()
    for path, data in read_changefiles():
        if path.name not in added:
            continue
        for change in data.get("changes", []):
            plugins.add(change["plugin"])
    return plugins


def check(base_ref: str, head_ref: str = "HEAD") -> tuple[int, list[str]]:
    plugins = touched_plugins(base_ref, head_ref)
    if not plugins:
        return 0, []
    pending = plugins_with_pending_changefiles(base_ref, head_ref)
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
