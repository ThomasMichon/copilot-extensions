"""Host-owned central/local acceptance with bounded, verified Docker cleanup."""

from __future__ import annotations

import argparse
import importlib.util
import json
import os
import re
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[3]
MANIFEST = Path(__file__).with_name("manifest.json")
TEST_FILE = "tests/test_central_authority_docker.py"
STAGES = (
    "fresh-services", "authenticated-presence-only",
    "independent-task-authorities", "outage-does-not-transfer-ownership",
)
_spec = importlib.util.spec_from_file_location(
    "authority_case_owner", Path(__file__).parents[1] / "restricted-dispatch" / "run.py",
)
_owner = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_owner)
_run_case = _owner._run_case
_cleanup_docker = _owner._cleanup_docker


def resource_names(suffix: str) -> tuple[tuple[str, ...], tuple[str, ...]]:
    if not re.fullmatch(r"[0-9a-f]{12}", suffix):
        raise ValueError("authority resource suffix must be twelve lowercase hexadecimal digits")
    return (
        tuple(f"authority-proof-{part}-{suffix}" for part in ("central", "peer", "uid")),
        (f"authority-proof-net-{suffix}",),
    )


def read_owner(env) -> dict:
    """Admit only the host's preassigned resources and exclusively created evidence."""
    registry = Path(env.get("AGENT_DISPATCH_AUTHORITY_E2E_OWNER", ""))
    evidence = Path(env.get("AGENT_DISPATCH_AUTHORITY_E2E_EVIDENCE_DIR", ""))
    if not registry.is_absolute() or not evidence.is_absolute():
        raise ValueError("central/local acceptance requires the outer host owner")
    owner = json.loads(registry.read_text(encoding="utf-8"))
    containers, networks = resource_names(owner["suffix"])
    if (
        owner.get("schema") != "central-local-authority-owned-resources/v1"
        or owner.get("containers") != list(containers)
        or owner.get("networks") != list(networks)
        or owner.get("image") != env.get("AGENT_DISPATCH_E2E_CENTRAL_IMAGE")
        or owner.get("evidence") != str(evidence)
        or registry.resolve() != evidence.parent / "owned-resources.json"
        or evidence.exists()
    ):
        raise ValueError("outer host owner does not match this image, evidence or resource set")
    return owner


def verify_case(evidence: Path, owner: dict) -> dict:
    proof = json.loads((evidence / "cr-report.json").read_text(encoding="utf-8"))
    if (
        proof.get("scenario") != "central-local-authority"
        or proof.get("tier") != "P" or proof.get("family") != "F1"
        or proof.get("result") != "pass" or proof.get("cleanup") != "complete"
        or proof.get("jams") != []
        or proof.get("env", {}).get("image") != owner["image"]
        or proof.get("resources") != {
            "network": owner["networks"][0], "containers": owner["containers"],
        }
        or proof.get("stages") is None
        or [stage.get("name") for stage in proof["stages"]] != list(STAGES)
        or any(stage.get("status") != "pass" for stage in proof["stages"])
    ):
        raise ValueError("contained exit zero is not a complete, owner-bound authority proof")
    return proof


def _finish(root: Path, report: dict) -> dict:
    report["finished_at"] = datetime.now(timezone.utc).isoformat()
    (root / "cr-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def run(root: Path, *, central_image: str) -> dict:
    report = {
        "scenario": "central-local-authority", "tier": "P", "family": "F1",
        "result": "fail", "stages": [], "jams": [],
        "started_at": datetime.now(timezone.utc).isoformat(),
        "env": {"host_os": sys.platform, "central_image": central_image},
    }
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        if any(manifest.get(key) != value for key, value in {
            "name": "central-local-authority", "tier": "P", "family": "F1",
            "deadlines_seconds": {"pytest_item": 300, "contained_case": 360, "docker_command": 45},
        }.items()):
            raise ValueError("manifest must retain lane identity and original 300/360/45-second bounds")
        limits = manifest["deadlines_seconds"]
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        report["jams"].append({
            "category": "manifest-invalid", "evidence": str(MANIFEST), "error": str(exc),
            "hint": "Restore the declared lane identity and original deadlines before running.",
        })
        return _finish(root, report)
    suffix = uuid.uuid4().hex[:12]
    containers, networks = resource_names(suffix)
    evidence = root / "evidence"
    registry = root / "owned-resources.json"
    owner = {
        "schema": "central-local-authority-owned-resources/v1",
        "suffix": suffix, "image": central_image, "evidence": str(evidence),
        "containers": list(containers), "networks": list(networks),
    }
    env = dict(os.environ)
    env.update({
        "AGENT_DISPATCH_CENTRAL_AUTHORITY_E2E": "1",
        "AGENT_DISPATCH_E2E_CENTRAL_IMAGE": central_image,
        "AGENT_DISPATCH_AUTHORITY_E2E_EVIDENCE_DIR": str(evidence),
        "AGENT_DISPATCH_AUTHORITY_E2E_OWNER": str(registry),
    })
    started = time.monotonic()
    stage = {"case": "central-local-authority", "status": "fail", "log": "runner.log"}
    phase = "runner"
    launched = False
    try:
        with registry.open("x", encoding="utf-8") as out:
            json.dump(owner, out, indent=2)
            out.write("\n")
            out.flush()
            os.fsync(out.fileno())
        with (root / "runner.log").open("x", encoding="utf-8") as log:
            launched = True
            returncode = _run_case(
                [sys.executable, str(REPO / "tools" / "run-plugin-tests.py"),
                 "agent-dispatch", "--test-file", TEST_FILE,
                 "--test-timeout", str(limits["pytest_item"]),
                 "--timeout", str(limits["contained_case"]),
                 "--plugin-timeout", str(limits["contained_case"])],
                env=env, log=log, timeout=limits["contained_case"],
            )
        phase = "runner-exit"
        if returncode:
            raise ValueError(f"Contained case exited {returncode}")
        phase = "proof-invalid"
        verify_case(evidence, owner)
        stage["status"] = "pass"
    except (OSError, ValueError, KeyError, TypeError, AttributeError, subprocess.TimeoutExpired) as exc:
        report["jams"].append({
            "category": "contained-timeout" if isinstance(exc, subprocess.TimeoutExpired) else phase,
            "evidence": "runner.log", "error_type": type(exc).__name__, "error": str(exc),
            "hint": "Inspect the original failure; do not resample, increase budgets or accept missing proof.",
        })
    finally:
        if launched:
            try:
                _cleanup_docker(containers, networks, deadline=started + limits["contained_case"])
                stage["owned_resource_cleanup"] = "complete"
            except AssertionError as exc:
                stage["status"] = "fail"
                stage["owned_resource_cleanup"] = "failed"
                report["jams"].append({
                    "category": "cleanup-incomplete", "evidence": str(registry), "error": str(exc),
                    "hint": "Reconcile only the recorded exact resources; unavailable inventory is not absence.",
                })
        stage["duration_seconds"] = time.monotonic() - started
        if stage["duration_seconds"] > limits["contained_case"]:
            stage["status"] = "fail"
            report["jams"].append({
                "category": "contained-timeout", "evidence": "runner.log",
                "error": "Execution and verified cleanup exceeded the original 360-second budget",
                "hint": "Keep execution, owner unwinding and cleanup inside the same original deadline.",
            })
        report["stages"].append(stage)
        report["result"] = stage["status"]
        _finish(root, report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--central-image", required=True)
    parser.add_argument("--results-root", required=True, type=Path)
    args = parser.parse_args()
    supplied = args.results_root.absolute()
    if any(
        path.is_symlink() or (os.name == "nt" and (
            getattr(path.lstat(), "st_file_attributes", 0) & stat.FILE_ATTRIBUTE_REPARSE_POINT
        ))
        for path in (supplied, *supplied.parents)
    ):
        parser.error("--results-root must not contain link indirection")
    root = supplied.resolve()
    if not root.is_dir() or root == REPO or REPO in root.parents:
        parser.error("--results-root must be an existing private scratch directory outside the repository")
    run_dir = Path(tempfile.mkdtemp(
        prefix="central-local-authority-" + datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-"),
        dir=root,
    ))
    report = run(run_dir, central_image=args.central_image)
    print(json.dumps({"results": str(run_dir), **report}))
    return 0 if report["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
