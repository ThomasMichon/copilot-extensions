"""Strict execution planning, separate from the non-blocking shadow CLI."""
from __future__ import annotations

from pathlib import Path

from tools.coverage_guided_selection.ancestor_resolution import _git, _parse_hunks
from tools.coverage_guided_selection.decide import decide


def execution_diff(
    repo_root: Path, base_ref: str, head_ref: str, *, plugin: str, cov_source: str,
) -> tuple[str, dict[str, list[int]], tuple[str, ...]]:
    """Select against fork-point coordinates, not post-edit line numbers."""
    fork = _git(["merge-base", base_ref, head_ref], cwd=repo_root).strip()
    changed = _git(
        ["diff", "--no-renames", "--name-only", "-z", fork, head_ref], cwd=repo_root,
    ).split("\x00")
    lines = {}
    test_files = []
    plugin_prefix = f"plugins/{plugin}/"
    for file in filter(None, changed):
        if file.startswith(plugin_prefix + "tests/"):
            path = repo_root / file
            if path.is_file() and path.name.startswith("test_") and path.suffix == ".py":
                test_files.append(file.removeprefix(plugin_prefix))
        if file.startswith(cov_source.rstrip("/") + "/"):
            text = _git(
                ["diff", "--no-renames", "--unified=0", "--no-color",
                 "--no-ext-diff", "--no-textconv", fork, head_ref, "--", file],
                cwd=repo_root,
            )
            hunks = _parse_hunks(text)
            touched = set()
            for old_start, old_len, _new_start, new_len in hunks:
                touched.update(range(old_start, old_start + old_len))
                if new_len:
                    touched.add(0)  # new execution has no earned attribution
            lines[file] = sorted(touched) if touched else [0]
        else:
            # Runtime line coverage cannot disprove an out-of-scope dependency
            # or text-scanning edge. Keep it visible as a fallback trigger.
            lines[file] = [0]
    return fork, lines, tuple(sorted(set(test_files)))


def build_execution_payload(
    *,
    repo_root: Path,
    repo: str,
    plugin: str,
    cov_source: str,
    base_ref: str,
    head_ref: str,
    main_ref: str,
    smoke_budget_s: float,
    commit_volume_threshold: int,
    age_threshold_seconds: float,
) -> dict:
    fork, changed_lines, test_files = execution_diff(
        repo_root, base_ref, head_ref, plugin=plugin, cov_source=cov_source,
    )
    result = decide(
        repo_root, repo, plugin, fork, changed_lines, main_ref=main_ref,
        fallback_runtime_budget_s=smoke_budget_s,
        commit_volume_threshold=commit_volume_threshold,
        age_threshold_seconds=age_threshold_seconds,
        include_smoke_floor=True,
    )
    payload = result.as_dict()
    payload.update(
        fork_commit=fork, smoke_budget_s=smoke_budget_s,
        commit_volume_threshold=commit_volume_threshold,
        age_threshold_seconds=age_threshold_seconds,
        changed_test_files=list(test_files), include_guards=True,
    )
    if result.selected_tests is None:
        payload["execution"] = "full"
        payload["selection"] = None
        return payload
    if result.fallback_set is None or not result.fallback_set.get("selected_tests"):
        raise ValueError(
            "coverage evidence produced an empty selection for the smoke floor; "
            f"refusing selected execution (budget={smoke_budget_s:g}s, "
            f"baseline={result.baseline_generation})"
        )
    if not result.selected_tests:
        raise ValueError("coverage evidence produced an empty selection; refusing a guard-only pass")
    payload["execution"] = "selected"
    payload["selection"] = {
        "version": 1, "plugin": plugin, "nodes": list(result.selected_tests),
        "files": list(test_files), "include_guards": True,
    }
    return payload
