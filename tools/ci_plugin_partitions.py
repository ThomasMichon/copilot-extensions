"""Partition complete plugin suites without changing the contained test runner."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
import json
import math
from pathlib import Path
import re

_PLUGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_FILES_PER_GROUP = 25
_SMOKE_PARTITIONS = {"agent-dispatch": 3, "agent-pull-requests": 2}
_COMMIT = re.compile(r"[0-9a-f]{40}\Z")


def _hosted(reason: str) -> dict[str, str]:
    return {"backend": "hosted", "reason": reason}


def _identity(value: object) -> bool:
    return isinstance(value, str) and bool(value) and value == value.strip()


def _fresh(observed: object, now: object, max_age: object) -> bool:
    values: list[float] = []
    for value in (observed, now, max_age):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            return False
        try:
            number = float(value)
        except OverflowError:
            return False
        if not math.isfinite(number):
            return False
        values.append(number)
    timestamp, current, maximum = values
    return maximum > 0 and 0 <= current - timestamp <= maximum


def _partition_identity(row: Mapping[str, object]) -> bool:
    if "partition" not in row and "partitions" not in row:
        return row.get("plugin") == "agent-worktrees"
    index, count = row.get("partition"), row.get("partitions")
    return type(index) is int and type(count) is int and count > 0 and 0 <= index < count


def select_backend(row: object, context: object) -> dict[str, str]:
    """Choose a route kind before execution; never mutate or allocate a row.

    Context supplies mode/job, exact tested head and immutable recipe identity,
    one existing provider, opt_in=True, current author/sender write trust and
    same-repository origin, execution_started=False, now, and a readiness
    snapshot. Readiness must bind that same provider/head/recipe/row and contain provider_ready,
    coverage_ready, coverage="complete", observed_at and max_age_seconds.
    "Complete" means the entire existing recipe, not 100% source-line coverage.

    Readiness.windows_ram explicitly declares required=False for a native
    non-Windows provider, or required=True plus fresh free_bytes/required_bytes,
    observed_at/max_age_seconds. Unknown observations remain hosted.
    Timestamps and now use the same caller clock, measured in seconds.

    Thresholds, identities and provider proof come from the protected caller;
    this pure per-row decision is not allocation authority or a runner label.
    The caller retains inventory, commands, worker admission and failure
    propagation. It must never reselect or retry on hosted after execution starts.
    """
    if not isinstance(context, Mapping):
        return _hosted("invalid-row-or-context")
    if context.get("execution_started") is True:
        raise ValueError("backend selection is pre-execution only; propagate the execution result")
    if not isinstance(row, Mapping):
        return _hosted("invalid-row-or-context")
    if context.get("execution_started") is not False:
        return _hosted("execution-state-unknown")
    if context.get("mode") != "pr":
        return _hosted("not-ordinary-pr")
    if (context.get("job"), row.get("plugin")) not in (
        ("smoke", "agent-dispatch"), ("worktrees-smoke", "agent-worktrees"),
    ):
        return _hosted("outside-selected-pr-recipes")
    if "job" in row and row["job"] != context.get("job"):
        return _hosted("job-identity-mismatch")
    if not _identity(row.get("name")):
        return _hosted("row-identity-missing")
    if not _partition_identity(row):
        return _hosted("invalid-existing-partition")
    if context.get("opt_in") is not True:
        return _hosted("opt-in-inactive")
    for key in ("same_repository", "author_write", "sender_write", "trust_current"):
        if context.get(key) is not True:
            return _hosted(f"{key}-unconfirmed")
    head, recipe, provider = (context.get(key) for key in ("head", "recipe", "provider"))
    if not isinstance(head, str) or not _COMMIT.fullmatch(head):
        return _hosted("head-not-immutable")
    if not _identity(recipe) or not _identity(provider):
        return _hosted("recipe-or-provider-identity-missing")
    ready = context.get("readiness")
    if not isinstance(ready, Mapping):
        return _hosted("readiness-missing")
    if ready.get("provider_ready") is not True:
        return _hosted("provider-not-ready")
    if ready.get("coverage_ready") is not True or ready.get("coverage") != "complete":
        return _hosted("recipe-coverage-incomplete")
    qualified_row = ready.get("row")
    if (
        not isinstance(qualified_row, Mapping) or not _partition_identity(qualified_row)
        or qualified_row != row
        or any(ready.get(key) != context.get(key) for key in ("head", "recipe", "provider"))
    ):
        return _hosted("readiness-identity-mismatch")
    if not _fresh(ready.get("observed_at"), context.get("now"), ready.get("max_age_seconds")):
        return _hosted("readiness-not-fresh")
    ram = ready.get("windows_ram")
    if not isinstance(ram, Mapping) or type(ram.get("required")) is not bool:
        return _hosted("windows-reserve-requirement-unknown")
    if ram["required"]:
        free, required = ram.get("free_bytes"), ram.get("required_bytes")
        if type(free) is not int or type(required) is not int or free < 0 or required <= 0:
            return _hosted("windows-reserve-observation-invalid")
        if not _fresh(ram.get("observed_at"), context.get("now"), ram.get("max_age_seconds")):
            return _hosted("windows-reserve-not-fresh")
        if free < required:
            return _hosted("windows-reserve-insufficient")
    return {"backend": "private", "reason": "ready-exact-ordinary-pr-recipe"}


def smoke_matrix(
    plugins: object, *, single_job_plugins: frozenset[str] = frozenset(),
) -> list[dict]:
    if not isinstance(plugins, list) or any(
        not isinstance(name, str) or not _PLUGIN.fullmatch(name) for name in plugins
    ):
        raise ValueError("expected a list of portable plugin names")
    if len(set(plugins)) != len(plugins):
        raise ValueError("duplicate plugin names")
    if not isinstance(single_job_plugins, frozenset) or any(
        not isinstance(name, str) or not _PLUGIN.fullmatch(name) for name in single_job_plugins
    ):
        raise ValueError("expected a frozenset of portable single-job plugin names")
    rows = []
    for plugin in plugins:
        count = 1 if plugin in single_job_plugins else _SMOKE_PARTITIONS.get(plugin, 1)
        for index in range(count):
            rows.append({
                "plugin": plugin, "partition": index, "partitions": count,
                "name": plugin if count == 1 else f"{plugin} (partition {index + 1}/{count})",
            })
    return rows


def partition_files(repo: Path, plugin: str, index: int, count: int) -> list[str]:
    if not _PLUGIN.fullmatch(plugin):
        raise ValueError("expected a portable plugin name")
    if count < 1 or not 0 <= index < count:
        raise ValueError("partition index must be in [0, count)")
    root = repo / "plugins" / plugin
    files = sorted((root / "tests").rglob("test_*.py"))
    # Preserve the runner's ordinary 25-file process boundaries, distributing
    # whole groups rather than combining unrelated tests into a larger process.
    groups = [files[start:start + _FILES_PER_GROUP] for start in range(0, len(files), _FILES_PER_GROUP)]
    selected = [path for offset, group in enumerate(groups) if offset % count == index for path in group]
    if not selected:
        raise ValueError("partition has no test files")
    return [path.relative_to(root).as_posix() for path in selected]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    matrix = commands.add_parser("matrix")
    matrix.add_argument("plugins_json")
    matrix.add_argument(
        "--single-job-plugin", action="append", default=[],
        help="emit one row for this plugin (repeatable; default partitions stay unchanged)",
    )
    candidate = commands.add_parser("candidate-smoke")
    candidate.add_argument("plugins_json")
    for name in ("event", "promotion-run", "phase", "profile"):
        candidate.add_argument(f"--{name}", required=True)
    files = commands.add_parser("files")
    files.add_argument("--plugin", required=True)
    files.add_argument("--partition", type=int, required=True)
    files.add_argument("--partitions", type=int, required=True)
    args = parser.parse_args()
    try:
        if args.command == "matrix":
            print(json.dumps(smoke_matrix(
                json.loads(args.plugins_json), single_job_plugins=frozenset(args.single_job_plugin),
            ), separators=(",", ":")))
        elif args.command == "candidate-smoke":
            try:
                from materialized_candidate_inventory import CandidateValidationError
                from candidate_complete_once import candidate_smoke
            except ModuleNotFoundError as exc:
                parser.error(f"candidate-smoke helper dependency is unavailable: {exc.name}")

            try:
                selected = candidate_smoke(
                    Path(__file__).resolve().parents[1], json.loads(args.plugins_json),
                    event=args.event, promotion_run=args.promotion_run,
                    phase=args.phase, profile=args.profile,
                )
            except CandidateValidationError as exc:
                parser.error(str(exc))
            print(json.dumps(selected, separators=(",", ":")))
        else:
            for path in partition_files(
                Path(__file__).resolve().parents[1], args.plugin, args.partition, args.partitions,
            ):
                print("--test-file")
                print(path)
    except ValueError as exc:
        parser.error(str(exc))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
