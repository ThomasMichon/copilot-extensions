"""Disposable owner-process fixture; every removal is intercepted."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))

from agent_worktrees import cleanup_gc_cli, output, tracking
from _cleanup_revalidation_helpers import CleanupHarness, S, info, make_record


def dispatch_attempt(attempt: int = 1) -> tracking.DispatchAttempt:
    return tracking.DispatchAttempt(
        task_id="task-a", reservation_key=f"dispatch-task:task-a:{attempt}",
        attempt=attempt, driver="agent-dispatch", supervisor="declared:project:workers",
        creator_machine="fixture-host",
    )


def exercise(root: Path, state: str, argv: list[str]) -> tuple[int, dict]:
    with pytest.MonkeyPatch.context() as patch:
        harness = CleanupHarness(patch, root)
        record = make_record(
            root, wt_id="workspace-a", status="active",
            dispatch_attempt=dispatch_attempt(2 if state == "retrying" else 1)
            if state in {"pending", "retrying", "concluded"} else None,
        )
        if state == "opaque":
            record.dispatch_attempt_opaque = True
            record.dispatch_attempt_raw_present = True
            record.dispatch_attempt_raw = {"version": 999, "task_id": "task-a"}
        if state == "concluded":
            record.kind = "bridge"
            record.status = "complete"
        harness.seed(record)
        harness.seed(make_record(root, wt_id="unrelated", status="active"))
        harness.set_classifier(info(S.UNUSED), info(S.UNUSED))
        harness.set_reap_callback(
            lambda rec, _info: (
                harness.assert_record_lock_blocks_peer_thread(harness.record_path(rec.worktree_id)),
                (0, []),
            )[1],
        )
        parser = argparse.ArgumentParser()
        parser.add_argument("--project", required=True)
        cleanup_gc_cli.add_parsers(parser.add_subparsers(dest="command", required=True))
        args = parser.parse_args(argv)
        assert args.project == "project-a"
        assert args.command == "cleanup"
        with output.capture_json_output() as captured:
            code = cleanup_gc_cli.cmd_cleanup(args)
        payload = json.loads(captured.getvalue()) if captured.getvalue() else None
        proof = {
            "argv": argv, "payload": payload, "reaped": harness.reaped,
            "workspace_exists": Path(record.worktree_path).is_dir(),
            "unrelated_exists": (root / "unrelated").is_dir(),
            "record_exists": harness.record_path("workspace-a").is_file(),
        }
        return code, proof


def main() -> int:
    root, state = Path(sys.argv[1]), sys.argv[2]
    root.mkdir()
    code, proof = exercise(root, state, sys.argv[3:])
    (root / "proof.json").write_text(json.dumps(proof), encoding="utf-8")
    print(json.dumps(proof["payload"]))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
