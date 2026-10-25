"""Dedicated read-only production Picker entry; never dispatch its result."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sys

from . import picker_review


def main(args: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="worktree-manager picker review")
    parser.add_argument("project")
    parser.add_argument("--candidate", default="source")
    parser.add_argument("--capture", type=Path)
    parser.add_argument("--format", choices=("svg", "text", "ansi"), default="svg")
    parser.add_argument("--pivot")
    parser.add_argument("--wait", type=float, default=20.0)
    options = parser.parse_args(args)
    if not math.isfinite(options.wait) or options.wait < 0:
        parser.error("--wait must be finite and non-negative")
    if (
        not options.candidate or len(options.candidate) > 48
        or any(ord(char) < 32 for char in options.candidate)
    ):
        parser.error("--candidate must be a printable label of 1-48 characters")
    output = options.capture.resolve() if options.capture else None
    try:
        with picker_review.session(options.candidate) as review:
            print(
                f"READ-ONLY candidate {review.candidate}\n"
                f"Manager source: {Path(__file__).resolve().parent}\n"
                f"Existing owner: {review.owner_root}\n"
                "Local reads only; no owner recovery, installs or live actions.",
                file=sys.stderr,
            )
            from .production_picker import runner

            if output is None:
                runner.run(options.project, mock_mode=True, local=True)
            else:
                captures = runner.capture(
                    options.project, pivot=options.pivot, wait_pivot=options.wait,
                )
                output.write_text(captures[options.format], encoding="utf-8")
                print(json.dumps({
                    "review": True, "read_only": True, "candidate": review.candidate,
                    "capture": str(output), "format": options.format,
                }))
    except Exception as error:
        print(f"error: read-only Picker review failed: {error}", file=sys.stderr)
        return 1
    return 0
