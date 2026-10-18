"""Partition complete plugin suites without changing the contained test runner."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re

_PLUGIN = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]*\Z")
_FILES_PER_GROUP = 25
_SMOKE_PARTITIONS = {"agent-dispatch": 3}


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
