#!/usr/bin/env python3
"""Require a pending changefile (``tools/changefile.py``) for every plugin a
PR touches -- the changefile-based replacement for
``check-version-bump.py``'s manual three-file version bump, once a PR
targets ``dev`` under the dev-branch-release-pipeline design
(ThomasMichon/copilot-extensions#3336).

Reuses ``check-version-bump.py``'s plugin-diff detection (which plugin(s) a
diff touches, including the shared-``libs/<lib>``-fans-out-to-every-consumer
rule) via a direct file load -- its filename has a hyphen, so it can't be a
normal ``import`` -- rather than duplicating that logic. New or changed library
bump intents also charge that map, even without a library source edit: promotion
changes the library manifest version, which ships inside every consumer.

**Wired into CI** (`ci.yml`'s `guards + lint` job, `PR-into-dev only`) since
the dev-branch-release-pipeline cutover; the paragraph below describing it
as not-yet-wired is historical and predates that cutover.

Usage::

    python tools/check-changefile-presence.py                 # diff vs origin/dev
    python tools/check-changefile-presence.py --base <sha>     # diff vs an explicit base
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

REPO = Path(__file__).resolve().parent.parent

sys.path.insert(0, str(Path(__file__).resolve().parent))
from changefile import VALID_TYPES


def _load_check_version_bump():
    path = REPO / "tools" / "check-version-bump.py"
    spec = importlib.util.spec_from_file_location("check_version_bump_shared", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def _resolve_mbase(cvb, base_ref: str, head_ref: str) -> tuple[str, str, str] | None:
    """The ``(merge_base, head_sha, target_base)`` for ``base_ref``/``head_ref``, or
    ``None`` when either ref is unresolvable OR they share no common
    ancestor at all.

    The latter is a real, standing condition for this repo: ``main`` is a
    wholesale-regenerated promotion artifact (see
    ``tools/promote_release.py``'s own docstring), so its commit graph has
    always been disjoint from ``dev``'s except for the repo's original fork
    point -- and a deliberate `main` history rewrite (docs/pipelines.md's
    "If main's history is force-rewritten") changes every commit's SHA on
    `main`'s own line, severing even that shared ancestor. Callers must
    treat ``None`` as "nothing determinable here," never silently fall back
    to diffing raw ``base_ref`` directly -- that would produce a large,
    misleading "changed" set spanning everything that differs between
    `main`'s last promotion snapshot and the current branch, not this
    branch's own actual changes."""
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
    return mbase, head, base


def touched_plugins(base_ref: str, head_ref: str = "HEAD") -> set[str]:
    """Every plugin the ``base_ref..head_ref`` diff touches, per
    check-version-bump.py's diff rule, using prospective PR integration inputs."""
    cvb = _load_check_version_bump()
    resolved = _resolve_mbase(cvb, base_ref, head_ref)
    if resolved is None:
        return set()
    mbase, head, target_base = resolved
    changed = cvb._changed_files(mbase, head)
    if not changed:
        return set()
    changed.extend(
        f"{library}/pyproject.toml"
        for library in _changed_library_intents(cvb, mbase, head, target_base)
    )
    tree = cvb._prospective_pr_tree(target_base, head, mbase)
    consumers = cvb._vendored_consumers(tree=tree)
    return set(cvb._plugins_needing_bump(changed, consumers, tree=tree))


def _changefile_intents(data: object, label: str) -> set[tuple[str, str]]:
    """Validate the existing changefile entry shape before comparing intent."""
    if not isinstance(data, dict) or not isinstance(data.get("changes"), list):
        raise RuntimeError(f"invalid changefile {label}: changes must be a list")
    intents = set()
    for change in data["changes"]:
        if (
            not isinstance(change, dict)
            or not isinstance(change.get("plugin"), str)
            or not change["plugin"].strip()
            or change.get("type") not in VALID_TYPES
        ):
            raise RuntimeError(f"invalid changefile {label}: expected plugin and valid bump type")
        intents.add((change["plugin"], change["type"]))
    return intents


def _read_changefile_intents(
    cvb, revision: str, relative: Path, *, baseline: bool = False,
) -> set[tuple[str, str]]:
    label = f"{'baseline changefile' if baseline else 'changefile'} {relative}"
    entry = cvb._git("ls-tree", "-z", "--full-tree", revision, "--", relative.as_posix())
    records = entry.stdout.split("\0")
    if entry.returncode or len(records) != 2 or records[-1]:
        raise RuntimeError(f"could not read {label}")
    metadata, separator, name = records[0].partition("\t")
    fields = metadata.split()
    if not separator or name != relative.as_posix() or len(fields) != 3:
        raise RuntimeError(f"invalid tree entry for {label}")
    mode, kind, oid = fields
    if mode == "120000":
        raise RuntimeError(f"symlinked {label}")
    if mode not in {"100644", "100755"} or kind != "blob":
        raise RuntimeError(f"not a regular {label}")
    blob = cvb._git("cat-file", "blob", oid)
    if blob.returncode:
        raise RuntimeError(f"could not read {label}")
    try:
        return _changefile_intents(json.loads(blob.stdout), label)
    except json.JSONDecodeError as error:
        raise RuntimeError(f"invalid {label}: {error}") from error


def _require_fresh_changefile(cvb: ModuleType, target_base: str, relative: Path) -> None:
    """Do not reuse identities promotion may have consumed, even after the PR fork."""
    history = cvb._git(
        "--literal-pathspecs", "log", "-1", "--format=%H", target_base, "--",
        relative.as_posix(),
    )
    if history.returncode:
        raise RuntimeError(f"could not verify fresh changefile identity for {relative}")
    if history.stdout.strip():
        raise RuntimeError(f"{relative}: release intent requires a fresh changefile filename")
    depth = cvb._git("rev-parse", "--is-shallow-repository")
    if depth.returncode or depth.stdout.strip() != "false":
        raise RuntimeError(f"{relative}: complete Git history is required to verify a fresh changefile")


def _changed_library_intents(cvb, base: str, head: str, target_base: str) -> set[str]:
    """Expand only added/changed bump entries, not unrelated baseline intents."""
    result = cvb._git(
        "diff", "--name-status", "-z", "--no-renames", "--diff-filter=AMT", f"{base}..{head}",
        "--", ".changefiles",
    )
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "could not read changed changefiles")
    selected = []
    fields = result.stdout.split("\0")
    if fields[-1] == "":
        fields.pop()
    if len(fields) % 2:
        raise RuntimeError("invalid changed-changefile path records")
    for index in range(0, len(fields), 2):
        status, name = fields[index:index + 2]
        path = Path(name)
        if path.parent.as_posix() == ".changefiles" and path.suffix == ".json":
            selected.append((status, path))
    if not selected:
        return set()
    libraries = set()
    for status, relative in selected:
        current = _read_changefile_intents(cvb, head, relative)
        previous: set[tuple[str, str]] = set()
        if status in {"M", "T"}:
            previous = _read_changefile_intents(cvb, base, relative, baseline=True)
        for plugin, _kind in current - previous:
            if plugin.startswith("libs/"):
                library = plugin[len("libs/"):]
                if not cvb.uer.is_safe_lib_name(library) or "\\" in library:
                    raise RuntimeError(f"invalid library changefile target {plugin!r}")
                _require_fresh_changefile(cvb, target_base, relative)
                libraries.add(plugin)
    return libraries


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
    mbase, head, _target_base = resolved
    r = cvb._git("diff", "--name-only", "-z", "--no-renames", "--diff-filter=A", f"{mbase}..{head}",
                 "--", ".changefiles")
    names = set()
    for ln in r.stdout.split("\0"):
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
    cvb = _load_check_version_bump()
    resolved = _resolve_mbase(cvb, base_ref, head_ref)
    if resolved is None:
        return set()
    _mbase, head, target_base = resolved
    added = added_changefile_names(target_base, head)
    plugins: set[str] = set()
    for name in sorted(added):
        relative = Path(".changefiles") / name
        intents = _read_changefile_intents(cvb, head, relative)
        if intents:
            _require_fresh_changefile(cvb, target_base, relative)
            plugins.update(plugin for plugin, _kind in intents)
    return plugins


def check(base_ref: str, head_ref: str = "HEAD") -> tuple[int, list[str]]:
    cvb = _load_check_version_bump()
    resolved = _resolve_mbase(cvb, base_ref, head_ref)
    if resolved is None:
        return 0, []
    _mbase, head, target_base = resolved
    plugins = touched_plugins(target_base, head)
    if not plugins:
        return 0, []
    pending = plugins_with_pending_changefiles(target_base, head)
    missing = sorted(plugins - pending)
    return (1 if missing else 0), missing


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/dev",
                    help="base ref to diff against (default: origin/dev -- "
                         "this repo's real contribution trunk; CI always "
                         "passes an explicit PR base instead)")
    ap.add_argument("--head", default="HEAD", help="head ref (default: HEAD)")
    args = ap.parse_args(argv)

    try:
        code, missing = check(args.base, args.head)
    except RuntimeError as error:
        print(f"check-changefile-presence: {error}", file=sys.stderr)
        return 1
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
