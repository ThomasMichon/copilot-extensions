#!/usr/bin/env python3
"""Require a version bump whenever a plugin's content changes (docs/pipelines.md
§ Release & Versioning).

The marketplace only redeploys a plugin's runtime when its declared version
**advances**: `<repo> update` refreshes the payload but the versioned-runtime
install is version-gated, so new code shipped under an unchanged version silently
serves stale (dotfiles #1025). The consistency guard
(`check-version-consistency.py`) proves a plugin's version is *identical* across
its files, but it is silent when the version doesn't move at all. This guard
closes that hole: **touch a plugin's content -> bump its version.**

What requires a bump, for a push/PR diff (`<base>..HEAD`):

* **Any file under `plugins/<p>/`** (its `src/`, `skills/`, `agents/`, its own
  `docs/`, tests, manifests -- everything ships or informs downstream agents) =>
  `<p>`'s `plugin.json` `version` must differ from the base.
* **Any file under a top-level, shared `libs/<lib>/`** (the canonical source that
  is *vendored* into plugins) => **every plugin that vendors `<lib>`** must bump.
  A shared-lib change reaches every consumer, so each consumer's payload changes
  (see `check-vendored-libs-sync.py`, dotfiles #929).

What does **not** require a bump: repo-root files that are not vendored into any
plugin -- `tools/`, `.github/`, the repo-root `docs/`, `CONTRIBUTING.md`,
`README.md`, etc. (docs/pipelines.md § Version scheme). Also exempt, even under
`plugins/<p>/`: build/venv/cache artifacts (`build/`, `dist/`, `.venv*/`,
`.test-venvs/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `*.pyc`) and
dev-hygiene files that never ship (`.gitignore`) -- none change the runtime
payload.

Scope is the push/PR diff only (like `check-no-internal-identifiers.py`), so a
pre-existing un-bumped state in an untouched plugin never blocks an unrelated
push. Newly added or deleted plugins are skipped (no before/after version to
compare).

Usage::

    python tools/check-version-bump.py                 # diff vs origin/main (pre-push/CI)
    python tools/check-version-bump.py --base <sha>     # diff vs an explicit base
    python tools/check-version-bump.py --list           # show the plugin<->vendored-lib map

Exit code 0 = conformant (or nothing to check), 1 = a touched plugin didn't bump.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer_engine_ref as ier  # noqa: E402
import uv_editable_ref as uer  # noqa: E402

try:  # tomllib is stdlib on 3.11+; tomli backports it for this repo's
    # 3.10 support floor -- mirrors uv_editable_ref's own identical fallback.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"

# Build/artifact noise under a plugin dir that never counts as "content".
_IGNORE_PARTS = {
    "build", "dist", ".venv", ".venv-test", ".venv-tools", ".test-venvs",
    ".testvenv", "__pycache__", ".pytest_cache", ".ruff_cache",
}
_IGNORE_SUFFIX = {".pyc", ".pyo"}
# Dev-hygiene files that never ship as plugin runtime, so a change to one must
# not force a version bump / redeploy (e.g. a per-plugin .gitignore).
_IGNORE_NAMES = {".gitignore"}

_PLUGIN_JSON_VERSION = re.compile(r'"version"\s*:\s*"([^"]+)"')


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, text=True, check=False,
    )


def _rev_parse(ref: str) -> str | None:
    r = _git("rev-parse", "--verify", "--quiet", ref)
    out = r.stdout.strip()
    return out or None


def _merge_base(base: str, head: str) -> str | None:
    r = _git("merge-base", base, head)
    return r.stdout.strip() or None


def _changed_files(base: str, head: str) -> list[str]:
    r = _git("diff", "--name-only", f"{base}..{head}")
    return [ln.strip() for ln in r.stdout.splitlines() if ln.strip()]


def _plugin_json_version_at(ref: str, plugin: str) -> str | None:
    """The ``version`` field of ``plugins/<plugin>/plugin.json`` at ``ref``.

    ``None`` when the file does not exist at that ref (a plugin added or removed
    across the range) or carries no version -- callers skip those."""
    r = _git("show", f"{ref}:plugins/{plugin}/plugin.json")
    if r.returncode != 0:
        return None
    m = _PLUGIN_JSON_VERSION.search(r.stdout)
    return m.group(1) if m else None


def _pyproject_project_version_at(ref: str, rel_pyproject: str) -> str | None:
    """The ``[project].version`` field of ``rel_pyproject`` (a repo-relative
    path) at ``ref`` -- for a top-level, out-of-plugin consumer (e.g.
    ``worktree-manager``) that has no ``plugin.json`` at all and instead
    carries its own release version directly in its ``pyproject.toml``.
    ``None`` on a missing file, unparseable TOML, or a manifest with no
    ``[project].version`` -- same "skip, don't crash" contract as
    ``_plugin_json_version_at``."""
    r = _git("show", f"{ref}:{rel_pyproject}")
    if r.returncode != 0:
        return None
    try:
        data = tomllib.loads(r.stdout)
    except tomllib.TOMLDecodeError:
        return None
    version = data.get("project", {}).get("version")
    return version if isinstance(version, str) else None


def _consumer_version_at(ref: str, consumer: str) -> str | None:
    """The release version for any consumer `_vendored_consumers()` can
    name -- a ``plugins/<consumer>`` plugin (``plugin.json``) or a
    top-level, out-of-plugin consumer tree like ``worktree-manager`` (its
    own ``pyproject.toml`` ``[project].version``, since it has no
    ``plugin.json`` at all)."""
    if (PLUGINS_DIR / consumer).is_dir():
        return _plugin_json_version_at(ref, consumer)
    return _pyproject_project_version_at(ref, f"{consumer}/pyproject.toml")


def _vendored_consumers() -> dict[str, list[str]]:
    """Map ``lib name -> [consumers that vendor it]`` -- both an ordinary
    in-tree ``<consumer>/libs/*`` real copy AND a `uv`-editable canonical-
    reference pointer in a consumer's own ``pyproject.toml`` (vendor-
    pointer-generalization effort, Phase 1: no local copy at all in a dev
    checkout). A consumer using only the pointer form still gets `<lib>`'s
    payload materialized into it at promotion time
    (`tools/materialize_main.py`), so it must be charged for a bump the
    same as a real-copy consumer -- skipping this half missed every
    editable-pointer consumer from the version-bump guard entirely (PR
    #4465 review).

    The real-copy half scans EVERY ``plugins/*`` directory directly
    (never ``uv_editable_ref.iter_consumer_dirs()``, which requires a
    root ``pyproject.toml`` and so silently drops a payload-only plugin
    like `customizing-copilot` that ships real vendored copies with no
    ``pyproject.toml`` of its own at all -- PR #4465 review). The
    editable-ref half legitimately needs `iter_consumer_dirs()` (plugins
    with a ``pyproject.toml`` + every top-level, out-of-plugin consumer
    tree, e.g. ``worktree-manager``) since a pointer can only ever live
    inside a real ``pyproject.toml``."""
    consumers: dict[str, list[str]] = {}
    if PLUGINS_DIR.is_dir():
        for plugin in sorted(p for p in PLUGINS_DIR.iterdir() if p.is_dir()):
            libs = plugin / "libs"
            if libs.is_dir():
                for lib in sorted(x for x in libs.iterdir() if x.is_dir()):
                    consumers.setdefault(lib.name, []).append(plugin.name)
    # Out-of-plugin consumer trees (e.g. `worktree-manager`) can carry
    # real vendored copies under their own top-level `libs/` too -- the
    # `plugins/*` scan above never reaches them, so a canonical lib
    # change would otherwise ship without charging them (mirrors
    # `check-vendored-libs-sync.py`'s own identical extra-consumer scan,
    # PR #4465 review).
    for extra in uer._EXTRA_CONSUMER_DIRS:
        extra_libs = REPO / extra / "libs"
        if extra_libs.is_dir():
            for lib in sorted(x for x in extra_libs.iterdir() if x.is_dir()):
                consumers.setdefault(lib.name, []).append(extra)
    # `iter_consumer_dirs()` filters candidates by `pyproject.toml.is_file()`,
    # which returns False for a symlink to a directory OR a dangling symlink
    # (broken target) -- both would silently vanish from its results before
    # ever reaching the symlink check below, contradicting the guarantee
    # that ANY symlinked manifest is rejected, not just one that also
    # happens to resolve to a real file (PR #4514 review). Scan every
    # candidate manifest path directly first, unfiltered.
    candidate_dirs = []
    if PLUGINS_DIR.is_dir():
        candidate_dirs.extend((p.name, p) for p in sorted(PLUGINS_DIR.iterdir()) if p.is_dir())
    candidate_dirs.extend(
        (extra, REPO / extra) for extra in uer._EXTRA_CONSUMER_DIRS if (REPO / extra).is_dir()
    )
    for name, consumer_dir in candidate_dirs:
        pyproject = consumer_dir / "pyproject.toml"
        if pyproject.is_symlink():
            raise SystemExit(
                f"check-version-bump: {pyproject} is a symlink -- cannot "
                f"safely determine {name}'s uv-editable consumers; "
                "replace it with a real file."
            )
    for name, consumer_dir in uer.iter_consumer_dirs():
        try:
            refs = uer.find_uv_editable_refs(consumer_dir)
        except uer.ManifestUnreadable as exc:
            # Fail closed, never silently drop this consumer from the
            # map -- an unreadable manifest could genuinely reference
            # `<lib>`, and skipping it would let a shared-lib change
            # ship without charging a real consumer (PR #4465 review).
            raise SystemExit(
                f"check-version-bump: {exc} -- cannot safely determine "
                f"{name}'s uv-editable consumers; fix its pyproject.toml "
                "[tool.uv.sources] table."
            ) from exc
        for _name, _raw_path, lib, _editable in refs:
            if name not in consumers.get(lib, ()):
                consumers.setdefault(lib, []).append(name)
    for lib in consumers:
        consumers[lib] = sorted(consumers[lib])
    installer_engine_consumers = [
        plugin for plugin in ier.ADOPTERS if (PLUGINS_DIR / plugin).is_dir()
    ]
    if installer_engine_consumers:
        consumers["installer-engine"] = sorted(
            set(consumers.get("installer-engine", ())) | set(installer_engine_consumers)
        )
    packaged_peers = [
        plugin for plugin in (
            "agent-bridge", "agent-dispatch", "agent-codespaces", "agent-containers",
            "agent-logger", "agent-index", "agent-machines",
        )
        if (PLUGINS_DIR / plugin).is_dir()
    ]
    if packaged_peers:
        consumers["peer-launch"] = packaged_peers
    return consumers


def _is_ignored(rel_parts: tuple[str, ...], name: str) -> bool:
    if _IGNORE_PARTS & set(rel_parts):
        return True
    if name in _IGNORE_NAMES:
        return True
    return any(name.endswith(s) for s in _IGNORE_SUFFIX)


def _plugins_needing_bump(changed: list[str], consumers: dict[str, list[str]]) -> dict[str, set[str]]:
    """Map ``plugin -> {reasons}`` for every plugin whose content changed.

    A path under ``plugins/<p>/`` charges ``<p>``; a path under a top-level
    shared ``libs/<lib>/`` charges every plugin that vendors ``<lib>``; a
    path under a recognized standalone consumer's own top-level tree (e.g.
    ``worktree-manager/``) charges that consumer directly -- without this,
    a direct payload change to a standalone consumer was invisible to both
    this guard and `check-changefile-presence.py`/`compute_from_diff()`,
    which both depend on this same map (PR #4514 review)."""
    needing: dict[str, set[str]] = {}
    for path in changed:
        parts = tuple(path.split("/"))
        if len(parts) < 2:
            continue
        if parts[0] == "plugins":
            plugin = parts[1]
            inner = parts[2:]
            if inner and _is_ignored(inner, parts[-1]):
                continue
            # Only real plugin dirs (with a manifest) count.
            if (PLUGINS_DIR / plugin / "plugin.json").exists():
                needing.setdefault(plugin, set()).add(f"plugins/{plugin}/")
        elif parts[0] == "libs" and len(parts) >= 2:
            lib = parts[1]
            inner = parts[2:]
            if inner and _is_ignored(inner, parts[-1]):
                continue
            for plugin in consumers.get(lib, ()):
                needing.setdefault(plugin, set()).add(f"libs/{lib}/ (vendored)")
        elif parts[0] in uer._EXTRA_CONSUMER_DIRS and (REPO / parts[0] / "pyproject.toml").exists():
            consumer = parts[0]
            inner = parts[1:]
            if inner and _is_ignored(inner, parts[-1]):
                continue
            needing.setdefault(consumer, set()).add(f"{consumer}/")
    return needing


def check(base_ref: str, head_ref: str) -> tuple[int, list[str]]:
    head = _rev_parse(head_ref)
    if head is None:
        # Nothing resolvable to check (e.g. an empty repo) -- never block.
        print(f"check-version-bump: cannot resolve HEAD ({head_ref}); skipping.")
        return 0, []
    base = _rev_parse(base_ref)
    if base is None:
        # The base (default origin/main) is unavailable -- a fresh clone or a
        # detached state. Degrade to a no-op rather than wedge the push.
        print(
            f"check-version-bump: base '{base_ref}' unavailable; skipping "
            "(fetch it to enable the guard).",
        )
        return 0, []
    mbase = _merge_base(base, head) or base

    changed = _changed_files(mbase, head)
    if not changed:
        print("check-version-bump: no changes vs base; nothing to check.")
        return 0, []

    consumers = _vendored_consumers()
    needing = _plugins_needing_bump(changed, consumers)
    if not needing:
        print("check-version-bump: no plugin content touched; nothing to bump.")
        return 0, []

    violations: list[str] = []
    for plugin in sorted(needing):
        head_ver = _consumer_version_at(head, plugin)
        base_ver = _consumer_version_at(mbase, plugin)
        if head_ver is None or base_ver is None:
            # Plugin added or removed across the range -- no bump obligation.
            continue
        if head_ver == base_ver:
            reasons = ", ".join(sorted(needing[plugin]))
            # This standalone tool only compares base/head versions -- it
            # never reads .changefiles, so a changefile alone does not clear
            # ITS OWN failure; check-changefile-presence.py is what actually
            # gates an ordinary PR today (see docs/pipelines.md § Release &
            # Versioning). Naming both keeps this diagnostic honest about
            # what each check requires without prescribing a fix the other
            # branch can't follow (plugin.json/marketplace.json don't exist
            # for a standalone consumer -- PR #4514 review).
            if (PLUGINS_DIR / plugin).is_dir():
                fix = (
                    "for ordinary PR compliance, add a changefile for it "
                    "(python tools/changefile.py add --plugin <name> --type patch "
                    "--comment '...'); to clear THIS check directly, bump "
                    "plugin.json + pyproject.toml + marketplace.json"
                )
            else:
                fix = (
                    "for ordinary PR compliance, add a changefile naming it "
                    "(python tools/changefile.py add --plugin <name> --type patch "
                    "--comment '...'); to clear THIS check directly, bump its own "
                    "pyproject.toml [project].version (+ source __version__ fallback)"
                )
            violations.append(f"{plugin}: content changed ({reasons}) but version is still {head_ver} -- {fix}.")
    return (1 if violations else 0), violations


def _print_list() -> None:
    consumers = _vendored_consumers()
    shared = {lib: plugs for lib, plugs in consumers.items() if len(plugs) >= 1}
    print("Vendored shared libs -> consuming plugins (a lib change bumps them all):")
    for lib in sorted(shared):
        top = "canonical" if (LIBS_DIR / lib).is_dir() else "no top-level source"
        print(f"  libs/{lib}  ({top}) -> {', '.join(shared[lib])}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--base", default="origin/main",
                    help="base ref to diff against (default: origin/main)")
    ap.add_argument("--head", default="HEAD", help="head ref (default: HEAD)")
    ap.add_argument("--list", action="store_true",
                    help="print the plugin<->vendored-lib map and exit")
    args = ap.parse_args(argv)

    if args.list:
        _print_list()
        return 0

    code, violations = check(args.base, args.head)
    if violations:
        print("check-version-bump: FAILED", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print(
            "\nThis standalone tool's own CI-enforcement role is retired in favor "
            "of tools/check-changefile-presence.py -- for ordinary PR compliance, "
            "a touched plugin needs a pending changefile, not a hand-applied "
            "version bump: python tools/changefile.py add --plugin <name> --type "
            "patch --comment '...' (see docs/pipelines.md § Release & "
            "Versioning). To clear THIS script's own exit code directly (e.g. "
            "release/recovery tooling), apply the actual version bump each "
            "violation above names instead. A shared `libs/<lib>` change needs "
            "one for every plugin that vendors it.",
            file=sys.stderr,
        )
        return 1
    return code


if __name__ == "__main__":
    raise SystemExit(main())
