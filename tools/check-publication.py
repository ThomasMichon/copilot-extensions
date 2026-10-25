#!/usr/bin/env python3
"""Run both independent publication guards against the same pinned Git inputs."""

from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import importlib.util
from pathlib import Path
import sys


def _guard(name: str):
    path = Path(__file__).with_name(name + ".py")
    spec = importlib.util.spec_from_file_location("_publication_" + name.replace("-", "_"), path)
    if spec is None or spec.loader is None:
        raise ImportError("Publication guard is unavailable: " + name)
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument("--published", action="append", default=[])
    args = parser.parse_args(argv)
    identifiers = _guard("check-no-internal-identifiers")
    sizes = _guard("check-large-files")
    size_args = ["--base", args.base, "--head", args.head]
    for published in args.published:
        size_args.extend(("--published", published))
    with ThreadPoolExecutor(max_workers=2) as workers:
        pending = [
            workers.submit(identifiers.main, ["--base", args.base, "--git-ref", args.head]),
            workers.submit(sizes.main, size_args),
        ]
        outcomes = [task.result() for task in pending]
    return 0 if outcomes == [0, 0] else 1


if __name__ == "__main__":
    raise SystemExit(main())
