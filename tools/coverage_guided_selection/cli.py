"""CI-facing CLI for `decide()` -- coverage-guided-ci Phase 4 (rollout).

Computes a `SelectionDecision` for one plugin's PR diff and prints it as
JSON to stdout, plus a short human-readable summary appended to
`$GITHUB_STEP_SUMMARY` when running under GitHub Actions (silently skipped
otherwise, e.g. a local manual run).

Phase 4's own rollout plan (see this effort's Journal) starts in **shadow
mode**: this CLI's output is observed, not yet gated on -- `ci.yml`'s own
step runs it with `continue-on-error: true` regardless. Deliberately never
raises for an *unexpected* failure either, on top of that workflow-level
belt: a bug in this still-new code path, a transient `gh`/network hiccup,
or an unusual diff shape must never be able to crash the step (and thus
still show up as a red X in the PR's checks list, `continue-on-error`'s own
"neutral" annotation notwithstanding) -- every exception is caught and
reported as its own `mode: "error"` JSON decision instead of propagating.
A future "live" mode (actually gating a job on this decision) is
intentionally not implemented yet -- see the effort's own Plan.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

try:
    from decide import decide
    from diff import compute_changed_lines
except ModuleNotFoundError:
    from tools.coverage_guided_selection.decide import decide
    from tools.coverage_guided_selection.diff import compute_changed_lines


def _write_step_summary(text: str) -> None:
    summary_path = os.environ.get("GITHUB_STEP_SUMMARY")
    if not summary_path:
        return
    with open(summary_path, "a", encoding="utf-8") as fh:
        fh.write(text)
        if not text.endswith("\n"):
            fh.write("\n")


def _resolve_commit(repo_root: Path, ref: str) -> str:
    import subprocess

    try:
        from ancestor_resolution import scrubbed_git_env
    except ModuleNotFoundError:
        from tools.coverage_guided_selection.ancestor_resolution import scrubbed_git_env

    proc = subprocess.run(
        ["git", "rev-parse", ref], cwd=repo_root, capture_output=True, text=True,
        check=False, env=scrubbed_git_env(),
    )
    if proc.returncode != 0:
        raise RuntimeError(f"git rev-parse {ref} failed: {proc.stderr.strip()}")
    return proc.stdout.strip()


def build_decision_payload(
    *,
    repo_root: Path,
    repo: str,
    plugin: str,
    cov_source: str,
    base_ref: str,
    head_ref: str,
    main_ref: str,
) -> dict:
    """The actual decide()-or-error logic, split out from `main()` so a
    test can exercise it directly without going through argv/stdout."""
    try:
        head_commit = _resolve_commit(repo_root, head_ref)
        changed_lines = compute_changed_lines(
            repo_root, base_ref, head_commit, path_prefix=cov_source,
        )
        result = decide(
            repo_root, repo, plugin, head_commit, changed_lines, main_ref=main_ref,
        )
        return result.as_dict()
    except Exception as error:  # noqa: BLE001 -- see module docstring
        return {
            "mode": "error",
            "selected_tests": None,
            "reason": f"{type(error).__name__}: {error}",
            "baseline_generation": None,
            "baseline_commit_on_main": None,
            "debt": None,
            "selection_fallback_reasons": [],
            "fallback_set": None,
        }


def render_summary(plugin: str, payload: dict) -> str:
    lines = [
        "### coverage-guided-ci selection (shadow mode, non-blocking)",
        f"- plugin: `{plugin}`",
        f"- mode: `{payload.get('mode')}`",
        f"- reason: `{payload.get('reason')}`",
    ]
    selected = payload.get("selected_tests")
    if selected is None:
        lines.append("- selected_tests: none (no curated evidence)")
    else:
        lines.append(f"- selected_tests: {len(selected)} test(s)")
    if payload.get("baseline_generation"):
        lines.append(f"- baseline_generation: `{payload['baseline_generation']}`")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo-root", type=Path, default=Path.cwd())
    parser.add_argument("--repo", required=True, help="owner/name, for the Release-asset fetch")
    parser.add_argument("--plugin", required=True)
    parser.add_argument(
        "--cov-source", required=True,
        help="e.g. plugins/agent-worktrees/src/agent_worktrees -- must match the "
        "baseline's own --cov-source so changed-file keys line up",
    )
    parser.add_argument("--base-ref", required=True, help="PR's base ref/sha to diff against")
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument(
        "--main-ref", default="origin/main",
        help="where the baseline pointer history lives -- origin/main (not local "
        "'main') since a CI checkout has no local branch by that name",
    )
    parser.add_argument("--out", type=Path, default=None, help="also write the JSON decision here")
    args = parser.parse_args(argv)

    payload = build_decision_payload(
        repo_root=args.repo_root, repo=args.repo, plugin=args.plugin,
        cov_source=args.cov_source, base_ref=args.base_ref, head_ref=args.head_ref,
        main_ref=args.main_ref,
    )

    text = json.dumps(payload, indent=2)
    print(text)
    if args.out:
        args.out.write_text(text)

    _write_step_summary(render_summary(args.plugin, payload))
    return 0  # shadow mode: this CLI never fails the step on its own account


if __name__ == "__main__":
    sys.exit(main())
