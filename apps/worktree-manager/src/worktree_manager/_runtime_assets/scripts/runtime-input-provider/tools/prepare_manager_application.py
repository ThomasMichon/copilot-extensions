"""Complete admitted first-party Manager inputs using the canonical native profile owner."""
from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [
    str(ROOT / "tools"), str(ROOT / "libs" / "installer-engine"),
    str(ROOT / "libs" / "versioned-runtime"), str(ROOT / "worktree-manager" / "scripts"),
]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--install-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--identity", required=True)
    parser.add_argument("--python", type=Path, required=True)
    parser.add_argument("--uv", type=Path, required=True)
    parser.add_argument("--deadline", type=float, required=True)
    args = parser.parse_args(argv)
    from installer_dependency_config import remaining_timeout
    from installer_packaging_seed import _target
    from build_toolchain_lock import _query_marker_environment
    from local_install_profile import prepare_registry_lock
    from install_source import manager_inputs, select

    remaining_timeout(args.deadline, error_type=ValueError)
    if not args.python.is_absolute() or not args.uv.is_absolute() or not args.uv.is_file():
        raise ValueError("Manager input preparation requires absolute Python and UV selections")
    expected = json.loads(args.identity)
    selected = select(args.install_source)
    if selected.identity.document() != expected:
        raise ValueError("Manager preparation input differs from its admitted immutable source")
    target = _query_marker_environment(args.python, deadline=args.deadline)
    if (
        target != _query_marker_environment(Path(sys.executable), deadline=args.deadline)
        or _target(args.python, dict(os.environ), args.deadline)
        != _target(Path(sys.executable), dict(os.environ), args.deadline)
    ):
        raise ValueError("Manager tooling interpreter differs from the selected native target")
    record = prepare_registry_lock(
        args.install_source, args.output, uv=str(args.uv), deadline=args.deadline,
        prepare_profile=True, component="worktree-manager",
    )
    with manager_inputs(Path(record["snapshot"]), deadline=args.deadline) as (observed, inputs):
        if observed.identity.document() != expected or "application" not in observed.project:
            raise ValueError("Manager preparation did not produce the admitted complete application")
        inputs.verify()
    remaining_timeout(args.deadline, error_type=ValueError)
    print(json.dumps({"snapshot": record["snapshot"], "identity": expected}, sort_keys=True))
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, ValueError, RuntimeError) as error:
        print(f"Manager application input preparation refused: {error}", file=sys.stderr)
        raise SystemExit(1) from error
