"""Host-driven Tier-P proof; all actual task operations run in fresh containers."""

from __future__ import annotations

import argparse
import json
import os
import stat
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MANIFEST = Path(__file__).with_name("manifest.json")
TEST_FILE = "tests/test_restricted_supervisor_docker.py"
NO_WINDOW = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}


def verify_case(root: Path, outcome: str) -> dict:
    proof = json.loads((root / "proof.json").read_text(encoding="utf-8"))
    stages = [
        json.loads(line) for line in (root / "stages.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    expected = "completed" if outcome == "complete" else "abandoned"
    checks = [
        ("terminal outcome differs", proof["outcome"] == outcome and proof["status"] == expected),
        ("native identity changed", bool(proof["native"]) and (
            proof["native"] == proof["owner_native"] == proof["bridge_native"]
        )),
        ("expected one prompt", proof["prompts"] == 1),
        ("credential did not rotate", proof["rotated"] is True),
        ("terminal credential was not refused", proof["terminal_credential_status"] == 403),
        ("expected five refused routes", len(proof["refused"]) == 5),
        ("route was not refused", all(item["status"] == 403 for item in proof["refused"])),
        ("acceptance did not complete exactly once", sum(
            row["stage"] == "acceptance" and row["event"] == "complete" for row in stages
        ) == 1),
        ("cleanup did not complete", bool(stages) and (
            stages[-1]["stage"] == "cleanup" and stages[-1]["event"] == "complete"
        )),
    ]
    for message, passed in checks:
        if not passed:
            raise ValueError(message)
    return proof


def run(root: Path, *, central_image: str, bridge_python: Path, provider_python: Path) -> dict:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    report = {
        "scenario": manifest["name"], "tier": manifest["tier"], "family": manifest["family"],
        "result": "fail", "stages": [], "jams": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "env": {"host_os": sys.platform, "central_image": central_image},
    }
    for outcome in manifest["cases"]:
        case = root / outcome
        case.mkdir()
        # The contained fixture creates its evidence directory exclusively.
        evidence = case / "evidence"
        env = dict(os.environ)
        env.update({
            "AGENT_DISPATCH_RESTRICTED_E2E": "1",
            "AGENT_DISPATCH_E2E_CENTRAL_IMAGE": central_image,
            "AGENT_DISPATCH_E2E_BRIDGE_PYTHON": str(bridge_python),
            "AGENT_DISPATCH_E2E_PROVIDER_PYTHON": str(provider_python),
            "AGENT_DISPATCH_E2E_EVIDENCE_DIR": str(evidence),
        })
        started = time.monotonic()
        stage = {"case": outcome, "status": "fail", "log": f"{outcome}/runner.log"}
        phase = "runner"
        try:
            with (case / "runner.log").open("wb") as log:
                process = subprocess.run(
                    [sys.executable, str(REPO / "tools" / "run-plugin-tests.py"),
                     "agent-dispatch", "--test-file", TEST_FILE, "-k", outcome,
                     "--test-timeout", "300", "--timeout", "360", "--plugin-timeout", "360"],
                    cwd=REPO, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                    timeout=360, **NO_WINDOW,
                )
            if process.returncode:
                raise ValueError(f"Contained case exited {process.returncode}")
            phase = "proof"
            verify_case(evidence, outcome)
            stage["status"] = "pass"
        except (OSError, ValueError, KeyError, IndexError, TypeError, subprocess.TimeoutExpired) as exc:
            stage["error_type"] = type(exc).__name__
            stage["error"] = str(exc)
            category = (
                "contained-timeout" if isinstance(exc, subprocess.TimeoutExpired)
                else "proof-invalid" if phase == "proof"
                else "runner-exit" if isinstance(exc, ValueError)
                else "runner-io"
            )
            hints = {
                "contained-timeout": "Inspect the contained deadline failure; do not increase the budget or resample.",
                "proof-invalid": "Inspect proof.json and stages.jsonl; exit zero is not complete authority evidence.",
                "runner-exit": "Inspect the contained runner log and fix its actual failing contract.",
                "runner-io": "Check prepared runtime paths and the caller-owned evidence directory permissions.",
            }
            report["jams"].append({
                "category": category, "evidence": stage["log"], "hint": hints[category],
                "error_type": stage["error_type"], "error": stage["error"],
            })
        stage["duration_seconds"] = time.monotonic() - started
        report["stages"].append(stage)
        if stage["status"] != "pass":
            break
    if len(report["stages"]) == len(manifest["cases"]) and all(
        stage["status"] == "pass" for stage in report["stages"]
    ):
        report["result"] = "pass"
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (root / "cr-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--central-image", required=True, help="Candidate services/dispatch image")
    parser.add_argument("--bridge-python", required=True, type=Path)
    parser.add_argument("--provider-python", required=True, type=Path)
    parser.add_argument("--results-root", required=True, type=Path, help="Existing private scratch root")
    args = parser.parse_args()
    supplied = args.results_root.absolute()
    if any(
        path.is_symlink()
        or (os.name == "nt" and (
            getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ))
        for path in (supplied, *supplied.parents)
    ):
        parser.error("--results-root must not contain link indirection")
    root = supplied.resolve()
    if not root.is_dir() or root == REPO or REPO in root.parents:
        parser.error("--results-root must be an existing directory outside the repository")
    for python in (args.bridge_python, args.provider_python):
        if not python.is_absolute() or not python.is_file():
            parser.error("Prepared runtime interpreters must be existing absolute files")
    run_dir = Path(tempfile.mkdtemp(
        prefix="restricted-dispatch-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-"),
        dir=root,
    ))
    report = run(
        run_dir, central_image=args.central_image,
        bridge_python=args.bridge_python, provider_python=args.provider_python,
    )
    print(json.dumps({"results": str(run_dir), **report}))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
