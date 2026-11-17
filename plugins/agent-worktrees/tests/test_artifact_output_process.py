"""Opt-in real-process qualification with synthetic owner/configuration inputs."""

from dataclasses import replace
import io
import json
import os
from pathlib import Path
import secrets
import subprocess
import sys
from typing import Any, Callable

import pytest

from agent_worktrees import __main__ as main, artifact_output, claims_cli, config as cfg
from agent_worktrees import config_sources, output, state_root, tracking
from agent_worktrees.artifact_root import admit_root
from test_resource_producer import owner as owner

pytestmark = [
    pytest.mark.portfolio_tier("T3"), pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
]


def _drive(root: Path) -> dict[str, Any]:
    with pytest.MonkeyPatch.context() as patch:
        fixture: Callable[[Path, pytest.MonkeyPatch], tuple[Path, cfg.Config]] = getattr(owner, "__wrapped__")
        path, config = fixture(root, patch)
        base = root / "source"
        base.mkdir()
        config.repos["p"] = replace(config.repos["p"], anchor=str(base))
        selected = root / "user-storage"
        with admit_root(selected, forbidden=(), create=True):
            pass
        patch.setattr(config_sources, "configuration_report", lambda *a: {
            "schema": "agent-worktrees.configuration", "version": 1,
            "context": {
                "repository_id": "repo-fixture", "execution_machine": "m",
                "installation_cell": "fixture-cell", "binding": "unbound", "base_root": str(base),
            },
        })
        patch.setattr(
            claims_cli, "_coordination_readiness_for_owner_ref",
            lambda *a: state_root.CoordinationReadiness(
                True, "ready", state_root.StateRoot(str(base), "launch_repo", "p", False, False, True),
            ),
        )
        def no_bootstrap() -> None:
            raise AssertionError("user-output process bootstrapped the command cluster")
        patch.setattr(main, "_ensure_cluster_loaded", no_bootstrap)
        def invoke(operation: str, payload: dict[str, Any]) -> dict[str, Any]:
            patch.setattr(sys, "stdin", io.TextIOWrapper(io.BytesIO(json.dumps(payload).encode("utf-8"))))
            with output.capture_json_output() as captured:
                status = main.main([
                    "resource-producer", operation, "--json", "--owner-ref", "m/p/owner",
                ])
            response: object = json.loads(captured.getvalue())
            assert status == 0 and isinstance(response, dict), response
            return response
        capability = invoke("output-capabilities", {})
        assert capability["allocator"] == capability["verified_delivery"] == 1
        produce_operation = next(name for name in capability["operations"] if name.endswith("produce"))
        deliver_operation = next(name for name in capability["operations"] if name.endswith("deliver"))
        text = "line one\r\nline two\n"
        request = {
            "topic": "Process report", "owner_roots": {"downloads": str(selected)},
            "files": {"report.txt": text, "empty.txt": ""},
            "operation_id": secrets.token_urlsafe(24),
        }
        delivered = invoke(produce_operation, request)
        receipt = delivered["producer"]["closeout"]["producer_verification"]["delivery"]
        destination = Path(receipt["destination"])
        assert (destination / "report.txt").read_bytes() == text.encode("utf-8")
        destination.rename(selected / "organized-by-user")
        record = tracking.load_record(path)
        record.status = "finalized"
        tracking.save_record(record, path)
        def no_storage(*args: Any, **options: Any) -> Any:
            raise AssertionError("completed process retry accessed user storage or current policy")
        patch.setattr(artifact_output, "_destination", no_storage)
        patch.setattr(config_sources, "configuration_report", no_storage)
        assert invoke(deliver_operation, {"token": delivered["token"]}) == delivered
        assert invoke(produce_operation, request) == delivered
        return {
            "phase": delivered["phase"], "content": receipt["content"],
            "replayed_original_receipt": True,
            "unsettled": sum(c.is_unsettled for c in tracking.load_record(path).resources),
        }


@pytest.mark.timeout(90)
def test_actual_process_produces_verifies_and_replays_after_user_move(tmp_path: Path) -> None:
    root = tmp_path / "process-fixture"
    root.mkdir()
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve()), "--drive", str(root)],
        capture_output=True, text=True, encoding="utf-8", timeout=60, env=dict(os.environ),
        creationflags=int(getattr(subprocess, "CREATE_NO_WINDOW", 0)),
    )
    assert result.returncode == 0, result.stdout + result.stderr
    response = json.loads(result.stdout)
    assert response["phase"] == "delivered" and response["replayed_original_receipt"]
    assert response["unsettled"] == 0 and response["content"]["files"] == 2


if __name__ == "__main__":
    if len(sys.argv) != 3 or sys.argv[1] != "--drive":
        raise SystemExit("use --drive with one fixture root")
    print(json.dumps(_drive(Path(sys.argv[2]))))
