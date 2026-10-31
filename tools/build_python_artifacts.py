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

**Manifest schema note (version 3):** ``build_toolchain`` is a structured
record containing ``packages``, ``lock_id``, and mandatory ``provenance``
(source policy and resolved interpreter; public policy also binds age,
pins, and eligible file hashes), describing the one locked toolchain
every wheel in the set was built against -- not schema version 1's ad hoc
list of per-wheel ``Generator:`` strings gathered after the fact.
``packaging`` is locked alongside ``setuptools``/``wheel`` so this tool's
own ``[build-system].requires`` verification can run inside the locked,
policy-selected venv rather than depending on ``packaging`` being
importable in whatever process runs this tool. Resolved locks also record
source policy and interpreter provenance, both included in ``lock_id``.
The default remains governed-only. Public promotion runners may explicitly
select ``--toolchain-source seasoned-public --toolchain-min-age-days 7``;
this build-tooling exception never applies to consumer runtime dependencies.

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

import contextlib
import hashlib
import json
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile
from pathlib import Path
from artifact_source_build import PromotionArtifactError, parse_wheel_identity

sys.path.insert(0, str(Path(__file__).resolve().parent))
import uv_editable_ref as uer  # noqa: E402
from build_toolchain_lock import (  # noqa: E402
    ArtifactBuildError,
    ToolchainLock,
    _assert_toolchain_satisfies_build_requires,
    _hash_fields,
    _query_marker_environment,  # noqa: F401 -- re-exported for test/caller use
    _governed_feed_configured,  # noqa: F401 -- re-exported for test/caller use
    _venv_python_path,  # noqa: F401 -- re-exported for test/caller use
    _TRUSTED_INDEX_HOSTS_ENV_VAR,  # noqa: F401 -- re-exported for test/caller use
    resolve_toolchain_lock,
    sanitize_subprocess_env,
    strip_package_source_env_vars,
)

try:  # tomllib is stdlib on 3.11+; tomli backports it for this repo's
    # 3.10 support floor -- mirrors uv_editable_ref's own identical fallback.
    import tomllib
except ModuleNotFoundError:  # pragma: no cover - exercised only on 3.10
    import tomli as tomllib

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
LIBS_DIR = REPO / "libs"
MANIFEST_SCHEMA = "copilot-extensions.python-artifact-manifest"
MANIFEST_SCHEMA_VERSION = 3

_GENERATOR_RE = re.compile(r"^Generator:\s*(.+?)\s*$", re.MULTILINE)

# Tags considered "universal" (compatible everywhere) -- any other tag is
# strictly more specific and wins when picking the artifact set's own
# overall platform/python identity (see `_more_specific`).
_UNIVERSAL_TAGS = {"none", "any", "py3", "py2.py3"}


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
    copy must NOT have). Still requires the materializer's accepted Python
    source layout, `pyproject.toml`, and a symlink-free tree, so it never builds
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
    if not uer.has_canonical_python_sources(canonical):
        raise ArtifactBuildError(
            f"{label}: {canonical}/src does not exist and no valid flat py-modules are declared"
        )
    if not (canonical / "pyproject.toml").is_file():
        raise ArtifactBuildError(f"{label}: {canonical}/pyproject.toml is missing")
    nested = uer._find_symlink(canonical, skip_test_runs=False)
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
    """Full acceptance check for one escaping canonical reference, including
    explicitly retained flat modules -- the same rules
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
    if not uer.has_canonical_python_sources(canonical):
        raise ArtifactBuildError(
            f"{consumer_dir}: {name} -> {raw_path} (resolved {canonical}): "
            "src does not exist and no valid flat py-modules are declared"
        )
    if not (canonical / "pyproject.toml").is_file():
        raise ArtifactBuildError(
            f"{consumer_dir}: {name} -> {raw_path} (resolved {canonical}): "
            "pyproject.toml is missing"
        )
    nested = uer._find_symlink(canonical, skip_test_runs=False)
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
    """Discover the materializer's complete recursive vendored-lib closure.

    Accept escaping editable canonical references, explicitly retained flat
    canonical sources, and noneditable in-tree/sibling copies. Validate the
    top-level consumer with the shared materializer policy, then validate each
    nested reference individually: a legitimate sibling reference need not
    escape to top-level ``libs`` or carry ``editable = true``.

    Every library must retain the same declared Python layout, metadata and
    containment guarantees before and after materialization. Return sorted
    ``(lib_name, canonical_dir)`` pairs, refusing ambiguous same-name roots.
    """
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
            problems = uer.uv_editable_problems(consumer_label, current, repo=REPO)
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
                if name in in_tree_names:
                    continue
                if not (
                    uer.is_safe_lib_name(lib)
                    and uer.is_retained_flat_ref(current, name, LIBS_DIR / lib)
                ):
                    raise ArtifactBuildError(
                        f"{current}: {name} -> {raw_path} is not editable = "
                        "true and does not resolve to an allowed in-tree "
                        "vendored-lib location -- refusing to silently omit "
                        "it from the artifact set"
                    )
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
    ".mypy_cache", ".ruff_cache", ".test-runs",
}
_PAYLOAD_IGNORE_SUFFIXES = (".pyc", ".pyo")


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
        if ".test-runs" in p.relative_to(d).parts:
            continue
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
    """Keep the builder's public parser and error type over the pure shared grammar."""
    return parse_wheel_identity(path, error_type=ArtifactBuildError, numeric_version=True)


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


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return f"sha256:{h.hexdigest()}"


def _build_child(command: list[str], environment: dict[str, str], deadline: float | None):
    if deadline is None:
        return subprocess.run(
            command, capture_output=True, text=True, env=environment,
            creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
        )
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(command, 0)
    previous = sys.path.copy()
    try:
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "agent-procutil" / "src"))
        sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
        import agent_procutil
        from installer_engine_process import run_owned
    finally:
        sys.path[:] = previous
    with tempfile.TemporaryFile() as ownership:
        try:
            return run_owned(
                command, cwd=Path.cwd(), env=environment, timeout=remaining, deadline=deadline,
                procutil=agent_procutil, construction_fd=ownership.fileno(),
            )
        except subprocess.CalledProcessError as error:
            return subprocess.CompletedProcess(command, error.returncode, error.stdout, error.stderr)


def build_wheel(
    source_dir: Path,
    out_dir: Path,
    *,
    python: str | None = None,
    toolchain: ToolchainLock | None = None,
    reserved_names: set[str] | None = None,
    offline: bool = False,
    uv: str = "uv",
    deadline: float | None = None,
    procutil=None,
    construction_fd: int | None = None,
    retained_runtime: tuple[Path, str, Path] | None = None,
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
    of its own either way. The explicit ``offline`` consumer path requires
    an already admitted toolchain and retained runtime custody. It disables
    indexes, configuration discovery, caches and Python downloads; requirement
    checks and the build share the caller's deadline and owned-child scope.

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
    run = None
    if offline:
        if (
            toolchain is None or deadline is None or procutil is None
            or construction_fd is None or retained_runtime is None
        ):
            raise ArtifactBuildError("Offline source builds require retained toolchain and child custody")
        from installer_dependency_policy import _select_uv_executable
        from installer_engine_process import run_owned
        from installer_wheel_inputs import _check_deadline

        _check_deadline(deadline)
        uv = _select_uv_executable(uv)
        build_env = sanitize_subprocess_env()
        strip_package_source_env_vars(build_env, strip_credentials=True)

        def run(argv):
            # UV receives its source explicitly; Windows cannot launch with a
            # deeply retained source directory as the process working directory.
            return run_owned(
                argv, cwd=out_dir, env=build_env, timeout=deadline - time.monotonic(),
                deadline=deadline, procutil=procutil, construction_fd=construction_fd,
                runtime_root=retained_runtime[0], runtime_version=retained_runtime[1],
                runtime_path=retained_runtime[2],
            )

    out_dir.mkdir(parents=True, exist_ok=True)
    if toolchain is not None:
        options = {"deadline": deadline} if deadline is not None else {}
        if run is not None:
            options["run"] = run
        _assert_toolchain_satisfies_build_requires(source_dir, toolchain, **options)
    with tempfile.TemporaryDirectory(prefix="build-python-artifacts-") as staging:
        staging_dir = Path(staging)
        cmd = [uv, "build", "--wheel", "-o", str(staging_dir)]
        if offline:
            cmd += ["--offline", "--no-index", "--no-config", "--no-cache",
                    "--no-sources", "--no-python-downloads"]
        if toolchain is not None:
            cmd += ["--no-build-isolation", "--python", str(toolchain.venv_python)]
        elif python:
            cmd += ["--python", python]
        cmd.append(str(source_dir))
        build_env = sanitize_subprocess_env()
        if toolchain is not None:
            # `--no-build-isolation` means this build needs NO index
            # access at all -- the locked toolchain venv already has
            # everything it needs -- yet the build BACKEND executes
            # arbitrary code from `source_dir`, which would otherwise
            # still observe an ambient credentialed index URL or named-
            # index credential env var. Unlike `resolve_toolchain_lock`'s
            # own install call (which deliberately keeps named-index
            # credential variables so it can authenticate), this build
            # subprocess has no such need and strips them too.
            strip_package_source_env_vars(build_env, strip_credentials=True)
        result = run(cmd) if run is not None else _build_child(cmd, build_env, deadline)
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
    """Accept the repository's hyphen-to-dot PEP 440 spelling convention only."""
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
    plugin_nested_symlink = uer._find_symlink(plugin_dir, skip_test_runs=False)
    if plugin_nested_symlink is not None:
        where = plugin_dir if plugin_nested_symlink == "." else plugin_dir / plugin_nested_symlink
        raise ArtifactBuildError(
            f"{where} is a symlink -- refusing (a plugin's own tree must "
            "contain only real files; hashing/building would otherwise "
            "silently follow it and consume bytes from outside the tree)"
        )

    vendored_libs = resolve_vendored_libs(plugin_dir)
    if plugin == "agent-containers":
        from plugin_runtime_resources import build_inputs
        try:
            build_inputs(plugin_dir, root=REPO)
        except PromotionArtifactError as exc:
            raise ArtifactBuildError(f"invalid Containers runtime build inputs: {exc}") from exc

    return build_source_artifacts(
        identity={"plugin": plugin}, source_dir=plugin_dir, dependencies=vendored_libs,
        out_dir=out_dir, python=python, toolchain=toolchain,
    )


def build_source_artifacts(
    *, identity: dict, source_dir: Path, dependencies: list[tuple[str, Path]],
    out_dir: Path, python: str | None, toolchain: ToolchainLock | None,
    resources: tuple[Path, ...] = (),
) -> dict:
    """Shared locked wheel construction; callers own source discovery and admission."""
    standalone = "component" in identity
    return build_project_artifacts(
        identity=identity,
        projects=[("standalone" if standalone else "plugin", source_dir), *(
            ("vendored-lib", directory) for _name, directory in dependencies
        )],
        raw_version=read_project_version(source_dir),
        schema_version=4 if standalone else MANIFEST_SCHEMA_VERSION,
        resources=resources, out_dir=out_dir, python=python, toolchain=toolchain,
    )


def build_project_artifacts(
    *, identity: dict, projects: list[tuple[str, Path]], raw_version: str,
    schema_version: int, resources: tuple[Path, ...], out_dir: Path,
    python: str | None, toolchain: ToolchainLock | None,
    selection: dict | None = None, payload_hash: str | None = None,
) -> dict:
    """One locked construction path for plugin, standalone and core wheel sets."""
    from standalone_python_artifacts import artifact_label
    label = artifact_label(identity)
    # Hash before the backend can leave build/egg-info residue in its inputs.
    all_dirs = [*(directory for _role, directory in projects), *resources]

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

    if payload_hash is None:
        payload_hash = compute_payload_hash(all_dirs)
    out_dir.mkdir(parents=True, exist_ok=True)

    entries: list[dict] = []
    wheel_infos: list[dict[str, str]] = []
    reserved_names: set[str] = set()

    with contextlib.ExitStack() as stack:
        if toolchain is None:
            # Windows rename cannot publish onto an existing directory.
            # Resolve into a nonexistent child of the temporary cleanup root.
            toolchain_root = Path(
                stack.enter_context(
                    tempfile.TemporaryDirectory(prefix="build-python-artifacts-toolchain-")
                )
            )
            toolchain = resolve_toolchain_lock(toolchain_root / "venv", python=python)

        toolchain_record = toolchain.record
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

        for role, lib_dir in projects:
            lib_wheel, lib_info, lib_generator = _build_and_verify(lib_dir)
            _assert_version_corresponds(
                raw_version=read_project_version(lib_dir), wheel_version=lib_info["version"],
                label=lib_dir,
            )
            wheel_infos.append(lib_info)
            rel_source = lib_dir.resolve().relative_to(REPO).as_posix()
            entries.append(
                {
                    "role": role,
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
            "schema_version": schema_version,
            **identity,
            "version": raw_version,
            "python_tag": identity_tags["python_tag"],
            "abi_tag": identity_tags["abi_tag"],
            "platform_tag": identity_tags["platform_tag"],
            "payload_hash": payload_hash,
            "build_toolchain": toolchain_record,
            "artifact_id": artifact_id,
            "wheels": entries,
        }
        if "component" in identity or "composition" in identity:
            manifest["build_resources"] = [
                path.resolve().relative_to(REPO.resolve()).as_posix() for path in resources
            ]
        if selection is not None:
            manifest["selection"] = selection
        manifest_path = out_dir / f"{label}-{raw_version}-manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return manifest


def main() -> int:
    from artifact_builder_cli import main as run
    from standalone_python_artifacts import build_standalone_artifacts
    def build_core_artifacts(**kwargs):
        from core_python_artifacts import build_core_artifacts as build
        return build(**kwargs)
    return run(
        build_plugin_artifacts, __doc__ or "", standalone_builder=build_standalone_artifacts,
        core_builder=build_core_artifacts,
    )


if __name__ == "__main__":
    sys.exit(main())
