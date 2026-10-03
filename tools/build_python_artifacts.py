#!/usr/bin/env python3
"""Build promotion-time, content-addressed Python artifacts for one plugin:
its own wheel plus a wheel for every vendored `libs/<lib>` dependency it
needs, discovered recursively in BOTH shapes a consumer's `pyproject.toml`
can carry: the dev-branch live, escaping `uv`-editable canonical-reference
form (`uv_editable_ref.find_uv_editable_refs`) and an ordinary, non-escaping
in-tree vendored copy (`find_in_tree_lib_sources`) -- the form every
reference becomes once `materialize_main.py` has run, which is the real
state this tool actually builds against during promotion. This is Phase 2
of the `governed-python-artifact-promotion` effort
(`efforts/active/governed-python-artifact-promotion/README.md`) -- the first
implementation slice: wheel + manifest generation. Publication (a GitHub
Release), attestation (Sigstore/OIDC), and promotion-pipeline wiring are
separate, later slices; this script only builds artifacts and writes a
manifest describing them, and does not publish or sign anything.

**Artifact identity.** Per the effort's resolved Open Design Questions, an
artifact set's identity folds together:

* a **payload hash** -- a content hash of the plugin's own directory plus
  every vendored lib directory it needs, walked directly on disk (not
  `git HEAD`: promotion builds from a scratch tree already mutated by
  version bumps and materialization, so the bytes actually fed to the
  build can differ from `HEAD` even though both describe "this commit" --
  hashing the working tree is the only way the identity matches what was
  actually built);
* the **platform/python tags** read directly off the built wheels'
  filenames (the canonical, self-describing source for this -- never
  guessed from the running interpreter), with a conflicting pair of
  non-universal tags across the wheel set treated as a hard failure rather
  than resolved by whichever wheel happened to be built first;
* the **build-tool closure** actually used -- a single, pre-resolved
  ``setuptools``/``wheel`` version pair, pinned into one dedicated venv
  (``resolve_toolchain_lock``) BEFORE any wheel is built and reused,
  unchanged, across every wheel in one invocation (and, when a caller
  passes the same ``--toolchain-venv`` across several invocations, across
  a whole promotion run). Every wheel is built with ``uv build --wheel
  --no-build-isolation`` against that one locked venv rather than `uv`'s
  normal per-wheel isolated PEP 517 build, which would otherwise silently
  resolve "whatever satisfies pyproject.toml's open-floor `requires`
  today" -- unrecorded and unreproducible run-to-run. Each built wheel's
  own `dist-info/WHEEL` ``Generator:`` line is read back and verified to
  match the locked toolchain exactly; a mismatch (or a missing
  `Generator:` line) fails the build closed rather than silently recording
  an unknown or drifted toolchain; and
* every **wheel's own filename and digest** -- two artifact sets with the
  same source/tags/toolchain but byte-different wheels must never collide
  on the same `artifact_id`.

**Manifest schema note (version 2):** ``build_toolchain`` is a structured
record (``{"packages": {"setuptools": "...", "wheel": "..."}, "lock_id":
"sha256:..."}``) describing the one locked toolchain every wheel in the
set was built against -- not schema version 1's ad hoc list of per-wheel
``Generator:`` strings gathered after the fact.

Usage::

    python tools/build_python_artifacts.py agent-bridge --out-dir /tmp/dist

    # Share one locked toolchain across several plugins in one promotion
    # run by passing the SAME --toolchain-venv to each invocation:
    python tools/build_python_artifacts.py agent-bridge --out-dir /tmp/dist \\
        --toolchain-venv /tmp/toolchain-venv
    python tools/build_python_artifacts.py agent-worktrees --out-dir /tmp/dist \\
        --toolchain-venv /tmp/toolchain-venv
"""
from __future__ import annotations

import argparse
import contextlib
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import urllib.parse
import zipfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uv_editable_ref as uer  # noqa: E402

try:  # tomllib is stdlib on 3.11+; tomli backports it for this repo's
    # 3.10 support floor -- mirrors uv_editable_ref's own identical fallback.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"
MANIFEST_SCHEMA = "copilot-extensions.python-artifact-manifest"
MANIFEST_SCHEMA_VERSION = 2

#: The exact packages a locked build-toolchain venv pins -- every
#: `pyproject.toml` under `plugins/` and `libs/` uses the same
#: `setuptools.build_meta` backend (confirmed by this effort's own Build
#: hermeticity survey), so pinning these two is sufficient to make every
#: wheel in a run reproducible without a per-backend scheme.
_LOCKED_TOOLCHAIN_PACKAGES = ("setuptools", "wheel")

_GENERATOR_RE = re.compile(r"^Generator:\s*(.+?)\s*$", re.MULTILINE)
_BUILD_TAG_RE = re.compile(r"^[0-9][^-]*$")
_VERSION_LIKE_RE = re.compile(r"^[0-9]")

# Tags considered "universal" (compatible everywhere) -- any other tag is
# strictly more specific and wins when picking the artifact set's own
# overall platform/python identity (see `_more_specific`).
_UNIVERSAL_TAGS = {"none", "any", "py3", "py2.py3"}


class ArtifactBuildError(Exception):
    """A plugin/lib wheel could not be built, or the result could not be
    understood (unparseable filename, unreadable WHEEL metadata) -- callers
    must fail closed rather than emit a manifest describing a guess."""


def _read_sources_table(consumer_dir: Path) -> dict:
    """The raw ``[tool.uv.sources]`` table from ``consumer_dir``'s
    `pyproject.toml` (``{}`` if the file is absent or symlinked -- a valid
    no-op, mirroring `uv_editable_ref.find_uv_editable_refs`'s own identical
    cases). Raises `ArtifactBuildError` when the manifest exists but cannot
    be read/parsed, or the table is structurally malformed, rather than
    silently returning no entries."""
    pyproject = consumer_dir / "pyproject.toml"
    if pyproject.is_symlink() or not pyproject.is_file():
        return {}
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ArtifactBuildError(f"{pyproject}: could not read/parse: {exc}") from exc
    tool = data.get("tool", {})
    if not isinstance(tool, dict):
        raise ArtifactBuildError(f"{pyproject}: [tool] is not a table")
    uv_table = tool.get("uv", {})
    if not isinstance(uv_table, dict):
        raise ArtifactBuildError(f"{pyproject}: [tool.uv] is not a table")
    sources = uv_table.get("sources", {})
    if not isinstance(sources, dict):
        raise ArtifactBuildError(f"{pyproject}: [tool.uv.sources] is not a table")
    return sources


def find_in_tree_lib_sources(consumer_dir: Path) -> list[tuple[str, str, str]]:
    """Every NON-editable ``[tool.uv.sources]`` entry in ``consumer_dir``'s
    `pyproject.toml` whose `path` resolves to a directory literally named
    `libs/<lib>` under an ALLOWED location -- an ordinary, already-
    vendored/materialized copy, the complement of
    `uv_editable_ref.find_uv_editable_refs`'s editable-only scope (an entry
    is classified by its `editable` marker, not by whether it escapes
    ``consumer_dir``'s own tree): this covers BOTH a plugin's own
    `libs/<lib>` (nested within its root) AND a vendored lib
    cross-referencing a SIBLING vendored lib in the SAME parent `libs/`
    folder (e.g. `plugins/agent-worktrees/libs/plugin-activation` depending
    on `../dropin-registry`, which escapes `plugin-activation`'s own root
    but lands in the same `plugins/agent-worktrees/libs/` it already lives
    in). The allowed set is deliberately narrow -- ``consumer_dir``'s own
    `libs/` and, only when ``consumer_dir`` itself already lives directly
    under a directory named `libs`, that SAME parent `libs/` folder -- so a
    non-editable path can never escape to an unrelated plugin's `libs/`
    directory elsewhere in the repo; `materialize_nested_uv_editable_refs`
    enforces this identical "expected sibling location" constraint for the
    materialization step itself. This is the ONLY shape left to discover
    once promotion's own `materialize_main.py` has rewritten every
    plugin's live editable reference into exactly this local, in-tree
    form; some plugins (e.g. `agent-worktrees`) also use it permanently,
    by design, even on `dev`. Returns ``(name, raw_path, lib)`` tuples."""
    sources = _read_sources_table(consumer_dir)
    consumer_root = consumer_dir.resolve()
    allowed_libs_dirs = {consumer_root / "libs"}
    if consumer_root.parent.name == "libs":
        allowed_libs_dirs.add(consumer_root.parent)
    out: list[tuple[str, str, str]] = []
    for name, entry in sources.items():
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        if entry.get("editable") is True:
            continue  # the dev-branch live canonical form is
            # find_uv_editable_refs's own job -- never double-classified.
        raw_path = entry["path"]
        if not isinstance(raw_path, str):
            raise ArtifactBuildError(
                f"{consumer_dir}: [tool.uv.sources] {name!r}'s path is not a string"
            )
        candidate = (consumer_dir / raw_path).resolve()
        if candidate.parent not in allowed_libs_dirs:
            continue
        out.append((name, raw_path, candidate.name))
    return out


def _validate_in_tree_lib_dir(label: str, unresolved: Path) -> Path:
    """Structural acceptance check for an in-tree vendored `libs/<lib>`
    directory -- deliberately lighter than
    `uv_editable_ref.uv_editable_problems` (which enforces the ESCAPING
    dev-branch form's own requirements: `editable = true` and a path that
    escapes the consumer root -- both of which a correct in-tree vendored
    copy must NOT have). Still refuses a missing directory, a missing
    `src/`/`pyproject.toml`, or a symlink, so this script can never build
    or describe a source a real materialization would consider incomplete.
    Takes the UNRESOLVED candidate path and checks `.is_symlink()` on it
    directly -- `Path.resolve()` follows symlinks, so checking a path
    that's already been resolved can never detect one; this must run
    before resolving. Also walks the ENTIRE tree for a nested symlink
    (reusing `uv_editable_ref._find_symlink` -- the same check
    `uv_editable_problems` applies to the escaping form) -- checking only
    the lib directory itself would miss a symlink anywhere below it, which
    hashing/building would then silently follow and consume bytes from
    outside the vendored tree entirely. Returns the resolved canonical
    directory."""
    if unresolved.is_symlink():
        raise ArtifactBuildError(f"{label}: {unresolved} is a symlink -- refusing")
    canonical = unresolved.resolve()
    if not canonical.is_dir():
        raise ArtifactBuildError(f"{label}: {canonical} does not exist")
    if not (canonical / "src").is_dir():
        raise ArtifactBuildError(f"{label}: {canonical}/src does not exist")
    if not (canonical / "pyproject.toml").is_file():
        raise ArtifactBuildError(f"{label}: {canonical}/pyproject.toml is missing")
    nested = uer._find_symlink(canonical)
    if nested is not None:
        where = canonical if nested == "." else canonical / nested
        raise ArtifactBuildError(
            f"{label}: {where} is a symlink -- refusing (a vendored lib tree "
            "must contain only real files)"
        )
    return canonical


def _validate_editable_canonical_ref(
    *, consumer_dir: Path, name: str, raw_path: str, lib: str
) -> Path:
    """Full acceptance check for ONE escaping, `editable = true`
    `[tool.uv.sources]` entry -- the exact rules
    `uv_editable_ref.uv_editable_problems` applies, but scoped to a single
    entry rather than a whole consumer's table. Applied uniformly at
    EVERY recursion depth (not only the originally requested top-level
    plugin, which already gets the aggregate `uv_editable_problems` check
    too): a nested lib can carry its own escaping, editable reference, and
    without this it would be queued and built completely unvalidated --
    an unsafe name, a path resolving outside the canonical `libs/<lib>`, a
    missing/incomplete directory, or a symlinked tree, none of which this
    script would otherwise catch at that depth. Returns the resolved
    canonical directory."""
    if not uer.is_safe_lib_name(lib):
        raise ArtifactBuildError(
            f"{consumer_dir}: {name} -> {raw_path}: unsafe lib name {lib!r}"
        )
    canonical_unresolved = LIBS_DIR / lib
    bad_ancestor = uer._find_symlinked_ancestor(canonical_unresolved, REPO)
    if bad_ancestor is not None:
        raise ArtifactBuildError(
            f"{consumer_dir}: {name} -> {raw_path}: {bad_ancestor} is a "
            "symlink -- refusing (a canonical lib root must be a real directory)"
        )
    canonical = (consumer_dir / raw_path).resolve()
    if not canonical.is_dir():
        raise ArtifactBuildError(
            f"{consumer_dir}: {name} -> {raw_path} (resolved {canonical}) "
            "does not exist"
        )
    if canonical_unresolved.resolve() != canonical:
        raise ArtifactBuildError(
            f"{consumer_dir}: {name} -> {raw_path} (resolved {canonical}) is "
            f"not libs/{lib}"
        )
    if not (canonical / "src").is_dir():
        raise ArtifactBuildError(
            f"{consumer_dir}: {name} -> {raw_path} (resolved {canonical}): "
            "src does not exist"
        )
    if not (canonical / "pyproject.toml").is_file():
        raise ArtifactBuildError(
            f"{consumer_dir}: {name} -> {raw_path} (resolved {canonical}): "
            "pyproject.toml is missing"
        )
    nested = uer._find_symlink(canonical)
    if nested is not None:
        where = canonical if nested == "." else canonical / nested
        raise ArtifactBuildError(
            f"{consumer_dir}: {name} -> {raw_path}: {where} is a symlink -- "
            "refusing (a canonical lib tree must contain only real files)"
        )
    return canonical


def _check_no_in_tree_editable_entries(consumer_dir: Path) -> None:
    """Fails closed on an `editable = true` entry whose path does NOT
    escape ``consumer_dir``'s own root -- a combination neither discovery
    function covers (`find_uv_editable_refs` only ever returns escaping
    entries; `find_in_tree_lib_sources` explicitly skips every
    `editable = true` entry, escaping or not), so it would otherwise be
    silently omitted from the manifest entirely rather than built or
    explicitly rejected. `editable = true` is reserved for the dev-branch
    canonical reference, which always escapes to a top-level `libs/<lib>`;
    an in-tree path marked editable has no established meaning in this
    repo and must not be silently dropped."""
    sources = _read_sources_table(consumer_dir)
    consumer_root = consumer_dir.resolve()
    for name, entry in sources.items():
        if not isinstance(entry, dict) or "path" not in entry:
            continue
        if entry.get("editable") is not True:
            continue
        raw_path = entry["path"]
        if not isinstance(raw_path, str):
            continue  # ManifestUnreadable from find_uv_editable_refs covers this
        candidate = (consumer_dir / raw_path).resolve()
        if not uer.escapes_root(candidate, consumer_root):
            raise ArtifactBuildError(
                f"{consumer_dir}: {name} -> {raw_path} is editable = true "
                "but does not escape its own root -- this combination is "
                "not supported (editable = true is reserved for the "
                "dev-branch canonical reference, which always escapes to a "
                "top-level libs/<lib>)"
            )


def _add_vendored_lib(
    out: dict[str, Path], pending: list[Path], *, lib: str, canonical: Path, label: str
) -> None:
    """Records ``lib`` -> ``canonical`` in ``out``, queueing it for
    recursion the first time it's seen. Fails closed if ``lib`` was
    already recorded pointing at a DIFFERENT canonical directory --
    deduplicating solely by the final directory name would otherwise
    silently drop one of two genuinely distinct sources that happen to
    share a name (e.g. `libs/a/libs/widget` and `libs/b/libs/widget`),
    building and describing only whichever was visited first."""
    existing = out.get(lib)
    if existing is not None:
        if existing.resolve() != canonical.resolve():
            raise ArtifactBuildError(
                f"{label}: lib name {lib!r} resolves to two different "
                f"directories ({existing} vs {canonical}) -- ambiguous, refusing"
            )
        return
    out[lib] = canonical
    pending.append(canonical)


def resolve_vendored_libs(consumer_dir: Path) -> list[tuple[str, Path]]:
    """Every vendored lib ``consumer_dir`` needs, recursively, discovering
    BOTH known `[tool.uv.sources]` shapes: the dev-branch live, escaping,
    `editable = true` canonical-reference form
    (`uv_editable_ref.find_uv_editable_refs`) AND an ordinary, non-editable
    in-tree vendored copy (`find_in_tree_lib_sources`, validated with
    `_validate_in_tree_lib_dir`) -- classified by the entry's `editable`
    marker, not by whether its path escapes the immediate consumer's own
    directory. Discovering only the editable form would miss every plugin
    that vendors its libs in-tree by design (e.g. `agent-worktrees`), AND
    find nothing at all once promotion's own materialization step has
    rewritten every reference into exactly that in-tree form -- the real
    state this tool actually runs against during promotion.

    `uv_editable_ref.uv_editable_problems` -- the same acceptance check
    `materialize_main.py` applies -- is run ONLY against the originally
    requested ``consumer_dir`` (the plugin itself), where its "must escape
    to the literal top-level `libs/<lib>` with `editable = true`" rule is
    the correct, established contract. It is deliberately NOT re-applied
    while recursing into an already-discovered vendored lib's own
    `pyproject.toml`: a vendored lib legitimately cross-references a
    SIBLING vendored lib one level up without `editable = true` (e.g.
    `plugins/agent-worktrees/libs/plugin-activation` depending on
    `../dropin-registry`) -- a real, already-shipped pattern that
    `uv_editable_problems` was never designed to validate and would
    otherwise wrongly reject as a broken canonical reference.

    Recurses into each discovered lib's own `pyproject.toml` the same way
    `materialize_main.py` does, so promotion's artifact set and its
    materialized-tree enumeration never disagree. Returns
    ``(lib_name, canonical_dir)`` pairs, each lib listed once (by name) even
    if more than one consumer along the walk references it."""
    out: dict[str, Path] = {}
    pending = [consumer_dir]
    seen_dirs: set[Path] = set()
    is_top_level = True
    while pending:
        current = pending.pop()
        current_r = current.resolve()
        if current_r in seen_dirs:
            continue
        seen_dirs.add(current_r)
        if is_top_level:
            consumer_label = current_r.name
            problems = uer.uv_editable_problems(consumer_label, current)
            if problems:
                raise ArtifactBuildError(
                    f"{current}: rejected by uv_editable_problems: "
                    f"{'; '.join(problems)}"
                )
            is_top_level = False
        _check_no_in_tree_editable_entries(current)

        try:
            escaping_refs = uer.find_uv_editable_refs(current)
        except uer.ManifestUnreadable as exc:
            raise ArtifactBuildError(str(exc)) from exc
        in_tree_refs = find_in_tree_lib_sources(current)
        in_tree_names = {name for name, _raw_path, _lib in in_tree_refs}
        for name, raw_path, lib, editable in escaping_refs:
            if not editable:
                # Not the dev-branch live canonical form -- expected to be
                # a sibling in-tree cross-reference that
                # `find_in_tree_lib_sources`'s own, more tightly
                # constrained scan of this same `current` also discovered.
                # If it did NOT (e.g. the target escapes to somewhere
                # outside every allowed `libs/` location), this reference
                # would otherwise be silently dropped entirely -- neither
                # validated-and-included nor rejected -- even though
                # `materialize_nested_uv_editable_refs` would refuse the
                # exact same reference. Fail closed instead of omitting it.
                if name not in in_tree_names:
                    raise ArtifactBuildError(
                        f"{current}: {name} -> {raw_path} is not editable = "
                        "true and does not resolve to an allowed in-tree "
                        "vendored-lib location -- refusing to silently omit "
                        "it from the artifact set"
                    )
                continue
            # Validated per-entry at EVERY recursion depth (not only via
            # the top-level `uv_editable_problems` call above) -- a nested
            # lib can carry its own escaping, editable reference, and
            # without this it would be queued and built unvalidated.
            canonical = _validate_editable_canonical_ref(
                consumer_dir=current, name=name, raw_path=raw_path, lib=lib
            )
            _add_vendored_lib(
                out, pending, lib=lib, canonical=canonical, label=str(current)
            )

        for name, raw_path, lib in in_tree_refs:
            if not uer.is_safe_lib_name(lib):
                raise ArtifactBuildError(
                    f"{current}: unsafe in-tree vendored-lib name {lib!r} "
                    f"(from {name!r})"
                )
            canonical = _validate_in_tree_lib_dir(
                f"{current}:{name}", current / raw_path
            )
            _add_vendored_lib(
                out, pending, lib=lib, canonical=canonical, label=str(current)
            )
    return sorted(out.items())


_PAYLOAD_IGNORE_DIR_NAMES = {
    ".git", ".venv", "__pycache__", ".pytest_cache", "build", "dist",
    ".mypy_cache", ".ruff_cache",
}
_PAYLOAD_IGNORE_SUFFIXES = (".pyc", ".pyo")


def _hash_fields(*fields: str) -> str:
    """Hashes an ordered sequence of string fields unambiguously: each
    field is length-prefixed before its UTF-8 bytes, so no delimiter choice
    can let two DIFFERENT field sequences serialize to the same digest
    (e.g. joining `"rel:hash"` pairs with a plain separator lets a crafted
    filename or digest absorb the separator and collide with a
    differently-split pair -- length-prefixing makes that impossible)."""
    h = hashlib.sha256()
    for field in fields:
        data = field.encode("utf-8")
        h.update(len(data).to_bytes(8, "big"))
        h.update(data)
    return h.hexdigest()


def directory_content_hash(d: Path) -> str:
    """A hex digest over every regular file's relative path and content
    under ``d`` (skipping VCS/cache/build-artifact noise), sorted so
    traversal order never affects the result. This hashes the actual
    working tree -- never `git HEAD` -- because promotion builds from a
    scratch tree already mutated by version bumps and materialization: the
    bytes a build actually consumes can differ from `HEAD` even when both
    nominally describe the same commit, and the payload hash must track
    what was really built. Must be called BEFORE building anything from
    ``d`` -- a build backend (even one invoked with build isolation) can
    leave residue inside the source tree itself (e.g. a `*.egg-info`
    directory setuptools' `build_meta` creates alongside the sources), and
    hashing after the fact would fold build output into the identity of
    the very input that produced it."""
    entries: list[tuple[str, str]] = []
    for p in sorted(d.rglob("*")):
        if not p.is_file():
            continue
        rel_parts = p.relative_to(d).parts
        if any(part in _PAYLOAD_IGNORE_DIR_NAMES for part in rel_parts[:-1]):
            continue
        if any(part.endswith(".egg-info") for part in rel_parts):
            # A build backend (setuptools' build_meta in particular) can
            # leave a `*.egg-info` directory inside the source tree even
            # for an isolated wheel build -- unlike `_PAYLOAD_IGNORE_DIR_NAMES`'
            # fixed names, its own directory name varies per-package (e.g.
            # `demo.egg-info`), so it must be matched by suffix, not exact
            # name. Without this, a build left behind from one invocation
            # changes the NEXT invocation's "pre-build" payload hash for
            # otherwise-unchanged source (mirrors `uv_editable_ref._file_hashes`'s
            # own identical exclusion).
            continue
        if p.suffix in _PAYLOAD_IGNORE_SUFFIXES:
            continue
        rel = p.relative_to(d).as_posix()
        entries.append((rel, hashlib.sha256(p.read_bytes()).hexdigest()))
    flattened = [field for pair in sorted(entries) for field in pair]
    return _hash_fields(*flattened)


def compute_payload_hash(dirs: list[Path]) -> str:
    """A single hash over every directory in ``dirs`` (plugin + vendored
    libs), each identified by its repo-relative path and its own working-
    tree content hash (`directory_content_hash`). Sorted so key order never
    affects the hash, and the relative path is included so swapping which
    lib lives at which path is itself a change (not just the content). Must
    be called BEFORE building any wheel from ``dirs`` -- see
    `directory_content_hash`'s own docstring."""
    pairs = [
        (d.resolve().relative_to(REPO).as_posix(), directory_content_hash(d))
        for d in dirs
    ]
    flattened = [field for pair in sorted(pairs) for field in pair]
    return f"sha256:{_hash_fields(*flattened)}"


def parse_wheel_filename(path: Path) -> dict[str, str]:
    """Parses a wheel filename's identity components by tokenizing from the
    RIGHT (PEP 427's own grammar: `{name}-{version}(-{build tag})?-{python
    tag}-{abi tag}-{platform tag}.whl`). A well-formed wheel's distribution
    `name` is ALWAYS exactly one `-`-delimited token (PEP 427 normalizes
    `-`/`_`/`.` runs in the distribution name to a single `_`, specifically
    so this split is never ambiguous) -- it is never reconstructed by
    rejoining multiple tokens, which would silently accept a malformed,
    non-normalized name (e.g. `demo-pkg-1.2.3-py3-none-any.whl`, whose
    unnormalized `demo-pkg` literally contains the separator) and misparse
    it as `name="demo", version="pkg"`. The three compatibility tags never
    contain hyphens, so they are unambiguously the last three tokens; the
    single token between `name` and those three tags is `version`, or --
    if there are two such tokens -- `version` then an optional numeric
    build tag. `version` is additionally required to look like a real
    version (start with a digit, per PEP 440), closing the case above
    where the naive split would otherwise accept a non-version token."""
    if not path.name.endswith(".whl"):
        raise ArtifactBuildError(f"{path}: not a well-formed wheel filename")
    tokens = path.name[: -len(".whl")].split("-")
    if len(tokens) < 5:
        raise ArtifactBuildError(f"{path}: not a well-formed wheel filename")
    platform_tag, abi_tag, python_tag = tokens[-1], tokens[-2], tokens[-3]
    name = tokens[0]
    middle = tokens[1:-3]
    if len(middle) == 1:
        version = middle[0]
    elif len(middle) == 2 and _BUILD_TAG_RE.match(middle[1]):
        version = middle[0]
    else:
        raise ArtifactBuildError(
            f"{path}: not a well-formed wheel filename (ambiguous "
            "distribution/version/build-tag split)"
        )
    if not name or not _VERSION_LIKE_RE.match(version):
        raise ArtifactBuildError(f"{path}: not a well-formed wheel filename")
    return {
        "name": name,
        "version": version,
        "python_tag": python_tag,
        "abi_tag": abi_tag,
        "platform_tag": platform_tag,
    }


def _more_specific(a: str, b: str, *, slot: str) -> str:
    """Prefer whichever of two same-slot tags is NOT a universal wildcard.
    Two DIFFERENT non-universal tags in the same slot (e.g. `cp311` and
    `cp312`) mean the wheel set genuinely has no single valid identity for
    that slot -- silently keeping whichever was encountered first would
    advertise the narrower set as compatible with an environment that
    cannot actually use part of it. Fail closed instead of guessing."""
    if a == b:
        return a
    if a in _UNIVERSAL_TAGS and b not in _UNIVERSAL_TAGS:
        return b
    if b in _UNIVERSAL_TAGS and a not in _UNIVERSAL_TAGS:
        return a
    if a in _UNIVERSAL_TAGS and b in _UNIVERSAL_TAGS:
        return a
    raise ArtifactBuildError(
        f"conflicting {slot} tags in one artifact set: {a!r} vs {b!r} -- "
        "no single wheel-compatibility identity covers both"
    )


def overall_identity_tags(wheel_infos: list[dict[str, str]]) -> dict[str, str]:
    """The artifact set's own (python_tag, abi_tag, platform_tag): the most
    specific tag present in any single wheel wins per slot, since a
    platform-specific wheel anywhere in the set makes the whole set only
    valid for that platform even if other wheels in the set are universal
    pure-Python wheels. Raises `ArtifactBuildError` if two wheels disagree
    on a genuinely conflicting, non-universal tag for the same slot."""
    python_tag = abi_tag = platform_tag = None
    for info in wheel_infos:
        python_tag = info["python_tag"] if python_tag is None else _more_specific(
            python_tag, info["python_tag"], slot="python_tag"
        )
        abi_tag = info["abi_tag"] if abi_tag is None else _more_specific(
            abi_tag, info["abi_tag"], slot="abi_tag"
        )
        platform_tag = info["platform_tag"] if platform_tag is None else _more_specific(
            platform_tag, info["platform_tag"], slot="platform_tag"
        )
    return {
        "python_tag": python_tag or "py3",
        "abi_tag": abi_tag or "none",
        "platform_tag": platform_tag or "any",
    }


def read_wheel_generator(wheel_path: Path) -> str:
    """The ``Generator:`` line from the wheel's own `dist-info/WHEEL` file
    -- the build backend + version that actually produced it (e.g.
    ``"setuptools (84.1.0)"``), read from the artifact itself rather than
    assumed from `pyproject.toml`'s open-floor `requires`. A manifest that
    cannot name the toolchain that built a wheel is exactly the
    unreproducible state this effort's build-hermeticity resolution exists
    to close, so a wheel with no (or unreadable) `Generator:` line fails
    the build rather than recording an unknown toolchain."""
    with zipfile.ZipFile(wheel_path) as zf:
        wheel_meta_names = [
            n for n in zf.namelist() if n.endswith(".dist-info/WHEEL")
        ]
        if len(wheel_meta_names) != 1:
            # A malformed wheel with multiple matching entries must never
            # silently trust whichever ZIP member happens to come first --
            # that could record a generator from the WRONG distribution's
            # metadata.
            raise ArtifactBuildError(
                f"{wheel_path}: expected exactly one dist-info/WHEEL entry, "
                f"found {len(wheel_meta_names)}: {wheel_meta_names}"
            )
        raw = zf.read(wheel_meta_names[0])
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        # `errors="replace"` would silently accept corrupt metadata and let
        # a replacement-character "Generator" be recorded as if the real
        # toolchain were known -- the opposite of fail-closed.
        raise ArtifactBuildError(
            f"{wheel_path}: dist-info/WHEEL is not valid UTF-8: {exc}"
        ) from exc
    m = _GENERATOR_RE.search(text)
    if not m:
        raise ArtifactBuildError(
            f"{wheel_path}: dist-info/WHEEL has no Generator: line -- refusing "
            "to record an artifact with an unknown build toolchain"
        )
    return m.group(1)


def _venv_python_path(venv_dir: Path) -> Path:
    """The venv's own interpreter path -- Windows and POSIX venvs place it
    differently, and this must match whatever `uv venv` actually created so
    later steps query the right interpreter."""
    if sys.platform == "win32":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


#: A stdlib-only script (no third-party imports, since the locked
#: toolchain venv only ever has `setuptools`/`wheel` installed) that
#: reports the same keys `packaging.markers.default_environment()` would,
#: queried by running it THROUGH the locked interpreter itself -- so a
#: PEP 508 marker evaluates against the actually-locked Python, never
#: whatever process happens to be running this tool. ``implementation_version``
#: is deliberately computed from ``sys.implementation.version`` (mirroring
#: `packaging.markers`' own `format_full_version` helper), NOT
#: `platform.python_version()` -- they coincide on CPython but diverge on
#: alternative implementations (e.g. PyPy), where a marker on
#: `implementation_version` would otherwise silently take the wrong branch.
_MARKER_ENV_QUERY_SCRIPT = (
    "import json, os, platform, sys\n"
    "def _format_full_version(info):\n"
    "    version = f'{info.major}.{info.minor}.{info.micro}'\n"
    "    kind = info.releaselevel\n"
    "    if kind != 'final':\n"
    "        version += kind[0] + str(info.serial)\n"
    "    return version\n"
    "print(json.dumps({\n"
    "    'implementation_name': sys.implementation.name,\n"
    "    'implementation_version': _format_full_version(sys.implementation.version),\n"
    "    'os_name': os.name,\n"
    "    'platform_machine': platform.machine(),\n"
    "    'platform_release': platform.release(),\n"
    "    'platform_system': platform.system(),\n"
    "    'platform_version': platform.version(),\n"
    "    'python_full_version': platform.python_version(),\n"
    "    'platform_python_implementation': platform.python_implementation(),\n"
    "    'python_version': '.'.join(platform.python_version_tuple()[:2]),\n"
    "    'sys_platform': sys.platform,\n"
    "}))\n"
)


def _query_marker_environment(python_exe: Path) -> dict:
    """Queries ``python_exe`` itself for its own PEP 508 marker-environment
    values -- never assumed from the process running this script, which
    may be a different Python than ``--python`` locked into the toolchain
    venv."""
    result = subprocess.run(
        [str(python_exe), "-c", _MARKER_ENV_QUERY_SCRIPT],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise ArtifactBuildError(
            f"could not query marker environment from {python_exe}:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise ArtifactBuildError(
            f"{python_exe}: marker-environment query produced non-JSON "
            f"output: {exc}"
        ) from exc


class ToolchainLock:
    """A resolved, pinned build toolchain (exact ``setuptools``/``wheel``
    versions) installed into one dedicated venv, meant to be reused --
    unchanged -- across every wheel built in a single invocation (and,
    when a caller passes the same ``venv_dir`` to ``resolve_toolchain_lock``
    across several invocations, across a whole promotion run), so two
    wheels built moments apart never silently differ in which toolchain
    version actually produced them (the `governed-python-artifact-promotion`
    effort's own resolved Build hermeticity direction)."""

    def __init__(self, venv_python: Path, packages: dict[str, str]):
        self.venv_python = venv_python
        self.packages = packages
        self._marker_environment: dict | None = None

    @property
    def generator(self) -> str:
        """The exact ``Generator:`` string a wheel built with this locked
        toolchain must carry -- used to verify a ``--no-build-isolation``
        build actually used the pinned venv rather than silently falling
        back to some other setuptools reachable on `PATH`."""
        return f"setuptools ({self.packages['setuptools']})"

    @property
    def marker_environment(self) -> dict:
        """The PEP 508 environment-marker dict for THIS locked toolchain's
        own interpreter (``venv_python``) -- queried directly from that
        interpreter (never assumed from whatever process happens to be
        running this tool), so a constraint like `python_version < "3.0"`
        evaluates against the interpreter actually locked, even when
        ``--python`` selected a different one than this process's own.
        Queried once and cached -- the locked venv's interpreter never
        changes for the lifetime of this ``ToolchainLock``."""
        if self._marker_environment is None:
            self._marker_environment = _query_marker_environment(self.venv_python)
        return self._marker_environment

    @property
    def lock_id(self) -> str:
        """A stable identity for this exact pinned toolchain, folded into
        every artifact's own ``artifact_id`` (see ``build_plugin_artifacts``)
        so two promotion runs that happen to lock different setuptools/
        wheel versions are naturally different, auditable artifact
        identities rather than a silent same-identity mismatch."""
        pairs = [f"{name}=={version}" for name, version in sorted(self.packages.items())]
        return "sha256:" + _hash_fields(*pairs)


_GOVERNED_FEED_DEFAULT_INDEX_ENV_VARS = ("UV_DEFAULT_INDEX", "UV_INDEX_URL")
_PUBLIC_PYPI_HOSTS = {"pypi.org", "pypi.python.org", "test.pypi.org"}
#: Affirmative trust policy for `resolve_toolchain_lock`'s governed-feed
#: gate (see `_governed_feed_configured`'s own docstring): a comma-
#: separated allowlist of hostnames the MACHINE explicitly asserts are its
#: own governed feed. Never committed/hardcoded here -- this repo stays
#: feed-neutral (`tools/check-feed-neutrality.py`); only a machine's own
#: local environment ever populates this.
_TRUSTED_INDEX_HOSTS_ENV_VAR = "BUILD_PYTHON_ARTIFACTS_TRUSTED_INDEX_HOSTS"


def _is_public_pypi_url(url: str) -> bool:
    """Whether ``url`` resolves to a public PyPI-family index (production
    or Test PyPI) -- a feed configuration that NAMES one of these hosts,
    even explicitly, is never a governed feed."""
    try:
        host = urllib.parse.urlsplit(url).hostname
    except ValueError:
        return False
    return (host or "").lower() in _PUBLIC_PYPI_HOSTS


def _url_host(url: str) -> str | None:
    try:
        return urllib.parse.urlsplit(url).hostname
    except ValueError:
        return None


def _trusted_index_hosts(env: dict) -> set[str]:
    raw = env.get(_TRUSTED_INDEX_HOSTS_ENV_VAR, "")
    return {h.strip().lower() for h in raw.split(",") if h.strip()}


def _effective_uv_toml_candidates(env: dict) -> list[Path]:
    """The uv.toml file(s) `uv` itself would actually read for index
    config in this environment.

    ``UV_CONFIG_FILE`` is EXCLUSIVE in `uv`'s own config resolution: when
    set, `uv` reads ONLY that file and skips its normal project/user-level
    discovery entirely (the same exclusivity this repo's own
    `plugins/agent-worktrees/scripts/install.sh` already handles) -- so
    this must mirror that explicitly, never additionally (or instead)
    consult the user-level `uv.toml` path while `UV_CONFIG_FILE` is set,
    which would check a file `uv` itself is NOT reading."""
    config_file = env.get("UV_CONFIG_FILE")
    if config_file:
        return [Path(config_file)]
    if sys.platform == "win32":
        appdata = env.get("APPDATA")
        return [Path(appdata) / "uv" / "uv.toml"] if appdata else []
    xdg = env.get("XDG_CONFIG_HOME")
    if xdg:
        return [Path(xdg) / "uv" / "uv.toml"]
    home = env.get("HOME")
    return [Path(home) / ".config" / "uv" / "uv.toml"] if home else []


def _effective_default_index_url(env: dict) -> str | None:
    """The URL of the index `uv` would actually use as its DEFAULT in this
    environment, or ``None`` if nothing replaces `uv`'s own implicit
    public-PyPI default. `UV_INDEX` (plural) and a plain `[[index]]` table
    without `default = true` only add a SUPPLEMENTAL index -- `uv` still
    falls back to public PyPI for anything the supplemental index doesn't
    resolve, so neither is the effective default. Only `UV_DEFAULT_INDEX`/
    `UV_INDEX_URL`, the legacy `index-url` key, or an `[[index]]` entry
    with `default = true` actually replace it."""
    for var in _GOVERNED_FEED_DEFAULT_INDEX_ENV_VARS:
        value = env.get(var)
        if value:
            return value
    for candidate in _effective_uv_toml_candidates(env):
        if not candidate.is_file():
            continue
        try:
            data = tomllib.loads(candidate.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
            continue
        if not isinstance(data, dict):
            continue
        index_url = data.get("index-url")
        if isinstance(index_url, str) and index_url:
            return index_url
        indexes = data.get("index")
        if isinstance(indexes, list):
            for entry in indexes:
                if not isinstance(entry, dict) or entry.get("default") is not True:
                    continue
                url = entry.get("url")
                if isinstance(url, str) and url:
                    return url
    return None


def _governed_feed_configured(*, env: dict | None = None) -> bool:
    """Whether `uv`'s EFFECTIVE DEFAULT index in this environment is both
    (a) not a public PyPI-family host, AND (b) affirmatively trusted by
    this machine's own explicit policy (`_TRUSTED_INDEX_HOSTS_ENV_VAR`).

    This is an ALLOWLIST, deliberately -- a prior revision inferred
    governance from "not one of a few known public hostnames", which would
    silently accept an arbitrary untrusted index (e.g. a public mirror
    under a different hostname) as "governed" just because it isn't named
    `pypi.org`. Nothing is trusted unless this machine's own environment
    affirmatively lists it: with no trust policy configured at all, this
    always fails closed, even if SOME non-public-looking index happens to
    be configured as the default. Deliberately reads configuration, never
    a hardcoded URL or hostname -- this repository stays feed-neutral (see
    `tools/check-feed-neutrality.py`); only each machine's own local
    configuration ever names its real, trusted feed."""
    env = dict(os.environ) if env is None else env
    trusted_hosts = _trusted_index_hosts(env)
    if not trusted_hosts:
        return False
    url = _effective_default_index_url(env)
    if not url or _is_public_pypi_url(url):
        return False
    host = _url_host(url)
    return host is not None and host.lower() in trusted_hosts


def resolve_toolchain_lock(venv_dir: Path, *, python: str | None = None) -> ToolchainLock:
    """Resolves one pinned build-toolchain venv at ``venv_dir`` and returns
    its exact installed ``setuptools``/``wheel`` versions.

    If ``venv_dir`` already holds a venv from an earlier call in this same
    process or a previous invocation of this script (its own interpreter
    already exists on disk), venv creation and package installation are
    skipped and only the already-installed versions are read back -- this
    is how a caller shares ONE toolchain lock across several plugins in one
    promotion run without this tool needing to build more than one plugin
    per process invocation: pass the same ``venv_dir`` (``--toolchain-venv``
    on the CLI) to every invocation in that run.

    Never resolves from an untrusted index: this call itself first
    verifies the effective default index is both non-public AND
    affirmatively trusted by this machine's own explicit policy
    (``_governed_feed_configured``), failing closed rather than letting
    `uv venv`/`uv pip install` silently resolve `setuptools`/`wheel` from
    an unverified index on a runner with no trust policy configured at
    all.

    Built in a staging directory and published into ``venv_dir`` only via
    an atomic rename AFTER both `uv venv` and `uv pip install` succeed --
    never directly into ``venv_dir`` -- so an interrupted or partially
    failed setup never leaves a venv on disk that a later call's own
    `venv_python.is_file()` reuse check would mistake for a complete,
    already-installed one (which would otherwise skip straight to the
    version query and fail there forever, requiring a manual delete to
    recover)."""
    if not _governed_feed_configured():
        raise ArtifactBuildError(
            "no affirmatively trusted governed package feed is configured "
            "on this machine (checked uv's effective default index -- "
            "UV_DEFAULT_INDEX/UV_INDEX_URL or the user-level uv.toml's "
            "index-url / [[index]] default=true -- against the explicit "
            f"trust policy in {_TRUSTED_INDEX_HOSTS_ENV_VAR}) -- refusing "
            "to install the build toolchain, which would otherwise "
            "silently resolve setuptools/wheel from an unverified index"
        )
    venv_python = _venv_python_path(venv_dir)
    if not venv_python.is_file():
        staging_venv_dir = venv_dir.parent / f".{venv_dir.name}.staging-{os.getpid()}"
        if staging_venv_dir.exists():
            shutil.rmtree(staging_venv_dir, ignore_errors=True)
        try:
            venv_cmd = ["uv", "venv", str(staging_venv_dir)]
            if python:
                venv_cmd += ["--python", python]
            result = subprocess.run(venv_cmd, capture_output=True, text=True)
            if result.returncode != 0:
                raise ArtifactBuildError(
                    f"uv venv failed for toolchain venv {venv_dir}:\n"
                    f"{result.stdout}\n{result.stderr}"
                )
            staging_venv_python = _venv_python_path(staging_venv_dir)
            install = subprocess.run(
                ["uv", "pip", "install", "--python", str(staging_venv_python),
                 *_LOCKED_TOOLCHAIN_PACKAGES],
                capture_output=True, text=True,
            )
            if install.returncode != 0:
                raise ArtifactBuildError(
                    f"uv pip install failed for toolchain venv {venv_dir}:\n"
                    f"{install.stdout}\n{install.stderr}"
                )
            # Only now, with BOTH steps verified successful, publish the
            # venv into its real location via a single rename -- a retry
            # after any earlier failure never finds a partially built
            # venv_dir, since venv_dir never existed until this point.
            # Never pre-delete an existing venv_dir: a concurrent caller
            # sharing this same path may have already published its OWN
            # complete venv (and may already be building against it) --
            # clobbering it here would violate the "unchanged shared lock"
            # promise and could break an in-flight build. If the rename
            # itself fails because venv_dir now exists (we lost the race
            # between our own absence check above and this point), defer
            # to whichever publisher won and discard our own copy instead.
            venv_dir.parent.mkdir(parents=True, exist_ok=True)
            try:
                staging_venv_dir.rename(venv_dir)
            except OSError:
                pass
        finally:
            if staging_venv_dir.exists():
                shutil.rmtree(staging_venv_dir, ignore_errors=True)

    query = subprocess.run(
        [
            str(venv_python), "-c",
            "import importlib.metadata as m, json, sys\n"
            "print(json.dumps({p: m.version(p) for p in sys.argv[1:]}))",
            *_LOCKED_TOOLCHAIN_PACKAGES,
        ],
        capture_output=True, text=True,
    )
    if query.returncode != 0:
        raise ArtifactBuildError(
            f"could not read installed toolchain versions from {venv_python}:\n"
            f"{query.stdout}\n{query.stderr}"
        )
    try:
        packages = json.loads(query.stdout)
    except json.JSONDecodeError as exc:
        raise ArtifactBuildError(
            f"{venv_python}: toolchain version query produced non-JSON output: {exc}"
        ) from exc
    missing = [p for p in _LOCKED_TOOLCHAIN_PACKAGES if not packages.get(p)]
    if missing:
        raise ArtifactBuildError(
            f"{venv_dir}: locked toolchain venv is missing required package(s) "
            f"{missing} -- refusing to record an incomplete toolchain lock"
        )
    return ToolchainLock(venv_python, packages)


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _read_build_system_requires(source_dir: Path) -> list[str]:
    """``source_dir``'s own declared `[build-system].requires` list --
    the constraints `uv build --no-build-isolation` can never satisfy
    itself (isolation is precisely what would otherwise let it install a
    different version), so a locked toolchain that doesn't meet them must
    be caught before the build runs, not discovered as a build failure."""
    pyproject = source_dir / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ArtifactBuildError(f"{pyproject}: could not read/parse: {exc}") from exc
    build_system = data.get("build-system", {})
    if not isinstance(build_system, dict):
        raise ArtifactBuildError(f"{pyproject}: [build-system] is not a table")
    requires = build_system.get("requires", [])
    if not isinstance(requires, list) or not all(isinstance(r, str) for r in requires):
        raise ArtifactBuildError(
            f"{pyproject}: [build-system].requires is not a list of strings"
        )
    return requires


def _assert_toolchain_satisfies_build_requires(
    source_dir: Path, toolchain: ToolchainLock
) -> None:
    """Fails closed unless the locked toolchain's exact `setuptools`/
    `wheel` versions satisfy every applicable constraint ``source_dir``
    itself declares in `[build-system].requires` -- e.g. a lagging
    governed feed resolving `setuptools` 83.x against a source that
    declares `setuptools>=84.0.0`. `--no-build-isolation` means `uv build`
    can never substitute a different version to satisfy this itself, so
    this must be checked before the build runs, not discovered as a
    mysterious build failure (or, worse, a successful build against a
    toolchain the source's own manifest says is insufficient)."""
    from packaging.requirements import InvalidRequirement, Requirement
    from packaging.version import InvalidVersion, Version

    for raw in _read_build_system_requires(source_dir):
        try:
            req = Requirement(raw)
        except InvalidRequirement as exc:
            raise ArtifactBuildError(
                f"{source_dir}: unparseable [build-system].requires entry "
                f"{raw!r}: {exc}"
            ) from exc
        if req.marker is not None and not req.marker.evaluate(
            environment=toolchain.marker_environment
        ):
            continue  # this constraint does not apply in this environment
        locked = toolchain.packages.get(req.name)
        if locked is None:
            continue  # a requirement on a package this toolchain doesn't lock
        try:
            locked_version = Version(locked)
        except InvalidVersion as exc:
            raise ArtifactBuildError(
                f"locked toolchain version {locked!r} for {req.name!r} is "
                f"not a valid version: {exc}"
            ) from exc
        if not req.specifier.contains(locked_version, prereleases=True):
            raise ArtifactBuildError(
                f"{source_dir}: locked {req.name} {locked} does not satisfy "
                f"its own declared build requirement {raw!r} -- refusing to "
                "build with --no-build-isolation against an insufficient "
                "pinned toolchain"
            )


def build_wheel(
    source_dir: Path,
    out_dir: Path,
    *,
    python: str | None = None,
    toolchain: ToolchainLock | None = None,
    reserved_names: set[str] | None = None,
) -> Path:
    """Builds a wheel for ``source_dir`` via `uv build --wheel`.

    When ``toolchain`` is given, builds with ``--no-build-isolation``
    against that one locked venv (``toolchain.venv_python``) -- the
    pinned, reproducible path every real ``build_plugin_artifacts`` call
    uses -- and ``python`` is ignored (the toolchain's own venv already
    pins the interpreter). Without a ``toolchain`` (direct callers / low-
    level tests only), falls back to `uv`'s normal per-wheel ISOLATED PEP
    517 build, resolving its build-system `requires` the normal way --
    which, on a correctly governed-feed-configured machine, already
    resolves only from that feed; this script adds no index configuration
    of its own either way.

    Builds into a fresh, empty temporary staging directory (never directly
    into ``out_dir``) and moves the single resulting wheel into ``out_dir``
    afterward: detecting a new wheel by diffing ``out_dir``'s own `*.whl`
    contents before/after would silently see no change -- and wrongly
    report "0 new wheels" -- on a second invocation that rebuilds the exact
    same filename (an identical version rebuilt again, or a retry after a
    later manifest step failed and left the wheel behind from a prior
    attempt). A fresh staging directory has no such pre-existing-name
    ambiguity: whatever `uv build` places there is unambiguously this
    invocation's own output.

    ``reserved_names``, when given, is checked BEFORE the move: a name
    already in it means two DIFFERENT sources within THIS SAME invocation
    produced the identical wheel filename -- a real collision (the earlier
    entry's manifest record and sha256 would silently describe bytes no
    longer on disk once the later wheel overwrites it) that must fail
    closed, distinct from the intentional retry-overwrite case this
    function's staging-directory design already handles (a prior
    invocation's own leftover wheel, which carries no in-progress
    ``reserved_names`` entry to collide with)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    if toolchain is not None:
        _assert_toolchain_satisfies_build_requires(source_dir, toolchain)
    with tempfile.TemporaryDirectory(prefix="build-python-artifacts-") as staging:
        staging_dir = Path(staging)
        cmd = ["uv", "build", "--wheel", "-o", str(staging_dir)]
        if toolchain is not None:
            cmd += ["--no-build-isolation", "--python", str(toolchain.venv_python)]
        elif python:
            cmd += ["--python", python]
        cmd.append(str(source_dir))
        result = subprocess.run(cmd, capture_output=True, text=True)
        if result.returncode != 0:
            raise ArtifactBuildError(
                f"uv build failed for {source_dir}:\n{result.stdout}\n{result.stderr}"
            )
        built = list(staging_dir.glob("*.whl"))
        if len(built) != 1:
            raise ArtifactBuildError(
                f"uv build for {source_dir} produced {len(built)} wheel(s) in "
                f"a fresh staging directory, expected exactly 1: "
                f"{sorted(p.name for p in built)}"
            )
        if reserved_names is not None and built[0].name in reserved_names:
            raise ArtifactBuildError(
                f"uv build for {source_dir} produced {built[0].name!r}, which "
                "another source already produced earlier in this same "
                "invocation -- a real filename collision, not a retry"
            )
        dest = out_dir / built[0].name
        shutil.move(str(built[0]), str(dest))
        if reserved_names is not None:
            reserved_names.add(dest.name)
        return dest


def read_project_version(project_dir: Path) -> str:
    """The raw, declared version string from ``project_dir``'s
    `pyproject.toml` (`[project].version`) -- NOT a wheel's PEP 440-
    normalized spelling (e.g. this repo declares `"0.4.1-dev3"`, which a
    built wheel's filename normalizes to `"0.4.1.dev3"`) -- so the
    manifest's `version` field and release-tag identity exactly match the
    `<plugin>-v<version>` form this effort's Publication channel resolution
    documents, rather than a normalized variant with no other exact match
    anywhere in the repository."""
    pyproject = project_dir / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as exc:
        raise ArtifactBuildError(f"{pyproject}: could not read/parse: {exc}") from exc
    project_table = data.get("project", {})
    if not isinstance(project_table, dict):
        raise ArtifactBuildError(f"{pyproject}: [project] is not a table")
    version = project_table.get("version")
    if not isinstance(version, str) or not version:
        raise ArtifactBuildError(
            f"{pyproject}: [project].version is missing or not a string"
        )
    return version


def _assert_version_corresponds(
    *, raw_version: str, wheel_version: str, label: str
) -> None:
    """A light sanity check that the wheel's PEP 440-normalized version
    still corresponds to the raw, declared one -- this repo's one actual
    convention is a hyphen before a pre/dev/post segment (e.g.
    `"0.4.1-dev3"` -> `"0.4.1.dev3"`); anything that doesn't match even
    that simple substitution indicates the two have genuinely diverged
    (not just a normalization spelling difference), which must fail closed
    rather than silently ship a manifest whose `version` field doesn't
    actually correspond to what was built."""
    if raw_version.replace("-", ".") != wheel_version:
        raise ArtifactBuildError(
            f"{label}: declared version {raw_version!r} does not correspond "
            f"to the built wheel's version {wheel_version!r}"
        )


def build_plugin_artifacts(
    plugin: str,
    *,
    out_dir: Path,
    python: str | None = None,
    toolchain: ToolchainLock | None = None,
) -> dict:
    """Builds the plugin's own wheel plus every vendored lib wheel it needs,
    and returns the manifest describing the whole set (also written to
    ``out_dir`` as ``<plugin>-<version>-manifest.json``).

    ``toolchain``, when given, is the ONE locked build-toolchain (see
    ``resolve_toolchain_lock``) every wheel in this call is built against --
    pass the same ``ToolchainLock`` across several ``build_plugin_artifacts``
    calls to share one toolchain lock across an entire promotion run.
    Without it, this call resolves its own disposable, single-invocation
    lock internally (using ``python`` to choose the toolchain venv's own
    interpreter) -- still a real, pinned, ``--no-build-isolation`` build,
    just not shared with any other call."""
    if not uer.is_safe_lib_name(plugin):
        # `plugin` becomes a path component twice over (`PLUGINS_DIR /
        # plugin` and the manifest filename `{plugin}-{version}-manifest
        # .json`) -- an absolute value or a `../` traversal must be
        # rejected before either, the same way a vendored-lib name already
        # is (`is_safe_lib_name` applies identically: a single path
        # component, never empty, `.`/`..`, or containing a separator).
        raise ArtifactBuildError(f"{plugin!r} is not a safe plugin name")
    plugin_dir = PLUGINS_DIR / plugin
    if not (plugin_dir / "pyproject.toml").is_file():
        raise ArtifactBuildError(f"{plugin_dir}: no pyproject.toml -- not a plugin")
    plugin_nested_symlink = uer._find_symlink(plugin_dir)
    if plugin_nested_symlink is not None:
        where = plugin_dir if plugin_nested_symlink == "." else plugin_dir / plugin_nested_symlink
        raise ArtifactBuildError(
            f"{where} is a symlink -- refusing (a plugin's own tree must "
            "contain only real files; hashing/building would otherwise "
            "silently follow it and consume bytes from outside the tree)"
        )

    vendored_libs = resolve_vendored_libs(plugin_dir)

    # Computed BEFORE any build runs: a build backend can leave residue
    # inside the source tree itself (e.g. setuptools' build_meta creating a
    # *.egg-info directory alongside the sources even for an isolated
    # wheel build), and hashing after the fact would fold build output into
    # the identity of the very input that produced it.
    all_dirs = [plugin_dir] + [d for _name, d in vendored_libs]

    out_dir_r = out_dir.resolve()
    for d in all_dirs:
        d_r = d.resolve()
        if out_dir_r == d_r or d_r in out_dir_r.parents:
            raise ArtifactBuildError(
                f"--out-dir {out_dir} is nested inside hashed source "
                f"directory {d} -- a build's own output (wheels, manifest) "
                "would then be part of its own payload hash on any retry. "
                "Choose an output directory outside every hashed source tree."
            )

    payload_hash = compute_payload_hash(all_dirs)
    out_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    wheel_infos: list[dict[str, str]] = []
    reserved_names: set[str] = set()

    with contextlib.ExitStack() as stack:
        if toolchain is None:
            toolchain_dir = Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(prefix="build-python-artifacts-toolchain-")
                )
            )
            toolchain = resolve_toolchain_lock(toolchain_dir, python=python)

        def _build_and_verify(source_dir: Path) -> tuple[Path, dict, str]:
            wheel = build_wheel(
                source_dir, out_dir, toolchain=toolchain, reserved_names=reserved_names
            )
            info = parse_wheel_filename(wheel)
            generator = read_wheel_generator(wheel)
            if generator != toolchain.generator:
                raise ArtifactBuildError(
                    f"{wheel}: built with generator {generator!r}, expected "
                    f"the locked toolchain's {toolchain.generator!r} -- the "
                    "--no-build-isolation build did not actually use the "
                    "pinned toolchain venv"
                )
            return wheel, info, generator

        plugin_wheel, plugin_info, plugin_generator = _build_and_verify(plugin_dir)
        raw_version = read_project_version(plugin_dir)
        _assert_version_corresponds(
            raw_version=raw_version, wheel_version=plugin_info["version"], label=plugin_dir
        )
        wheel_infos.append(plugin_info)
        entries.append(
            {
                "role": "plugin",
                "name": plugin_info["name"],
                "source": f"plugins/{plugin}",
                "filename": plugin_wheel.name,
                "sha256": sha256_file(plugin_wheel),
                "generator": plugin_generator,
                "python_tag": plugin_info["python_tag"],
                "abi_tag": plugin_info["abi_tag"],
                "platform_tag": plugin_info["platform_tag"],
            }
        )

        for lib_name, lib_dir in vendored_libs:
            lib_wheel, lib_info, lib_generator = _build_and_verify(lib_dir)
            wheel_infos.append(lib_info)
            rel_source = lib_dir.resolve().relative_to(REPO).as_posix()
            entries.append(
                {
                    "role": "vendored-lib",
                    "name": lib_info["name"],
                    "source": rel_source,
                    "filename": lib_wheel.name,
                    "sha256": sha256_file(lib_wheel),
                    "generator": lib_generator,
                    "python_tag": lib_info["python_tag"],
                    "abi_tag": lib_info["abi_tag"],
                    "platform_tag": lib_info["platform_tag"],
                }
            )

        identity_tags = overall_identity_tags(wheel_infos)
        wheel_fields = [
            field
            for e in sorted(entries, key=lambda e: e["filename"])
            for field in (e["filename"], e["sha256"])
        ]
        artifact_id = "sha256:" + _hash_fields(
            payload_hash,
            identity_tags["python_tag"],
            identity_tags["abi_tag"],
            identity_tags["platform_tag"],
            toolchain.lock_id,
            *wheel_fields,
        )

        manifest = {
            "schema": MANIFEST_SCHEMA,
            "schema_version": MANIFEST_SCHEMA_VERSION,
            "plugin": plugin,
            "version": raw_version,
            "python_tag": identity_tags["python_tag"],
            "abi_tag": identity_tags["abi_tag"],
            "platform_tag": identity_tags["platform_tag"],
            "payload_hash": payload_hash,
            "build_toolchain": {
                "packages": dict(sorted(toolchain.packages.items())),
                "lock_id": toolchain.lock_id,
            },
            "artifact_id": artifact_id,
            "wheels": entries,
        }
        manifest_path = out_dir / f"{plugin}-{raw_version}-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("plugin", help="plugin name under plugins/")
    ap.add_argument("--out-dir", required=True, type=Path)
    ap.add_argument(
        "--python", help="interpreter used to create the locked toolchain venv"
    )
    ap.add_argument(
        "--toolchain-venv",
        type=Path,
        help=(
            "reuse (or create) a locked build-toolchain venv at this path -- "
            "pass the SAME path across multiple invocations in one promotion "
            "run so every plugin built in that run shares one pinned "
            "setuptools/wheel toolchain; omit for a disposable, single-"
            "invocation toolchain lock"
        ),
    )
    args = ap.parse_args()
    try:
        toolchain = None
        if args.toolchain_venv is not None:
            toolchain = resolve_toolchain_lock(args.toolchain_venv, python=args.python)
        manifest = build_plugin_artifacts(
            args.plugin, out_dir=args.out_dir, python=args.python, toolchain=toolchain
        )
    except ArtifactBuildError as exc:
        print(f"build-python-artifacts: FAILED: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
