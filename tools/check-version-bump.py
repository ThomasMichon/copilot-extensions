#!/usr/bin/env python3
"""Require a version bump whenever a plugin's content changes (docs/pipelines.md
§ Release & Versioning).

**Retired as the per-PR enforcement guard** in favor of
`check-changefile-presence.py` (which requires a pending changefile instead
of a hand-applied bump); this module now serves two roles: (1) the shared
diff engine `check-changefile-presence.py` and `accumulate_bumps.py` both
import for "which consumer(s) does this diff touch", and (2) a standalone
release/recovery check for directly verifying or enforcing an actual version
bump outside the ordinary changefile-driven PR flow (its own CLI below still
reports a real version-mismatch, it just no longer gates an ordinary PR).

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
  is *vendored* into plugins) => **every direct or transitive consumer** must bump.
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

    python tools/check-version-bump.py                 # diff vs origin/dev (this repo's real trunk)
    python tools/check-version-bump.py --base <sha>     # diff vs an explicit base (e.g. origin/main for a release/recovery check)
    python tools/check-version-bump.py --list           # show the plugin<->vendored-lib map

Exit code 0 = conformant (or nothing to check), 1 = a touched plugin didn't bump.
Revision and blob arguments use an explicit path separator so Git does not try
to stat long SHA expressions as Windows paths. A failed diff is an error, not
an empty change set.
"""
from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import installer_engine_ref as ier  # noqa: E402
import uv_editable_ref as uer  # noqa: E402
from vendored_lib_inventory import is_artifact_only  # noqa: E402

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
    result = subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True, check=False,
    )
    return subprocess.CompletedProcess(
        result.args, result.returncode, result.stdout.decode("utf-8"), result.stderr.decode("utf-8"),
    )


def _rev_parse(ref: str) -> str | None:
    r = _git("rev-parse", "--verify", "--quiet", ref)
    out = r.stdout.strip()
    return out or None


def _merge_base(base: str, head: str) -> str | None:
    r = _git("merge-base", base, head)
    return r.stdout.strip() or None


def _changed_files(base: str, head: str) -> list[str]:
    r = _git("diff", "--name-only", "-z", f"{base}..{head}", "--")
    if r.returncode != 0:
        raise RuntimeError(r.stderr.strip() or "git diff failed while reading changed files")
    return [path for path in r.stdout.split("\0") if path]


def _plugin_json_version_at(ref: str, plugin: str) -> str | None:
    """The ``version`` field of ``plugins/<plugin>/plugin.json`` at ``ref``.

    ``None`` when the file does not exist at that ref (a plugin added or removed
    across the range) or carries no version -- callers skip those."""
    r = _git("show", f"{ref}:plugins/{plugin}/plugin.json", "--")
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
    r = _git("show", f"{ref}:{rel_pyproject}", "--")
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
    version = _plugin_json_version_at(ref, consumer)
    if version is not None:
        return version
    return _pyproject_project_version_at(ref, f"{consumer}/pyproject.toml")


class _GitTree:
    """Read dependency inputs and modes without consulting a different checkout."""

    def __init__(self, revision: str):
        result = _git("ls-tree", "-r", "-z", "--full-tree", revision)
        if result.returncode:
            raise RuntimeError(result.stderr.strip() or "could not read dependency tree")
        self.entries: dict[str, tuple[str, str, str]] = {}
        self.blobs: dict[str, str] = {}
        for record in result.stdout.split("\0"):
            if not record:
                continue
            metadata, separator, name = record.partition("\t")
            fields = metadata.split()
            if not separator or len(fields) != 3:
                raise RuntimeError("invalid dependency tree entry")
            self.entries[name] = tuple(fields)

    def validate(self, relative: str) -> None:
        path = Path(relative)
        for part in (path, *path.parents):
            entry = self.entries.get(part.as_posix())
            if entry is not None and entry[0] == "120000":
                raise RuntimeError(f"symlinked dependency input {part}")
            if entry is not None and entry[1] != "blob":
                raise RuntimeError(f"unsupported dependency input {part}")

    def has_dir(self, relative: str) -> bool:
        self.validate(relative)
        return any(name.startswith(relative + "/") for name in self.entries)

    def has_file(self, relative: str) -> bool:
        self.validate(relative)
        return relative in self.entries

    def read(self, relative: str) -> str | None:
        self.validate(relative)
        entry = self.entries.get(relative)
        if entry is None:
            if self.has_dir(relative):
                raise RuntimeError(f"not a regular dependency input {relative}")
            return None
        mode, kind, oid = entry
        if mode not in {"100644", "100755"} or kind != "blob":
            raise RuntimeError(f"not a regular dependency input {relative}")
        if oid not in self.blobs:
            result = _git("cat-file", "blob", oid)
            if result.returncode:
                raise RuntimeError(f"could not read dependency input {relative}")
            self.blobs[oid] = result.stdout
        return self.blobs[oid]

    def toml(self, relative: str) -> dict:
        text = self.read(relative)
        try:
            return tomllib.loads(text) if text is not None else {}
        except tomllib.TOMLDecodeError as error:
            raise RuntimeError(f"invalid dependency input {relative}: {error}") from error


def _prospective_pr_tree(target_base: str, head: str, merge_base: str) -> _GitTree:
    """Compute PR integration inputs without changing the checkout or index."""
    if merge_base == target_base:
        return _GitTree(head)
    result = _git("merge-tree", "--write-tree", target_base, head)
    if result.returncode == 1:
        raise RuntimeError("target/head release inputs conflict; rebase before validating coverage")
    if result.returncode:
        raise RuntimeError(result.stderr.strip() or "could not compute target/head release inputs")
    lines = result.stdout.splitlines()
    tree = lines[0].strip() if lines else ""
    if re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", tree) is None:
        raise RuntimeError("invalid prospective PR tree")
    return _GitTree(tree)


def _canonical_dependencies(lib: str, *, tree: _GitTree | None = None) -> set[str]:
    """Read sibling library edges with the materializer's shared UV parser."""
    if not uer.is_safe_lib_name(lib):
        raise SystemExit(f"check-version-bump: unsafe canonical library name {lib!r}")
    directory = LIBS_DIR / lib
    pyproject = directory / "pyproject.toml"
    linked = uer._find_symlinked_ancestor(pyproject, REPO) if tree is None else None
    if linked is not None:
        raise SystemExit(
            f"check-version-bump: {linked} is a symlink -- cannot safely "
            f"determine {lib}'s nested consumers."
        )
    if tree is None and pyproject.exists() and not pyproject.is_file():
        raise SystemExit(f"check-version-bump: {pyproject} is not a manifest file")
    try:
        refs = uer.find_uv_editable_refs(directory) if tree is None else (
            uer.find_uv_editable_refs(
                directory, data=tree.toml(f"libs/{lib}/pyproject.toml"),
                resolve_symlinks=False,
            )
        )
    except uer.ManifestUnreadable as exc:
        raise SystemExit(
            f"check-version-bump: {exc} -- cannot safely determine "
            f"{lib}'s nested consumers; fix its pyproject.toml [tool.uv.sources] table."
        ) from exc
    dependencies: set[str] = set()
    resolve = Path.resolve if tree is None else lambda path: Path(os.path.abspath(path))
    for _name, raw_path, dependency, _editable in refs:
        # Promotion places each nested dependency at the same sibling location.
        # Never reinterpret an arbitrary escaping path by its basename alone.
        if (
            not uer.is_safe_lib_name(dependency)
            or Path(raw_path).is_absolute()
            or resolve(directory / raw_path) != resolve(LIBS_DIR / dependency)
        ):
            raise SystemExit(
                f"check-version-bump: {pyproject} references {raw_path!r}, "
                "not a canonical sibling library -- cannot determine nested consumers."
            )
        dependencies.add(dependency)
    return dependencies


def _transitive_consumers(
    direct: dict[str, list[str]], *, tree: _GitTree | None = None,
) -> dict[str, list[str]]:
    """Charge each seed's consumers for its complete, cycle-safe library closure."""
    consumers: dict[str, set[str]] = {}
    dependencies: dict[str, set[str]] = {}
    for seed, names in sorted(direct.items()):
        pending = [seed]
        visited: set[str] = set()
        while pending:
            lib = pending.pop()
            if lib in visited:
                continue
            visited.add(lib)
            consumers.setdefault(lib, set()).update(names)
            if lib not in dependencies:
                dependencies[lib] = _canonical_dependencies(lib, tree=tree)
            pending.extend(sorted(dependencies[lib], reverse=True))
    return {lib: sorted(names) for lib, names in sorted(consumers.items())}


def _implicit_consumer_seeds(consumers: dict[str, list[str]], plugins: set[str]) -> None:
    installer_engine_consumers = plugins & set(ier.ADOPTERS)
    if installer_engine_consumers:
        consumers["installer-engine"] = sorted(
            set(consumers.get("installer-engine", ())) | installer_engine_consumers
        )
    packaged_peers = plugins & {
        "agent-bridge", "agent-dispatch", "agent-codespaces", "agent-containers",
        "agent-logger", "agent-index", "agent-machines", "agent-worktrees",
        "agent-pull-requests",
    }
    if packaged_peers:
        consumers["peer-launch"] = sorted(packaged_peers)


def _snapshot_consumers(tree: _GitTree) -> dict[str, list[str]]:
    plugin_names = {
        name.split("/")[1] for name in tree.entries if name.startswith("plugins/")
    }
    roots = {
        name: f"plugins/{name}" for name in plugin_names if tree.has_dir(f"plugins/{name}")
    }
    roots.update({name: name for name in uer._EXTRA_CONSUMER_DIRS if tree.has_dir(name)})
    consumers: dict[str, list[str]] = {}
    for name, root in sorted(roots.items()):
        prefix = f"{root}/libs/"
        for path, (mode, _kind, _oid) in tree.entries.items():
            if not path.startswith(prefix):
                continue
            parts = path[len(prefix):].split("/")
            if len(parts) < 2:
                tree.validate(path)
                continue
            inner = parts[1:]
            if (
                any(part.endswith(".egg-info") for part in inner[:-1])
                or set(inner[:-1]) & {".ruff_cache", ".pytest_cache", ".mypy_cache", "__pycache__"}
                or (len(inner) > 1 and inner[0] in {".venv", "build", "dist"})
                or (mode != "120000" and inner[-1].endswith(".egg-info"))
            ):
                continue
            if name not in consumers.get(parts[0], ()):
                consumers.setdefault(parts[0], []).append(name)
        try:
            refs = uer.find_uv_editable_refs(
                REPO / root, data=tree.toml(f"{root}/pyproject.toml"),
                resolve_symlinks=False,
            )
        except uer.ManifestUnreadable as error:
            raise RuntimeError(f"invalid dependency input {root}: {error}") from error
        for _name, _raw_path, lib, _editable in refs:
            if name not in consumers.get(lib, ()):
                consumers.setdefault(lib, []).append(name)
    _implicit_consumer_seeds(consumers, set(roots) & plugin_names)
    return _transitive_consumers(consumers, tree=tree)


def _vendored_consumers(*, tree: _GitTree | None = None) -> dict[str, list[str]]:
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
    inside a real ``pyproject.toml``. Canonical sibling dependencies expand
    every seed's consumers transitively, matching the release materializer,
    including shared edges and cycles."""
    if tree is not None:
        return _snapshot_consumers(tree)
    consumers: dict[str, list[str]] = {}
    if PLUGINS_DIR.is_dir():
        for plugin in sorted(p for p in PLUGINS_DIR.iterdir() if p.is_dir()):
            libs = plugin / "libs"
            if libs.is_dir():
                for lib in sorted(x for x in libs.iterdir() if x.is_dir()):
                    if not is_artifact_only(lib):
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
                if not is_artifact_only(lib):
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
    _implicit_consumer_seeds(
        consumers, {p.name for p in PLUGINS_DIR.iterdir() if p.is_dir()}
        if PLUGINS_DIR.is_dir() else set(),
    )
    return _transitive_consumers(consumers)


def _is_ignored(rel_parts: tuple[str, ...], name: str) -> bool:
    if _IGNORE_PARTS & set(rel_parts):
        return True
    if name in _IGNORE_NAMES:
        return True
    return any(name.endswith(s) for s in _IGNORE_SUFFIX)


def _plugins_needing_bump(
    changed: list[str], consumers: dict[str, list[str]], *, tree: _GitTree | None = None,
) -> dict[str, set[str]]:
    """Map ``plugin -> {reasons}`` for every plugin whose content changed.

    A path under ``plugins/<p>/`` charges ``<p>``; a path under a top-level
    shared ``libs/<lib>/`` charges every plugin that vendors ``<lib>``; a
    path under a recognized standalone consumer's own top-level tree (e.g.
    ``worktree-manager/``) charges that consumer directly -- without this,
    a direct payload change to a standalone consumer was invisible to both
    this guard and `check-changefile-presence.py`/`compute_from_diff()`,
    which both depend on this same map (PR #4514 review)."""
    needing: dict[str, set[str]] = {}
    packaged_sources: dict[str, set[str]] = {}
    if tree is None:
        manifests = list(PLUGINS_DIR.glob("*/packaged-assets.json"))
        manifests.extend(
            REPO / name / "packaged-assets.json" for name in uer._EXTRA_CONSUMER_DIRS
            if (REPO / name / "packaged-assets.json").is_file()
        )
    else:
        manifests = [
            REPO / name for name in tree.entries
            if (
                name.startswith("plugins/") and len(name.split("/")) == 3
                and name.endswith("/packaged-assets.json")
            ) or name in {f"{root}/packaged-assets.json" for root in uer._EXTRA_CONSUMER_DIRS}
        ]
    if manifests:
        import launch_wrapper_assets_ref as assets

    for manifest_path in manifests:
        consumer = (
            manifest_path.parent.name if manifest_path.is_relative_to(PLUGINS_DIR)
            else manifest_path.parent.relative_to(REPO).as_posix()
        )
        kwargs = {} if tree is None else {
            "data": json.loads(tree.read(manifest_path.relative_to(REPO).as_posix()))
        }
        groups = assets.load_manifest_groups(
            manifest_path.parent, manifest_name=assets.PACKAGED_MANIFEST_NAME, **kwargs,
        )
        for manifest in groups:
            for name in manifest.files:
                source = f"{manifest.canonical_dir_raw}/{name}"
                packaged_sources.setdefault(source, set()).add(consumer)
    for path in changed:
        for consumer in packaged_sources.get(path, ()):
            needing.setdefault(consumer, set()).add(f"packaged source {path}")
        parts = tuple(path.split("/"))
        if len(parts) < 2:
            continue
        if parts[0] == "plugins":
            plugin = parts[1]
            inner = parts[2:]
            if inner and _is_ignored(inner, parts[-1]):
                continue
            # Only real plugin dirs (with a manifest) count.
            if (
                (PLUGINS_DIR / plugin / "plugin.json").exists() if tree is None else
                tree.has_file(f"plugins/{plugin}/plugin.json")
            ):
                needing.setdefault(plugin, set()).add(f"plugins/{plugin}/")
        elif parts[0] == "libs" and len(parts) >= 2:
            lib = parts[1]
            inner = parts[2:]
            if inner and _is_ignored(inner, parts[-1]):
                continue
            for plugin in consumers.get(lib, ()):
                needing.setdefault(plugin, set()).add(f"libs/{lib}/ (vendored)")
        else:
            consumer = next(
                (root for root in uer._EXTRA_CONSUMER_DIRS if path.startswith(root + "/")), None,
            )
            if consumer is None or not (
                (REPO / consumer / "pyproject.toml").exists() if tree is None else
                tree.has_file(f"{consumer}/pyproject.toml")
            ):
                continue
            inner = parts[len(Path(consumer).parts):]
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
        # The base (default origin/dev) is unavailable -- a fresh clone or a
        # detached state. Degrade to a no-op rather than wedge the push.
        print(
            f"check-version-bump: base '{base_ref}' unavailable; skipping "
            "(fetch it to enable the guard).",
        )
        return 0, []
    mbase = _merge_base(base, head)
    if mbase is None:
        # `base` resolves but shares no common ancestor with `head`: `main`
        # is a generated/promoted artifact (see `tools/promote_release.py`'s
        # own docstring), never a fork point, so a branch's only shared
        # ancestor with `main` was always just the repo's original root --
        # and a `main` history rewrite (docs/pipelines.md's "If main's
        # history is force-rewritten") changes every commit's SHA on
        # `main`'s own line, severing even that. Silently falling back to a
        # literal two-dot diff against the raw base's CURRENT snapshot
        # would produce a large, misleading "changed" set spanning every
        # plugin that happens to differ between that snapshot and this
        # branch, not this branch's own actual changes -- degrade instead
        # the same way an unresolvable base already does just below (this
        # tool's own established "never wedge the push over an
        # infra/topology hiccup" stance).
        print(
            f"check-version-bump: base '{base_ref}' shares no common history with "
            f"HEAD (e.g. after a main history rewrite, or because this base is "
            f"unrelated to this branch's real trunk) -- skipping. For a release/"
            f"recovery check against a specific target, pass an explicit --base.",
        )
        return 0, []

    changed = _changed_files(mbase, head)
    if not changed:
        print("check-version-bump: no changes vs base; nothing to check.")
        return 0, []

    tree = _GitTree(head)
    consumers = _vendored_consumers(tree=tree)
    needing = _plugins_needing_bump(changed, consumers, tree=tree)
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
                has_pyproject = (PLUGINS_DIR / plugin / "pyproject.toml").is_file()
                direct_fix = (
                    "plugin.json + pyproject.toml + marketplace.json"
                    if has_pyproject
                    # A payload-only plugin (e.g. copilot-extensions-harness)
                    # has no pyproject.toml at all -- naming it here would
                    # prescribe a file that doesn't exist (docs/pipelines.md's
                    # payload-only/runtime plugin distinction).
                    else "plugin.json + marketplace.json"
                )
                fix = (
                    "for ordinary PR compliance, add a changefile for it "
                    "(python tools/changefile.py add --plugin <name> --type patch "
                    f"--comment '...'); to clear THIS check directly, bump {direct_fix}"
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
    ap.add_argument("--base", default="origin/dev",
                    help="base ref to diff against (default: origin/dev -- "
                         "this repo's real contribution trunk; pass "
                         "origin/main explicitly for a release/recovery check)")
    ap.add_argument("--head", default="HEAD", help="head ref (default: HEAD)")
    ap.add_argument("--list", action="store_true",
                    help="print the plugin<->vendored-lib map and exit")
    args = ap.parse_args(argv)

    if args.list:
        _print_list()
        return 0

    try:
        code, violations = check(args.base, args.head)
    except RuntimeError as error:
        print(f"check-version-bump: {error}", file=sys.stderr)
        return 1
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
