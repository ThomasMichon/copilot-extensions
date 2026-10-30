#!/usr/bin/env python3
"""Interpret a previously captured request locally without another CLI launch."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys

from snapshot_artifacts import local_output, reserve_output, write_report
from snapshot_report import analyze_file


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--working-directory", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--session-id")
    parser.add_argument("--agent-worktrees-command")
    args = parser.parse_args(argv)
    descriptor = None
    output = None
    written = False
    try:
        output = local_output(args.output)
        descriptor = reserve_output(output)
        report = analyze_file(
            args.request, args.working_directory, session_id=args.session_id,
            agent_worktrees_command=args.agent_worktrees_command,
        )
        write_report(descriptor, report)
        written = True
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        print(json.dumps({"status": "failed", "error": type(error).__name__}))
        return 1
    finally:
        if descriptor is not None:
            os.close(descriptor)
            if not written:
                output.unlink()
    print(json.dumps({
        "status": "analyzed", "analysis_path": str(output),
        "analysis_warning_count": len(report["warnings"]),
    }))
    return 0


if __name__ == "__main__":
    sys.exit(main())
