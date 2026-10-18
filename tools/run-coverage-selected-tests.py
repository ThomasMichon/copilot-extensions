#!/usr/bin/env python3
"""Execute an auditable coverage decision through the contained plugin runner."""
from __future__ import annotations

import argparse
import json
import math
import os
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from html import escape

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from tools.coverage_guided_selection.execution import build_execution_payload  # noqa: E402


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--plugin", required=True, choices=["agent-dispatch"])
    parser.add_argument("--base-ref", required=True)
    parser.add_argument("--head-ref", default="HEAD")
    parser.add_argument("--main-ref", default="origin/main")
    parser.add_argument("--smoke-budget", required=True, type=float)
    parser.add_argument("--commit-volume-threshold", required=True, type=int)
    parser.add_argument("--age-threshold-seconds", required=True, type=float)
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args(argv)
    if (
        not math.isfinite(args.smoke_budget) or args.smoke_budget <= 0
        or args.commit_volume_threshold <= 0
        or not math.isfinite(args.age_threshold_seconds) or args.age_threshold_seconds <= 0
    ):
        parser.error("smoke and coverage-debt budgets must be finite and positive")
    tests = REPO / "plugins" / args.plugin / "tests"
    if not tests.is_dir() or not any(tests.glob("test_*.py")):
        parser.error("coverage execution requires an existing plugin test suite, not a no-suite skip")
    started = time.monotonic()
    payload = build_execution_payload(
        repo_root=REPO, repo=args.repo, plugin=args.plugin,
        cov_source="plugins/agent-dispatch/src/agent_dispatch",
        base_ref=args.base_ref, head_ref=args.head_ref, main_ref=args.main_ref,
        smoke_budget_s=args.smoke_budget,
        commit_volume_threshold=args.commit_volume_threshold,
        age_threshold_seconds=args.age_threshold_seconds,
    )
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    reason = payload["reason"]
    console_reason = " ".join(reason.splitlines())
    if len(console_reason) > 500:
        console_reason = console_reason[:500] + "... (see decision artifact)"
    selection_data = payload["selection"]
    floor = payload.get("fallback_set")
    print(json.dumps({
        "execution": payload["execution"],
        "mode": payload.get("mode"),
        "reason": console_reason,
        "baseline_generation": payload["baseline_generation"],
        "selected_node_count": None if selection_data is None else len(selection_data["nodes"]),
        "changed_test_file_count": len(payload.get("changed_test_files", [])),
        "smoke": None if floor is None else {
            key: floor[key] for key in ("total_runtime_s", "covered_fraction", "universe_size")
        },
    }), flush=True)
    command = [sys.executable, str(REPO / "tools/run-plugin-tests.py"), args.plugin]
    if payload["execution"] == "full":
        print(f"[FALLBACK] full/default portfolio: {console_reason}", flush=True)
        result = subprocess.run(command, cwd=REPO, check=False)
    else:
        with tempfile.TemporaryDirectory(prefix="ce-selection-") as directory:
            selection = Path(directory) / "selection.json"
            selection.write_text(json.dumps(payload["selection"]), encoding="utf-8")
            result = subprocess.run(
                [*command, "--selection-file", str(selection),
                 "--timeout", "540", "--plugin-timeout", "540"],
                cwd=REPO, check=False,
            )
    payload.update(invocation_seconds=time.monotonic() - started, exit_code=result.returncode)
    args.out.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if summary:
        with open(summary, "a", encoding="utf-8") as stream:
            stream.write(
                "### Coverage-selected dispatch execution\n"
                f"- Execution: `{payload['execution']}`; reason: <code>{escape(console_reason)}</code>\n"
                f"- Baseline: `{payload['baseline_generation']}`\n"
                f"- Complete invocation: {payload['invocation_seconds']:.2f}s; "
                f"exit: {result.returncode}\n"
                "- Exact nodes, curation cost, coverage breadth, debt and changed "
                "test files are in the uploaded decision artifact.\n"
            )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
