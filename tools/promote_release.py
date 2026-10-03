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


class PromotionError(RuntimeError):
    pass


def _git(args: list[str], *, cwd: Path | None = None, check: bool = True) -> str:
    result = subprocess.run(
        ["git", *args], cwd=str(cwd or REPO), capture_output=True, text=True
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
    for consumer in acc.iter_standalone_consumer_names():
        main_version = acc._project_version_at(main_head, f"{consumer}/pyproject.toml")
        if main_version is None:
            continue  # never shipped on main yet -- dev's own literal stands as-is
        dev_version = acc.read_consumer_version(consumer)
        if dev_version is None or dev_version == main_version:
            continue
        seed_result[consumer] = (dev_version, main_version)
    if seed_result:
        acc.apply(seed_result)


def _sync_instruction_projections(scratch: Path) -> list[str]:
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
    script = (
        scratch / "plugins" / "customizing-copilot" / "skills"
        / "reviewing-customizations" / "scripts" / "manage-instruction-projections.py"
    )
    if not script.exists():
        return []
    proc = subprocess.run(
        [sys.executable, str(script), "sync", str(scratch), "--json"],
        capture_output=True, text=True,
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
    scratch: Path, *, last_dev_head: str | None = None, main_head: str, repo: Path = REPO
) -> dict:
    """Run accumulate_bumps' compute/apply against the scratch worktree's own
    copy of the tooling, and materialize_main's pointer expansion. Returns a
    summary dict used for the tag/commit message and tests.

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
    changefile_mod = _load_module(scratch / "tools" / "changefile.py", "promote_changefile")
    previous_changefile_mod = sys.modules.get("changefile")
    sys.modules["changefile"] = changefile_mod
    try:
        acc = _load_module(scratch / "tools" / "accumulate_bumps.py", "promote_accumulate_bumps")
        mm = _load_module(scratch / "tools" / "materialize_main.py", "promote_materialize_main")

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
        projections_synced = _sync_instruction_projections(scratch)
    finally:
        if previous_changefile_mod is not None:
            sys.modules["changefile"] = previous_changefile_mod
        else:
            sys.modules.pop("changefile", None)

    return {
        "bumps": {p: computed[p] for p in applied},
        "changefiles_consumed": changefile_names,
        "changefiles_newly_applied": newly_applied_names,
        "projections_synced": projections_synced,
        "materialize_log": materialize_log,
    }


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
    """
    return f"coverage-baselines-{measured_commit[:12]}"


def _write_coverage_baselines_into_scratch(
    scratch: Path, baselines_dir: Path | None, *, dev_head: str
) -> list[str]:
    """Write a small correlation **pointer** (never the full per-line
    coverage map) into the scratch worktree's own ``COVERAGE_BASELINES_DIR``
    for every ``*.json`` baseline file found in ``baselines_dir``, after
    verifying each one's own ``measured_commit`` matches ``dev_head``.

    The full per-line data is expected to **already be published** as a
    GitHub Release (one asset per plugin, tag `_release_tag_for(dev_head)`)
    by the caller (`validate-and-promote.yml`'s own "Publish coverage
    baselines" step) BEFORE this function ever runs -- this function only
    ever commits a pointer (`measured_commit` + `release_tag` + `asset`, a
    few hundred bytes regardless of plugin suite size) referencing that
    already-published release, never the payload itself. This script
    deliberately does NOT verify the release actually exists (it has no
    `gh`/network dependency anywhere else and this would be its only one)
    -- a nonexistent release for a committed pointer would be a
    caller-side ordering bug in the workflow, not something this pure
    git/tree-
    building tool can meaningfully check locally.

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
        plugin = data.get("plugin") or src.stem
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


def build_promotion_tree(scratch: Path) -> str:
    _remove_pycache(scratch)
    _git(["add", "-A"], cwd=scratch)
    return _git(["write-tree"], cwd=scratch)


def format_promotion_message(
    *, dev_range: tuple[str, str], summary: dict
) -> str:
    base, head = dev_range
    lines = [
        f"release: promote dev {base[:12]}..{head[:12]} to main",
        "",
        "Generated by tools/promote_release.py -- wholesale tree replace,",
        "never a merge commit. See the dev-branch-release-pipeline effort",
        "(ThomasMichon/copilot-extensions#3336) for the full design.",
        "",
    ]
    if summary["bumps"]:
        lines.append("Version bumps:")
        for plugin, (old, new) in sorted(summary["bumps"].items()):
            lines.append(f"  - {plugin}: {old} -> {new}")
    else:
        lines.append("Version bumps: none")
    lines.append("")
    if summary["changefiles_consumed"]:
        lines.append("Changefiles consumed:")
        for name in summary["changefiles_consumed"]:
            lines.append(f"  - {name}")
    else:
        lines.append("Changefiles consumed: none")
    return "\n".join(lines) + "\n"


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
        )
        subprocess.run(
            ["git", "rm", "--cached", "-q", "--ignore-unmatch", PIPELINE_STATE_PATH],
            cwd=str(repo), env=env, check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "rm", "--cached", "-q", "-r", "--ignore-unmatch",
             COVERAGE_BASELINES_DIR],
            cwd=str(repo), env=env, check=True, capture_output=True,
        )
        result = subprocess.run(
            ["git", "write-tree"], cwd=str(repo), env=env,
            capture_output=True, text=True, check=True,
        )
        return result.stdout.strip()


def promote(
    *,
    repo: Path = REPO,
    dev_ref: str = "origin/dev",
    main_ref: str = "origin/main",
    push: bool = False,
    force: bool = False,
    tag_prefix: str = "promote",
    candidate_branch: str | None = None,
    coverage_baselines_dir: Path | None = None,
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
    (a GitHub Release asset the caller must publish before calling this).
    Written only after the no-op content check (alongside the pipeline
    state file), so a baseline update alone -- with no real `dev` content
    change -- never forces a vacuous promotion."""
    dev_head = _rev_parse(dev_ref, cwd=repo)
    if dev_head is None:
        raise PromotionError(f"cannot resolve dev ref: {dev_ref!r}")
    main_head = _rev_parse(main_ref, cwd=repo)
    if main_head is None:
        raise PromotionError(f"cannot resolve main ref: {main_ref!r}")
    main_tree = _git(["rev-parse", f"{main_head}^{{tree}}"], cwd=repo)

    state = _read_pipeline_state(main_head, repo=repo)
    if state.get("paused"):
        raise PromotionPaused(
            "promotion is paused: "
            f"{state.get('pause_reason') or '(no reason recorded)'} -- "
            "run tools/rollback_release.py resume once safe to continue"
        )
    last_rollback = state.get("last_rollback")
    if (
        last_rollback
        and not force
        and last_rollback.get("reverted_dev_head") == dev_head
    ):
        raise NonIncrementalPromotion(
            f"refusing to re-promote dev@{dev_head[:12]}: this exact dev state "
            "was already rolled back "
            f"({last_rollback.get('reason') or 'no reason recorded'}). "
            "dev must move forward with an actual fix before promoting again "
            "-- or pass --force to override this guard deliberately."
        )

    last_promotion = state.get("last_promotion") or {}
    last_dev_head = last_promotion.get("dev_head")
    # Monotonic stale-candidate guard (added after a live review catch,
    # 2026-09-29 -- see the dev-branch-release-pipeline effort Journal):
    # `validate-and-promote.yml`'s own concurrency group only serializes
    # DISPATCH-ARRIVAL order, never commit order. Two `dev` commits' own
    # `promote-trigger.yml` filter runs (deliberately unthrottled -- that's
    # the whole point of the split) can complete and dispatch out of order
    # -- an older commit A's dispatch can arrive and get processed AFTER a
    # newer commit B's, even though A was pushed first. Without this guard,
    # `dev_head` (A) being a real `origin/dev` ancestor was the only check,
    # so A's (stale) content would silently promote on top of main's
    # already-newer (B) content -- a genuine regression, not a no-op.
    # Refuse unless `dev_head` is at least as new as whatever this pipeline
    # already promoted: either the identical commit (a legitimate re-run,
    # e.g. a retried dispatch -- falls through to the ordinary "no content
    # change vs. main" no-op below) or a strict descendant of it.
    if (
        last_dev_head
        and last_dev_head != dev_head
        and not _is_ancestor(last_dev_head, dev_head, cwd=repo)
    ):
        raise StaleCandidatePromotion(
            f"refusing to promote dev@{dev_head[:12]}: it is not a descendant "
            f"of the already-promoted dev@{last_dev_head[:12]} -- this looks "
            "like an out-of-order/stale candidate (a newer dev commit was "
            "already promoted while this one was still validating). No "
            "action needed: dev has already moved forward past this "
            "candidate's own content."
        )

    scratch = add_scratch_worktree(dev_head, repo=repo)
    try:
        summary = consume_pending_changes(
            scratch,
            last_dev_head=last_promotion.get("dev_head"),
            main_head=main_head,
            repo=repo,
        )
        content_tree = build_promotion_tree(scratch)
        main_content_tree = _tree_excluding_state(main_tree, repo=repo)

        if content_tree == main_content_tree:
            return {"promoted": False, "reason": "no content change vs. main", **summary}

        base = _git(["merge-base", main_head, dev_head], cwd=repo, check=False) or main_head
        message = format_promotion_message(dev_range=(base, dev_head), summary=summary)
        tag_name = f"{tag_prefix}-{time.strftime('%Y%m%d%H%M%S')}"

        new_state = dict(state)
        new_state["last_promotion"] = {
            "dev_head": dev_head,
            "main_before": main_head,
            "tag": tag_name,
            "promoted_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        }
        _write_pipeline_state_into_scratch(scratch, new_state)
        _seed_coverage_baselines_from_main(scratch, main_head, repo=repo)
        baselines_written = _write_coverage_baselines_into_scratch(
            scratch, coverage_baselines_dir, dev_head=dev_head
        )
        new_tree = build_promotion_tree(scratch)

        commit = _git(
            ["commit-tree", new_tree, "-p", main_head, "-m", message], cwd=scratch
        )
        tag_name = f"{tag_name}-{commit[:8]}"

        if push and candidate_branch:
            # Land via a real PR, not a direct push (see promote()'s
            # docstring) -- the tag is created by the caller against the
            # actual post-merge commit, not this pre-merge candidate.
            _git(["push", "origin", f"{commit}:refs/heads/{candidate_branch}"], cwd=scratch)
        else:
            _git(["tag", "-a", tag_name, "-m", message, commit], cwd=scratch)
            if push:
                _git(["push", "origin", f"{commit}:refs/heads/main"], cwd=scratch)
                _git(["push", "origin", tag_name], cwd=scratch)

        return {
            "promoted": True,
            "commit": commit,
            "tag": tag_name,
            "main_before": main_head,
            "dev_head": dev_head,
            "pushed": push,
            "candidate_branch": candidate_branch if (push and candidate_branch) else None,
            "coverage_baselines_written": baselines_written,
            **summary,
        }
    finally:
        remove_scratch_worktree(scratch, repo=repo)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    ap.add_argument("--repo", type=Path, default=REPO, help="repo root (default: this checkout)")
    ap.add_argument("--dev-ref", default="origin/dev")
    ap.add_argument("--main-ref", default="origin/main")
    ap.add_argument(
        "--force", action="store_true",
        help="override the non-incremental-promotion guard after a rollback",
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
    args = ap.parse_args(argv)

    try:
        report = promote(
            repo=args.repo, dev_ref=args.dev_ref, main_ref=args.main_ref,
            push=args.push, force=args.force, candidate_branch=args.candidate_branch,
            coverage_baselines_dir=args.coverage_baselines_dir,
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

    print(f"promote-release: generated commit {report['commit']} (tag {report['tag']})")
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
    if report.get("candidate_branch"):
        print(f"  pushed candidate branch: {report['candidate_branch']} "
              "(land it on main via a real PR + merge; tag the actual merged commit)")
    elif not report["pushed"]:
        print("  (dry run -- nothing pushed; re-run with --push to update origin/main)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
