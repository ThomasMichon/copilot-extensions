#!/usr/bin/env python3
"""Phase 3 of the dev-branch-release-pipeline effort
(ThomasMichon/copilot-extensions#3336): promote ``dev``'s current,
already-CI-green state onto ``main`` as a single **generated commit** whose
tree wholesale-replaces ``main``'s -- never a merge of ``dev`` into ``main``.

The promotion, in order:

1. Check out ``dev`` into an isolated ``git worktree`` (never mutates the
   caller's real working tree).
2. Consume every pending changefile (``tools/changefile.py``) via
   ``tools/accumulate_bumps.py``'s ``compute()``/``apply()`` -- writes the
   real per-plugin version bumps and removes the changefiles that requested
   them, exactly as a contributor-driven ``--apply`` run would.
3. Expand every DRY vendor pointer (``tools/materialize_main.py``) so the
   generated ``main`` commit carries full vendored-library copies, never a
   pointer file -- ``main`` is the form every consumer's Copilot CLI
   actually installs from, and pointers are a `dev`-only compression.
4. Build a git tree object from the processed worktree and, if it differs
   from ``main``'s current tree, create a new commit whose **parent is
   ``main``'s current tip** but whose **tree is entirely `dev`'s processed
   state** -- i.e. a wholesale replace, not a three-way merge.
5. Tag the generated commit with full traceability metadata: the promoted
   ``dev`` commit range, the plugins bumped (old -> new version), and the
   changefiles consumed.

Nothing here pushes anything by default -- ``--push`` is required to move
the real ``refs/heads/main`` and push the tag to ``origin``. Without it this
tool only reports what *would* happen (the Validation Plan's required dry
run before flipping branch protection on the real ``main``).

Usage::

    python tools/promote_release.py --dry-run
    python tools/promote_release.py --push
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import subprocess
import sys
import tempfile
import time
import types
from pathlib import Path
from promotion_candidate import (
    CandidateIdentityError, candidate_clock, candidate_environment, format_promotion_message,
    push_candidate,
)

REPO = Path(__file__).resolve().parent.parent

# Never let importing the scratch worktree's own tooling modules leave
# __pycache__ directories behind to pollute the promotion tree diff.
sys.dont_write_bytecode = True

#: Where the promotion pipeline's own cross-run state lives -- a small
#: main-only artifact (pause flag + last-promotion/last-rollback record)
#: that is never part of dev's own content. It is injected directly into
#: the generated commit's tree (see ``_write_pipeline_state_into_scratch``)
#: rather than tracked in dev, so a promotion's own bookkeeping never needs
#: dev to know or carry it.
PIPELINE_STATE_PATH = ".github/release-pipeline-state.json"
PIPELINE_STATE_SCHEMA = "copilot-extensions.release-pipeline-state"
# New release tags name Dev; historical timestamp/Main-SHA tags share this prefix.
PROMOTION_TAG_PREFIX = "promote"


class PromotionError(RuntimeError):
    pass


def _git(
    args: list[str], *, cwd: Path | None = None, check: bool = True,
    env: dict[str, str] | None = None,
) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd or REPO), capture_output=True, text=True, env=env,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    if check and result.returncode != 0:
        raise PromotionError(f"git {' '.join(args)} failed: {result.stderr.strip()}")
    return result.stdout.strip()


def _rev_parse(ref: str, *, cwd: Path | None = None) -> str | None:
    try:
        return _git(["rev-parse", "--verify", ref], cwd=cwd)
    except PromotionError:
        return None


def _is_ancestor(ancestor: str, descendant: str, *, cwd: Path | None = None) -> bool:
    """True when ``ancestor`` is reachable from ``descendant`` (``git
    merge-base --is-ancestor``) -- includes the equal-commit case."""
    result = subprocess.run(
        ["git", "merge-base", "--is-ancestor", ancestor, descendant],
        cwd=str(cwd or REPO),
        capture_output=True,
        text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result.returncode == 0


def _load_module(path: Path, name: str) -> types.ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


def add_scratch_worktree(dev_ref: str, *, repo: Path = REPO) -> Path:
    """Check out ``dev_ref`` into a fresh, detached scratch worktree and
    return its path. Caller must remove it via ``remove_scratch_worktree``."""
    # git-common-dir is often printed relative to the cwd the subprocess ran
    # in (``repo``), not the caller's own process cwd -- resolve it against
    # ``repo`` explicitly rather than Python's own working directory.
    common_dir = (repo / _git(["rev-parse", "--git-common-dir"], cwd=repo)).resolve()
    scratch = common_dir.parent / ".promote-scratch" / f"promote-{int(time.time() * 1000)}"
    scratch.parent.mkdir(parents=True, exist_ok=True)
    _git(["worktree", "add", "--detach", str(scratch), dev_ref], cwd=repo)
    return scratch


def remove_scratch_worktree(scratch: Path, *, repo: Path = REPO) -> None:
    _git(["worktree", "remove", "--force", str(scratch)], cwd=repo, check=False)


def _changefile_existed_at(rev: str, name: str, *, repo: Path) -> bool:
    """True if ``.changefiles/<name>`` was already present in ``dev`` at
    ``rev`` (a real, historical commit -- typically the last promotion's own
    ``dev_head``). Uses the real repo's object store, which a scratch
    worktree shares (``git worktree add`` never creates a second store), so
    this works regardless of which checkout the caller passes."""
    result = subprocess.run(
        ["git", "cat-file", "-e", f"{rev}:.changefiles/{name}"],
        cwd=str(repo), capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    return result.returncode == 0


def _seed_versions_from_main(scratch: Path, main_head: str, *, acc, repo: Path) -> None:
    """Overwrite every plugin's version-bearing surfaces in the scratch tree
    with whatever ``main`` last actually shipped for it, BEFORE any new bump
    is computed.

    ``main`` is the SOLE source of truth for a plugin's real, shipped
    version -- ``dev``'s own ``plugin.json`` is a stale literal that
    ``promote()`` never writes back to (see this module's docstring: it
    only ever generates a NEW commit on ``main``, never mutates ``dev``).
    ``compute()``/``read_plugin_json_version()`` read "current" straight
    from whatever is checked out in ``scratch`` -- which starts as a plain
    checkout of ``dev``. Skipping this seed step would silently regress
    every plugin that gets NO new changefile in a given promotion round
    back to ``dev``'s frozen baseline in the newly generated snapshot,
    discarding whatever real version a PAST promotion already shipped.
    Confirmed live: the very first promotion after the "stop re-applying an
    already-consumed changefile" fix landed (#3542) regressed 11 of 13
    plugins on `main` back to their pre-bump versions this exact way (e.g.
    agent-bridge 0.4.1-dev1 -> 0.4.0-dev551) -- that fix was necessary but
    not sufficient on its own; this seed step is the other required half."""
    plugins_dir = scratch / "plugins"
    seed_result: dict[str, tuple[str, str]] = {}
    if plugins_dir.is_dir():
        for plugin_dir in sorted(plugins_dir.iterdir()):
            if not plugin_dir.is_dir():
                continue
            plugin = plugin_dir.name
            main_pj_raw = _git(
                ["show", f"{main_head}:plugins/{plugin}/plugin.json"], cwd=repo, check=False,
            )
            if not main_pj_raw:
                continue  # never shipped on main yet -- dev's own literal stands as-is
            try:
                main_version = json.loads(main_pj_raw)["version"]
            except (json.JSONDecodeError, KeyError):
                continue
            dev_version = acc.read_plugin_json_version(plugin)
            if dev_version is None or dev_version == main_version:
                continue
            seed_result[plugin] = (dev_version, main_version)
    # A standalone, out-of-plugin consumer (e.g. `worktree-manager`) ships
    # its own `pyproject.toml` [project].version instead of a plugin.json --
    # it needs the exact same main-is-truth seeding, or a promotion round
    # with no new changefile for it would regress it back to dev's stale
    # literal (or repeat an already-shipped bump) the same way an
    # un-seeded plugin would (PR #4514 review).
    for consumer in (
        acc.iter_standalone_consumer_names() + acc.iter_library_consumer_names()
    ):
        main_version = acc._project_version_at(main_head, f"{consumer}/pyproject.toml")
        if main_version is None:
            continue  # never shipped on main yet -- dev's own literal stands as-is
        dev_version = acc.read_consumer_version(consumer)
        if dev_version is None or dev_version == main_version:
            continue
        seed_result[consumer] = (dev_version, main_version)
    if seed_result:
        missing = set(seed_result) - set(acc.apply(seed_result))
        if missing:
            raise PromotionError(f"cannot seed shipped versions for: {', '.join(sorted(missing))}")


def _sync_instruction_projections(
    scratch: Path, *, trusted_source: Path | None = None,
) -> list[str]:
    """Re-render any declarative instruction-projection files (e.g.
    ``.github/instructions/efforts/completion-gate.instructions.md``) so
    they reflect the version bumps ``consume_pending_changes`` just applied.

    These projections embed their source plugin's version verbatim in
    rendered content (``[owner: <plugin>@<version>]``) -- a version surface
    neither ``accumulate_bumps.py`` nor ``check-version-consistency.py``
    tracks, so it silently drifted on every real promotion that bumped a
    plugin owning one until this call existed: confirmed live on `main`
    (ThomasMichon/copilot-extensions#3378 fixed the symptom once by hand;
    this closes the actual gap that let it recur, 2026-09-25). Delegates to
    the plugin's own CLI (``manage-instruction-projections.py sync``)
    rather than reimplementing its rendering/locking rules here.

    Returns the list of destination paths that changed; an empty list if
    the customizing-copilot plugin (or this script) isn't present in the
    tree at all -- this sweep is best-effort and never a hard dependency of
    promotion (a synthetic/stripped-down checkout, e.g. this module's own
    test fixtures, simply has nothing to sync)."""
    relative = (
        Path("plugins") / "customizing-copilot" / "skills" / "reviewing-customizations"
        / "scripts" / "manage-instruction-projections.py"
    )
    script = (trusted_source or scratch) / relative
    if not (scratch / relative).exists() or not script.exists():
        return []
    proc = subprocess.run(
        [sys.executable, str(script), "sync", str(scratch), "--json"],
        capture_output=True, text=True,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        payload = None
    if payload is None or proc.returncode not in (0, 1):
        raise PromotionError(
            "instruction-projection sync crashed "
            f"(exit {proc.returncode}): {proc.stderr.strip() or proc.stdout.strip()}"
        )
    if payload.get("blocking"):
        raise PromotionError(
            "instruction-projection sync reported blocking finding(s) -- "
            f"refusing to promote unsynced projections: {json.dumps(payload.get('findings', []))}"
        )
    return list(payload.get("changed", []))


def consume_pending_changes(
    scratch: Path, *, last_dev_head: str | None = None, main_head: str, repo: Path = REPO,
    trusted_source: Path | None = None,
) -> dict:
    """Compose candidate data using its tooling, or explicitly trusted tooling
    for candidate-independent signing validation. Returns a summary used for
    the tag/commit message and tests.

    ``last_dev_head`` is the ``dev`` commit the PREVIOUS promotion actually
    promoted (persisted as ``last_promotion.dev_head`` in the pipeline state
    committed on ``main`` -- see ``_read_pipeline_state``), or ``None`` for
    the very first promotion ever. Bump computation only considers a pending
    changefile that did NOT already exist in ``dev`` at that commit --
    verified by sweeping every changefile currently present and checking
    each one's presence at that exact boundary, never by keeping a
    persisted "have we ever seen this filename" list. `dev`'s own tree is
    never mutated by a promotion (see this module's docstring), so an
    already-consumed changefile can keep sitting on `dev` for a while after
    landing (the async "clear consumed changefiles on dev" cleanup PR is
    best-effort/eventual, not synchronous with promotion) -- without this
    filter, every later promotion would re-read it and recompute the exact
    same bump target from `dev`'s still-unbumped plugin.json, colliding
    with whatever real new version actually shipped in between (confirmed
    live: two consecutive real promotions both computed identical
    "agent-bridge 0.4.0-dev551 -> 0.4.1-dev1"). Every changefile physically
    present is still deleted from the scratch tree regardless -- main must
    never carry any, newly-applied or not."""
    # accumulate_bumps.py does a bare ``from changefile import
    # read_changefiles`` -- a plain import checks sys.modules by name FIRST,
    # before consulting sys.path, so if anything else in this process has
    # already imported a "changefile" module (e.g. a sibling test module,
    # or a previous promotion's own scratch copy), accumulate_bumps would
    # silently bind to *that* stale module -- reading/writing the wrong
    # repo's .changefiles/ entirely. Force the scratch worktree's own
    # changefile.py into sys.modules under its bare name before loading
    # accumulate_bumps, then restore whatever was there afterward.
    tool_root = (trusted_source or scratch) / "tools"
    changefile_mod = _load_module(tool_root / "changefile.py", "promote_changefile")
    if trusted_source is not None:
        changefile_mod.REPO = scratch
        changefile_mod.CHANGEFILES_DIR = scratch / ".changefiles"
    previous_changefile_mod = sys.modules.get("changefile")
    sys.modules["changefile"] = changefile_mod
    try:
        acc = _load_module(tool_root / "accumulate_bumps.py", "promote_accumulate_bumps")
        mm = _load_module(tool_root / "materialize_main.py", "promote_materialize_main")
        if trusted_source is not None:
            acc.REPO = scratch
            acc.PLUGINS_DIR = scratch / "plugins"
            acc.MARKETPLACE = scratch / ".github" / "plugin" / "marketplace.json"

        _seed_versions_from_main(scratch, main_head, acc=acc, repo=repo)

        all_changefiles = list(acc.read_changefiles())
        changefile_names = [p.name for p, _data in all_changefiles]
        if last_dev_head is None:
            new_entries = all_changefiles
        else:
            new_entries = [
                (path, data) for path, data in all_changefiles
                if not _changefile_existed_at(last_dev_head, path.name, repo=repo)
            ]
        newly_applied_names = [path.name for path, _data in new_entries]

        grouped: dict[str, list[str]] = {}
        for _path, data in new_entries:
            for change in data.get("changes", []):
                grouped.setdefault(change["plugin"], []).append(change["type"])
        computed = acc.compute(grouped)
        applied = acc.apply(computed) if computed else []
        unapplied = sorted(set(computed) - set(applied))
        if unapplied:
            # A computed bump that `apply()` could not actually write (a
            # manifest-format edge case `apply()` doesn't recognize, a
            # missing file, etc.) must never be silently accepted --
            # consuming the changefile below regardless would ship the OLD
            # version on `main` while permanently discarding the bump
            # intent (PR #4514 review, a recurring finding across several
            # prior rounds' individual TOML-format fixes; this is the
            # structural fix instead of another one-off format patch).
            raise PromotionError(
                f"promotion refused: computed a version bump for "
                f"{', '.join(unapplied)} but apply() could not write it -- "
                "fix the underlying manifest(s) before promoting again."
            )
        if all_changefiles:
            # Delete every changefile physically present, not just the ones
            # that drove a bump this round -- an already-consumed changefile
            # left un-deleted here would otherwise leak straight into main's
            # generated tree (main must never carry any .changefiles/*).
            acc._consume_changefiles()

        materialize_log = mm.materialize(scratch, canonical_root=scratch)
        unresolved = [line for line in materialize_log if line.startswith("SKIP")]
        if unresolved:
            raise PromotionError(
                "promotion refused: one or more vendor pointers did not "
                "resolve during materialization -- a malformed, missing, or "
                "escaping pointer would otherwise ship as an unexpanded stub "
                "in the main snapshot:\n  " + "\n  ".join(unresolved)
            )
        projections_synced = _sync_instruction_projections(scratch, trusted_source=trusted_source)
    finally:
        if previous_changefile_mod is not None:
            sys.modules["changefile"] = previous_changefile_mod
        else:
            sys.modules.pop("changefile", None)

    from promotion_control import ControlPreservationError, preserve_control

    try:
        control = preserve_control(scratch, main_head=main_head, repo=repo)
    except ControlPreservationError as exc:
        raise PromotionError(f"control preservation refused: {exc}") from exc
    summary = {
        "bumps": {p: computed[p] for p in applied},
        "changefiles_consumed": changefile_names,
        "changefiles_newly_applied": newly_applied_names,
        "projections_synced": projections_synced,
        "materialize_log": materialize_log,
    }
    if control is not None:
        summary["control_preserved"] = control
    return summary


def _read_pipeline_state(main_head: str, *, repo: Path) -> dict:
    """Read the pipeline state committed at ``main_head``, or a fresh
    default state if the commit predates this file's introduction."""
    raw = _git(
        ["show", f"{main_head}:{PIPELINE_STATE_PATH}"], cwd=repo, check=False
    )
    if not raw:
        return {
            "schema": PIPELINE_STATE_SCHEMA,
            "version": 1,
            "paused": False,
            "pause_reason": None,
            "last_promotion": None,
            "last_rollback": None,
        }
    try:
        return json.loads(raw)
    except json.JSONDecodeError as exc:
        raise PromotionError(f"pipeline state at {main_head} is not valid JSON: {exc}")


def _write_pipeline_state_into_scratch(scratch: Path, state: dict) -> None:
    path = scratch / PIPELINE_STATE_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


# Mirrors `tools/coverage_guided_selection/correlation.py`'s own
# `BASELINE_DIR_ON_MAIN` constant -- duplicated here (not imported) so this
# script's own dependency surface stays exactly what `_REQUIRED_TOOLS`
# already captures for the scratch worktree bundle, rather than growing a
# new cross-package import for the release tool. Keep both constants in
# sync if this path ever changes.
COVERAGE_BASELINES_DIR = ".github/coverage-baselines"


def _seed_coverage_baselines_from_main(scratch: Path, main_head: str, *, repo: Path) -> int:
    """Copy every existing ``COVERAGE_BASELINES_DIR/*.json`` file already
    committed on ``main`` into the scratch worktree, BEFORE any freshly
    collected baseline for this run is overlaid on top.

    Mirrors ``_seed_versions_from_main``'s own reasoning: ``scratch`` starts
    as a plain checkout of ``dev``, which has never had a baseline
    committed into it at all (baselines only ever land on `main`, via this
    very function's caller) -- so without this seed step, a promotion round
    with no freshly-collected baseline for some already-enrolled plugin
    (a transient collection/upload/download failure, or simply a plugin not
    in this run's artifact set) would wholesale-replace `main`'s tree
    *without* that plugin's last-known-good baseline at all, silently
    discarding real evidence a past promotion already recorded -- the exact
    regression class #3542 named for version numbers, recurring here for
    coverage baselines.

    Returns the count of files seeded (for the promotion report/log).
    """
    listing = _git(
        ["ls-tree", "-r", "--name-only", main_head, "--", COVERAGE_BASELINES_DIR],
        cwd=repo, check=False,
    )
    if not listing:
        return 0
    dest_dir = scratch / COVERAGE_BASELINES_DIR
    dest_dir.mkdir(parents=True, exist_ok=True)
    seeded = 0
    for path in listing.splitlines():
        raw = _git(["show", f"{main_head}:{path}"], cwd=repo, check=False)
        if not raw:
            continue
        (scratch / path).parent.mkdir(parents=True, exist_ok=True)
        (scratch / path).write_text(raw + "\n", encoding="utf-8")
        seeded += 1
    return seeded


def _release_tag_for(measured_commit: str) -> str:
    """Mirrors `tools/coverage_guided_selection/correlation.py`'s own
    `release_tag_for` -- duplicated here (not imported), same reasoning as
    `COVERAGE_BASELINES_DIR` above. Keep both in sync if this ever changes.
    Uses the full `measured_commit` SHA (never a truncated prefix) -- see
    that function's own docstring for why a shortened prefix risks a
    release-tag collision between two distinct commits.
    """
    return f"coverage-baselines-{measured_commit}"


def _write_coverage_baselines_into_scratch(
    scratch: Path, baselines_dir: Path | None, *, dev_head: str
) -> list[str]:
    """Write a small correlation **pointer** (never the full per-line
    coverage map) into the scratch worktree's own ``COVERAGE_BASELINES_DIR``
    for every ``*.json`` baseline file found in ``baselines_dir``, after
    verifying each one's own ``measured_commit`` matches ``dev_head``.

    The full per-line data is published **separately**, as a GitHub
    Release (one asset per plugin, tag `_release_tag_for(dev_head)`), by
    the caller (`validate-and-promote.yml`'s own "Publish coverage
    baselines" step) -- but only AFTER this function (and the rest of a
    real promotion) has already succeeded, never before: publishing
    first would risk a since-rejected (mismatched `measured_commit`/
    `plugin`) baseline's upload silently overwriting an already-published,
    still-valid release's assets before its own data was ever checked.
    This function itself has no `gh`/network dependency and does NOT
    verify the release actually exists at the point it runs -- a pointer
    committed ahead of its own release momentarily resolving to nothing
    is expected and transient (the caller publishes the release in the
    very next step of the same job), not a correctness gap this pure
    git/tree-
    building tool needs to check locally.

    Call ``_seed_coverage_baselines_from_main`` first (see its own
    docstring) so this only ever OVERLAYS this run's freshly collected
    pointers on top of whatever `main` already has, never replaces the
    whole directory.

    The promotion's own ``full`` matrix jobs already embed ``measured_commit``
    when collecting (see ``coverage_guided_selection.baseline.collect_baseline``'s
    own ``measured_commit`` parameter); a mismatch here means a stale or
    mis-targeted artifact was downloaded (e.g. a leftover from a prior,
    already-superseded run), and must fail the promotion loudly rather than
    be silently checked in as if it were this promotion's own evidence --
    the same "the correlation loop never silently breaks" Behavior the
    vision requires.

    Returns the sorted list of plugin names actually written (for the
    promotion report/log), or an empty list if ``baselines_dir`` is
    ``None``/absent/empty -- coverage-baseline collection is optional, and a
    promotion with none attached is still a valid promotion, just without
    this evidence (e.g. before the `full` matrix is instrumented for every
    plugin).
    """
    if baselines_dir is None or not baselines_dir.is_dir():
        return []
    dest_dir = scratch / COVERAGE_BASELINES_DIR
    written: list[str] = []
    for src in sorted(baselines_dir.glob("*.json")):
        data = json.loads(src.read_text())
        measured_commit = data.get("measured_commit")
        if measured_commit != dev_head:
            raise PromotionError(
                f"coverage baseline {src.name!r} was measured against "
                f"{measured_commit!r}, not this promotion's own dev head "
                f"{dev_head!r} -- refusing to check in a mismatched baseline"
            )
        plugin = data["plugin"] if "plugin" in data else src.stem
        if plugin != src.stem:
            raise PromotionError(
                f"coverage baseline {src.name!r} embeds plugin {plugin!r}, "
                f"which does not match its own file name -- refusing to "
                f"write a pointer whose asset name would not match the "
                f"file it was actually collected as"
            )
        pointer = {
            "schema": "copilot-extensions.coverage-baseline-pointer",
            "plugin": plugin,
            "measured_commit": measured_commit,
            "release_tag": _release_tag_for(measured_commit),
            "asset": f"{plugin}.json",
        }
        dest_dir.mkdir(parents=True, exist_ok=True)
        (dest_dir / src.name).write_text(
            json.dumps(pointer, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        written.append(src.stem)
    return written


class PromotionPaused(PromotionError):
    pass


class NonIncrementalPromotion(PromotionError):
    pass


class StaleCandidatePromotion(PromotionError):
    pass


def _remove_pycache(root: Path) -> None:
    """Delete any ``__pycache__`` directories left by importing the scratch
    worktree's own tooling modules (importlib bytecode-caches next to the
    ``.py`` files it loads) -- these must never appear in the promoted
    tree's diff against ``main``."""
    for cache_dir in root.rglob("__pycache__"):
        if cache_dir.is_dir():
            import shutil as _shutil
            _shutil.rmtree(cache_dir, ignore_errors=True)


def build_promotion_tree(scratch: Path, *, control: dict | None = None) -> str:
    _remove_pycache(scratch)
    _git(["add", "-A"], cwd=scratch)
    if control is not None:
        # Stage pinned blobs after checkout filters and core.filemode handling.
        for path, blob in control["files"].items():
            _git(["update-index", "--add", "--cacheinfo", f"100644,{blob},{path}"], cwd=scratch)
    return _git(["write-tree"], cwd=scratch)


def _tree_excluding_state(tree_sha: str, *, repo: Path) -> str:
    """Return a tree identical to ``tree_sha`` but with ``PIPELINE_STATE_PATH``
    and ``COVERAGE_BASELINES_DIR`` removed, via a throwaway index -- so
    comparing it against the scratch worktree's own (state/baseline-free,
    since both are only written *after* this comparison -- see ``promote()``)
    tree is a fair, content-only comparison that never treats a mere
    bookkeeping update (pipeline state) or a freshly re-collected coverage
    baseline with no real code change behind it as a real promotion."""
    with tempfile.TemporaryDirectory() as tmp:
        index_file = Path(tmp) / "index"
        env = {**os.environ, "GIT_INDEX_FILE": str(index_file)}
        subprocess.run(
            ["git", "read-tree", tree_sha], cwd=str(repo), env=env,
            check=True, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        subprocess.run(
            ["git", "rm", "--cached", "-f", "-q", "--ignore-unmatch", PIPELINE_STATE_PATH],
            cwd=str(repo), env=env, check=True, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        subprocess.run(
            ["git", "rm", "--cached", "-f", "-q", "-r", "--ignore-unmatch",
             COVERAGE_BASELINES_DIR],
            cwd=str(repo), env=env, check=True, capture_output=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        result = subprocess.run(
            ["git", "write-tree"], cwd=str(repo), env=env,
            capture_output=True, text=True, check=True,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        return result.stdout.strip()


def promote(
    *,
    repo: Path = REPO,
    dev_ref: str = "origin/dev",
    main_ref: str = "origin/main",
    push: bool = False,
    force: bool = False,
    tag_prefix: str = PROMOTION_TAG_PREFIX,
    candidate_branch: str | None = None,
    coverage_baselines_dir: Path | None = None,
    build_python_artifacts_dir: Path | None = None,
    python_artifacts_dir: Path | None = None,
    toolchain_source: str = "governed",
    toolchain_min_age_days: int | None = None,
    promotion_time: str | None = None,
    qualification_only: bool = False,
    reuse_artifacts_repository: str | None = None,
    prepare_candidate_dir: Path | None = None,
    frozen_candidate_dir: Path | None = None,
    expected_candidate_sha: str | None = None,
) -> dict:
    """Run one full promotion cycle. Returns a report dict; never raises for
    "nothing to promote" (reported via ``report["promoted"] is False``).

    ``candidate_branch``, when given, changes what ``push=True`` actually
    does: instead of pushing the generated commit directly to ``main_ref``'s
    branch (which a real, protected ``main`` will reject -- a personal
    GitHub account's branch rulesets have no way to grant a bypass to the
    GitHub Actions app the way an organization's can), the commit is pushed
    to ``refs/heads/<candidate_branch>`` and the caller
    (``validate-and-promote.yml``, via ``gh pr create``/``gh pr merge``) is
    responsible for landing it on
    ``main`` through a real pull request -- the same sanctioned path every
    other change to this repo already uses. The tag is intentionally left
    unpushed in that mode: its target should be the real post-merge commit
    on ``main``, not this pre-merge candidate, whose sha a squash-merge
    replaces. Without ``candidate_branch``, the original direct-push
    behavior is unchanged (used by the test suite and any trusted/
    unprotected repo).

    ``coverage_baselines_dir``, when given, is a local directory of
    pre-collected ``<plugin>.json`` coverage-baseline files (see
    ``coverage_guided_selection.baseline.collect_baseline``) this
    promotion reads a small correlation **pointer** from and checks THAT
    (never the full file) into the generated commit's own tree at
    ``COVERAGE_BASELINES_DIR`` -- see
    ``_write_coverage_baselines_into_scratch``'s own docstring for the
    ``measured_commit`` consistency check this performs before accepting
    any of them, and for the full per-line data's own publication contract
    (a GitHub Release asset the caller publishes separately, only AFTER
    this promotion itself succeeds). Written only after the no-op content
    check (alongside the pipeline state file), so a baseline update alone
    -- with no real `dev` content change -- never forces a vacuous
    promotion."""
    dev_head = _rev_parse(dev_ref, cwd=repo)
    if expected_candidate_sha is not None and frozen_candidate_dir is None:
        raise PromotionError("an expected candidate SHA requires the frozen-candidate path")
    if prepare_candidate_dir is not None and (
        push or build_python_artifacts_dir is not None or python_artifacts_dir is not None
        or frozen_candidate_dir is not None
    ):
        raise PromotionError("candidate freezing must precede builds and publication")
    if qualification_only and push and not candidate_branch:
        raise PromotionError("qualification-only publication requires the normal candidate PR path")
    if build_python_artifacts_dir is not None and (
        push or python_artifacts_dir is not None
    ):
        raise PromotionError("artifact export must be a separate, non-pushing preparation")
    if dev_head is None:
        raise PromotionError(f"cannot resolve dev ref: {dev_ref!r}")
    main_head = _rev_parse(main_ref, cwd=repo)
    if main_head is None:
        raise PromotionError(f"cannot resolve main ref: {main_ref!r}")
    main_tree = _git(["rev-parse", f"{main_head}^{{tree}}"], cwd=repo)

    state = _read_pipeline_state(main_head, repo=repo)
    if state.get("paused") and not qualification_only:
        raise PromotionPaused(
            "promotion is paused: "
            f"{state.get('pause_reason') or '(no reason recorded)'} -- "
            "run tools/rollback_release.py resume once safe to continue"
        )
    last_rollback = state.get("last_rollback")
    if last_rollback and not force and last_rollback.get("reverted_dev_head") == dev_head:
        raise NonIncrementalPromotion(
            f"refusing to re-promote dev@{dev_head[:12]}: this exact dev state "
            "was already rolled back "
            f"({last_rollback.get('reason') or 'no reason recorded'}). "
            "dev must move forward with an actual fix before promoting again "
            "-- or pass --force to override this guard deliberately."
        )
    last_promotion = state.get("last_promotion") or {}
    last_dev_head = last_promotion.get("dev_head")
    if last_dev_head and last_dev_head != dev_head and not _is_ancestor(last_dev_head, dev_head, cwd=repo):
        raise StaleCandidatePromotion(
            f"refusing to promote dev@{dev_head[:12]}: it is not a descendant "
            f"of the already-promoted dev@{last_dev_head[:12]} -- this looks "
            "like an out-of-order/stale candidate (a newer dev commit was "
            "already promoted while this one was still validating). No "
            "action needed: dev has already moved forward past this "
            "candidate's own content."
        )
    if frozen_candidate_dir is not None:
        from promotion_frozen_candidate import publish_frozen

        if prepare_candidate_dir is not None or build_python_artifacts_dir is not None:
            raise PromotionError("a frozen candidate cannot be recomposed")
        if expected_candidate_sha is None:
            raise PromotionError("frozen publication requires the admitted composition SHA")
        return publish_frozen(
            frozen_candidate_dir, repo=repo, dev_head=dev_head, main_head=main_head,
            promotion_time=promotion_time, python_artifacts_dir=python_artifacts_dir,
            push=push, candidate_branch=candidate_branch,
            candidate_sha=expected_candidate_sha,
        )
    scratch = add_scratch_worktree(dev_head, repo=repo)
    try:
        summary = consume_pending_changes(
            scratch,
            last_dev_head=last_promotion.get("dev_head"),
            main_head=main_head,
            repo=repo,
            trusted_source=REPO,
        )
        content_tree = build_promotion_tree(scratch, control=summary.get("control_preserved"))
        main_content_tree = _tree_excluding_state(main_tree, repo=repo)

        if content_tree == main_content_tree and last_dev_head in (None, dev_head):
            return {"promoted": False, "reason": "no content change vs. main", **summary}

        from promotion_python_artifacts import (
            PromotionArtifactError, export_artifacts, validate_composed_artifacts,
        )

        if python_artifacts_dir is not None:
            try:
                validate_composed_artifacts(
                    python_artifacts_dir, scratch=scratch, content_tree=content_tree,
                    dev_head=dev_head, main_before=main_head,
                )
            except PromotionArtifactError as exc:
                raise PromotionError(str(exc)) from exc

        base = _git(["merge-base", main_head, dev_head], cwd=repo, check=False) or main_head
        message = format_promotion_message(dev_range=(base, dev_head), summary=summary)
        prepared_at, stamp = candidate_clock(promotion_time)
        tag_stem = f"{tag_prefix}-{stamp}"
        source_tag = f"{tag_prefix}-dev-{dev_head}"

        new_state = dict(state)
        new_state["last_promotion"] = {
            "dev_head": dev_head,
            "main_before": main_head,
            "tag": source_tag,
            "release_id": dev_head,
            "promoted_at": prepared_at,
        }
        if "control_preserved" in summary:
            new_state["last_promotion"]["control_preserved"] = summary["control_preserved"]
        _write_pipeline_state_into_scratch(scratch, new_state)
        _seed_coverage_baselines_from_main(scratch, main_head, repo=repo)
        baselines_written = _write_coverage_baselines_into_scratch(
            scratch, coverage_baselines_dir, dev_head=dev_head
        )
        new_tree = build_promotion_tree(scratch, control=summary.get("control_preserved"))

        identity = (
            ["-c", "commit.gpgsign=false", "-c", "i18n.commitEncoding=UTF-8"]
            if promotion_time is not None else []
        )
        commit = _git(
            [*identity, "commit-tree", new_tree, "-p", main_head, "-m", message],
            cwd=scratch, env=candidate_environment(promotion_time),
        )
        if build_python_artifacts_dir is not None:
            try:
                export_artifacts(
                    repo=repo, commit=commit, content_tree=content_tree,
                    dev_head=dev_head, main_before=main_head,
                    out_dir=build_python_artifacts_dir, source_policy=toolchain_source,
                    min_age_days=toolchain_min_age_days,
                    reuse_repository=reuse_artifacts_repository,
                )
            except PromotionArtifactError as exc:
                raise PromotionError(str(exc)) from exc
        tag_name: str | None = source_tag

        if push and candidate_branch:
            # The landed Git container is a receipt, not another release key.
            tag_name = None
            push_candidate(commit, candidate_branch, cwd=scratch, git=_git)
        else:
            if build_python_artifacts_dir is None and prepare_candidate_dir is None and not qualification_only:
                from tag_promotion import _same_source_release

                existing = _git(
                    ["rev-parse", "-q", "--verify", f"refs/tags/{tag_name}^{{commit}}"],
                    cwd=scratch, check=False,
                )
                if existing and not _same_source_release(existing, commit, dev_head, repo=repo):
                    raise PromotionError("existing source release tag binds a different source/composition")
                if not existing:
                    _git(["tag", "-a", tag_name, "-m", message, commit], cwd=scratch)
            else:
                # Artifact preparation never tags (and never pushes; see the
                # guard at the top), so there is no tag name to report.
                tag_name = None
            if push:
                _git(["push", "origin", f"{commit}:refs/heads/main"], cwd=scratch)
                _git(["push", "origin", tag_name], cwd=scratch)

        report = {
            "promoted": True,
            "commit": commit,
            "tag": tag_name,
            "tag_stem": tag_stem,
            "source_tag": source_tag,
            "main_before": main_head,
            "dev_head": dev_head,
            "pushed": push,
            "candidate_branch": candidate_branch if (push and candidate_branch) else None,
            "coverage_baselines_written": baselines_written,
            "python_artifacts_exported": build_python_artifacts_dir is not None,
            **summary,
        }
        if prepare_candidate_dir is not None:
            if push or build_python_artifacts_dir is not None:
                raise PromotionError("candidate freezing must precede builds and publication")
            from promotion_frozen_candidate import export_candidate

            export_candidate(
                prepare_candidate_dir, repo=repo, report=report,
                content_tree=content_tree, prepared_at=prepared_at,
            )
        return report
    except CandidateIdentityError as exc:
        raise PromotionError(str(exc)) from exc
    finally:
        remove_scratch_worktree(scratch, repo=repo)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--repo", type=Path, default=REPO, help="repo root (default: this checkout)")
    ap.add_argument("--dev-ref", default="origin/dev")
    ap.add_argument("--main-ref", default="origin/main")
    ap.add_argument("--github-output", type=Path, help="append the exact pushed candidate SHA")
    ap.add_argument("--promotion-time", help="immutable run-created UTC time for exact candidate retries")
    ap.add_argument(
        "--force", action="store_true",
        help="override the non-incremental-promotion guard after a rollback",
    )
    ap.add_argument(
        "--qualification-only", action="store_true",
        help="qualify a replacement while preserving the pause; never push Main directly",
    )
    ap.add_argument(
        "--candidate-branch", default=None,
        help="push the candidate commit here instead of main directly, for a "
             "caller (validate-and-promote.yml) to land via a real PR + merge",
    )
    ap.add_argument(
        "--coverage-baselines-dir", type=Path, default=None,
        help="directory of pre-collected <plugin>.json coverage-baseline files "
             "(coverage_guided_selection.baseline.collect_baseline) to check "
             "into this promotion's own commit at COVERAGE_BASELINES_DIR",
    )
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="default: do not push")
    mode.add_argument("--push", action="store_true", help="push the generated commit + tag")
    artifacts = ap.add_mutually_exclusive_group()
    artifacts.add_argument("--build-python-artifacts-dir", type=Path)
    artifacts.add_argument("--python-artifacts-dir", type=Path)
    ap.add_argument("--prepare-candidate-dir", type=Path)
    ap.add_argument("--frozen-candidate-dir", type=Path)
    ap.add_argument("--expected-candidate-sha")
    ap.add_argument("--reuse-artifacts-repository", help="reuse the admitted immutable development artifact set")
    ap.add_argument(
        "--toolchain-source", choices=("governed", "seasoned-public"), default="governed"
    )
    ap.add_argument("--toolchain-min-age-days", type=int, default=None)
    args = ap.parse_args(argv)
    if args.github_output is not None and not (args.push and args.candidate_branch):
        ap.error("--github-output requires --push and --candidate-branch")
    if args.build_python_artifacts_dir is not None and args.push:
        ap.error("build artifacts without --push, in a credential-separated job")
    if args.toolchain_min_age_days is not None and args.toolchain_source != "seasoned-public":
        ap.error("--toolchain-min-age-days requires --toolchain-source seasoned-public")

    try:
        report = promote(
            repo=args.repo, dev_ref=args.dev_ref, main_ref=args.main_ref,
            push=args.push, force=args.force, candidate_branch=args.candidate_branch,
            qualification_only=args.qualification_only,
            reuse_artifacts_repository=args.reuse_artifacts_repository,
            coverage_baselines_dir=args.coverage_baselines_dir,
            build_python_artifacts_dir=args.build_python_artifacts_dir,
            python_artifacts_dir=args.python_artifacts_dir,
            prepare_candidate_dir=args.prepare_candidate_dir,
            frozen_candidate_dir=args.frozen_candidate_dir,
            expected_candidate_sha=args.expected_candidate_sha,
            toolchain_source=args.toolchain_source,
            toolchain_min_age_days=args.toolchain_min_age_days,
            promotion_time=args.promotion_time,
        )
    except PromotionPaused as exc:
        # A pause is an expected, intentional operator action (part of the
        # rollback procedure), not a pipeline failure -- exit 0 so a CI run
        # reports this as a normal no-op rather than a red build.
        print(f"promote-release: {exc}")
        return 0
    except StaleCandidatePromotion as exc:
        # Also a normal, expected outcome under out-of-order dispatch
        # racing (see the guard's own comment in promote()) -- dev has
        # already moved forward past this candidate via a different,
        # already-completed promotion run. Nothing is wrong and nothing
        # needs fixing; exit 0 rather than failing report-failure's
        # watchdog into filing a spurious incident for benign behavior.
        print(f"promote-release: {exc}")
        return 0
    except PromotionError as exc:
        print(f"promote-release: {exc}", file=sys.stderr)
        return 1

    if not report["promoted"]:
        print(f"promote-release: {report['reason']}; nothing to do.")
        return 0

    if report["tag"]:
        print(f"promote-release: generated commit {report['commit']} (tag {report['tag']})")
    elif report.get("python_artifacts_exported"):
        print(
            f"promote-release: prepared commit {report['commit']} for artifact export "
            f"(not tagged; source release {report['source_tag']})"
        )
    else:
        print(
            f"promote-release: generated commit {report['commit']} "
            f"(the qualified landing is recorded as {report['source_tag']})"
        )
    print(f"  main before: {report['main_before']}")
    print(f"  dev head:    {report['dev_head']}")
    for plugin, (old, new) in sorted(report["bumps"].items()):
        print(f"  bump: {plugin} {old} -> {new}")
    for name in report.get("changefiles_consumed", []):
        # Diagnostic log line: every changefile this promotion consumed (a
        # bump input, or one already landed by an earlier promotion but
        # still physically present on dev -- see consume_pending_changes()'s
        # own docstring). `purge-consumed-changefiles.yml` independently
        # re-derives its own safe-to-delete set from `main`'s persisted
        # `last_promotion.dev_head`, so nothing greps this line any more;
        # it's for human/log visibility only.
        print(f"  changefile-consumed: {name}")
    for path in report.get("projections_synced", []):
        # Confirms the version-drift sweep (#3378-recurrence) actually
        # re-rendered something this run, not just ran silently.
        print(f"  projection-synced: {path}")
    for plugin in report.get("coverage_baselines_written", []):
        print(f"  coverage-baseline-checked-in: {plugin}")
    if report.get("python_artifacts_exported"):
        print("  python-artifacts-exported: true")
    if report.get("candidate_branch"):
        if args.github_output is not None:
            with args.github_output.open("a", encoding="utf-8") as workflow_output:
                workflow_output.write(f"candidate_sha={report['commit']}\n")
        print(f"  pushed candidate branch: {report['candidate_branch']} "
              "(land it on main via a real PR + merge; tag the actual merged commit)")
    elif not report["pushed"]:
        print("  (dry run -- nothing pushed; re-run with --push to update origin/main)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
