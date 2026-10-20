"""Explicit opt-in, credential-free host-side Gateway container witness."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import sys
import time
from pathlib import Path

from engine import Engine
from source import ProofError, fresh_run, materialize, private_root, save
from witness import Witness

TERMINATION_SIGNALS = (signal.SIGTERM, signal.SIGINT)


class RunnerTerminated(BaseException):
    """Control flow, not a retryable operation failure."""


def shield_termination() -> None:
    for number in TERMINATION_SIGNALS:
        signal.signal(number, signal.SIG_IGN)


def report_template() -> dict:
    """Keep the existing clean-room report vocabulary and top-level shape."""
    return {
        "scenario": "gateway-role-tunnels", "copilot_version": "not-used",
        "ran_until_phase": 0, "passed": 0, "failed": 0, "results": [],
        "env": {"tools": [], "configs": [], "path": "not-collected"},
        "jams": [],
    }


def run(repository: Path, ref: str, root: Path, build_timeout: int = 900,
        architecture: str = "amd64") -> dict:
    previous = {number: signal.getsignal(number) for number in TERMINATION_SIGNALS}

    def terminated(signum: int, frame: object) -> None:
        shield_termination()
        raise RunnerTerminated("runner-terminated")

    for number in TERMINATION_SIGNALS:
        signal.signal(number, terminated)
    try:
        return _run(repository, ref, root, build_timeout, architecture)
    finally:
        for number, handler in previous.items():
            signal.signal(number, handler)


def _run(repository: Path, ref: str, root: Path, build_timeout: int,
         architecture: str) -> dict:
    report = report_template()
    receipt = {
        "version": 1, "result": "fail", "cleanup": "pending",
        "started_at": int(time.time()), "runner_pid": os.getpid(),
        "host_socket_granted": False, "privileged_nested_target": False,
        "host_ports_published": False, "credentials": "synthetic-files-only",
        "build_timeout_seconds": build_timeout, "witness_budget_seconds": 300,
        "requested_architecture": architecture,
        "scope": "native isolated synthetic direct-TLS witness; no provider/live adoption",
    }
    engine = None
    try:
        receipt.update(materialize(repository, ref, root))
        if shutil.which("docker") is None:
            raise ProofError("docker-unavailable")
        engine = Engine(root, receipt, architecture)
        engine.journal()
        engine.preflight()
        image = engine.build(receipt["source_commit"], build_timeout)
        engine.deadline = time.monotonic() + 300
        witness = Witness(engine, image, report, receipt)
        witness.passed(f"Pinned canonical source closure built for inspected {architecture} image", {
            key: receipt[key] for key in ("source_commit", "source_manifest_sha256",
                                         "image_id", "architecture")
        })
        witness.run()
        receipt["result"] = "pass"
    except (RunnerTerminated, ProofError, OSError, ValueError, KeyError, TypeError) as exc:
        code = (str(exc) if isinstance(exc, (RunnerTerminated, ProofError))
                else "host-proof-operation-failed")
        category = exc.category if isinstance(exc, ProofError) else "runtime-install"
        receipt["error"] = code
        report["failed"] += 1
        report["results"].append({"kind": "FAIL", "phase": report["ran_until_phase"] + 1,
                                  "msg": code})
        report["jams"].append({"category": category,
                               "evidence": "cr-logs/" if engine else "receipt.json",
                               "hint": code})
    finally:
        # Repeated termination cannot interrupt exact-ID teardown or its receipts.
        shield_termination()
        errors = engine.cleanup() if engine else []
        receipt["cleanup"] = "failed" if errors else "complete"
        if errors:
            receipt["result"] = "fail"
            report["failed"] += 1
            report["results"].append({"kind": "FAIL", "phase": report["ran_until_phase"],
                                      "msg": "exact-owned-cleanup-failed"})
            report["jams"].append({"category": "runtime-install", "evidence": "receipt.json",
                                   "hint": "inspect exact owned cleanup receipts"})
        receipt["finished_at"] = int(time.time())
        report["source_commit"] = receipt.get("source_commit")
        report["image_id"] = receipt.get("image_id")
        report["result"] = receipt["result"]
        save(root / "receipt.json", receipt)
        save(root / "cr-report.json", report)
    return receipt


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--source-commit")
    source.add_argument("--source-ref")
    parser.add_argument("--repository", type=Path, default=Path(__file__).resolve().parents[3])
    parser.add_argument("--results-root", type=Path, required=True,
                        help="Existing owner-private directory outside repositories/session state")
    parser.add_argument("--build-timeout", type=int, default=900)
    parser.add_argument("--architecture", choices=("amd64", "arm64"), default="amd64",
                        help="Required native engine/image architecture (default: amd64)")
    args = parser.parse_args(argv)
    if sys.platform != "linux":
        parser.error("Requires a native Linux/WSL host with an existing Linux Docker engine")
    if not 30 <= args.build_timeout <= 1800:
        parser.error("--build-timeout must be between 30 and 1800 seconds")
    if args.source_commit and (len(args.source_commit) != 40
                               or any(c not in "0123456789abcdef" for c in args.source_commit)):
        parser.error("--source-commit requires the exact full lowercase Git SHA")
    try:
        root = private_root(args.results_root, args.repository.resolve(strict=True))
        run_root = fresh_run(root)
    except (ProofError, OSError) as exc:
        parser.error(str(exc) if isinstance(exc, ProofError) else "results-root-unavailable")
    receipt = run(args.repository, args.source_commit or args.source_ref,
                  run_root, args.build_timeout, args.architecture)
    print(json.dumps({"results": str(run_root), "result": receipt["result"],
                      "cleanup": receipt["cleanup"]}))
    return 0 if receipt["result"] == "pass" else 1


if __name__ == "__main__":
    raise SystemExit(main())
