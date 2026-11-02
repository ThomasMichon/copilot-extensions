"""Non-owning process-only stage. All processes inherit the original Job."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import subprocess
import sys
import time
from typing import Any

if not __package__:
    sys.path.insert(0, str(Path(__file__).resolve().parent))
    import native_qualification_contract as contract
    import native_qualification_receiver as receiver
    import native_process_artifacts as artifacts
    import native_process_sources as sources
else:
    from . import native_qualification_contract as contract
    from . import native_qualification_receiver as receiver
    from . import native_process_artifacts as artifacts
    from . import native_process_sources as sources


def candidate(
    argv: list[str], output: Path, boundary: contract.Boundary,
) -> int:
    """Capture a child, not a second containment owner; failure stays held."""
    boundary.require(time.monotonic, work=True)
    process = subprocess.Popen(
        argv, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert process.stdout is not None and process.stderr is not None
    capture = contract.Capture(streams=(contract.Stream(), contract.Stream()))
    try:
        capture.start((process.stdout, process.stderr))
        code = process.wait(timeout=boundary.remaining(work=True, clock=time.monotonic))
        capture.finish((process.stdout, process.stderr), boundary, time.monotonic)
        boundary.require(time.monotonic, work=True)
        return code
    finally:
        # A timeout never creates an independent kill/retry owner. The original
        # outside Job retains all descendants and refuses positive settlement.
        if process.poll() is not None:
            handle = getattr(process, "_handle", None)
            if handle is None:
                raise contract.QualificationError("Process candidate handle is unavailable.")
            handle.Close()
        for name, stream in zip(("stdout", "stderr"), capture.streams):
            with (output / f"candidate-{name}.bin").open("xb") as target:
                target.write(stream.data)


def run(
    *, scope: int, acceptance: str, manifest_sha256: str, runtime_root: Path,
    original_start_monotonic: float, original_start_utc: str,
) -> dict[str, Any]:
    boundary = contract.Boundary.supplied(original_start_monotonic, original_start_utc, time.monotonic)
    if os.name != "nt" or not acceptance.strip() or type(scope) is not int or scope <= 0:
        raise contract.QualificationError("Process stage requires its accepted native scope.")
    if (
        not runtime_root.is_absolute() or runtime_root.resolve() != runtime_root
        or not runtime_root.is_dir()
    ):
        raise contract.QualificationError("Process stage requires the original existing runtime root.")
    launcher = Path(__file__).with_name(sources.LAUNCHER).resolve()
    root, prepared, rows = sources.load(launcher, manifest_sha256, boundary, time.monotonic)
    admission_path = runtime_root / "evidence" / "owner-admission.json"
    sources.ordinary(admission_path)
    admission = contract.decode_object(receiver._read_bounded(admission_path, boundary, time.monotonic))
    if admission != {
        "scope": scope, "acceptance": acceptance, "manifest_sha256": manifest_sha256,
        "monotonic_start": boundary.start, "started_utc": boundary.utc, "runtime_root": str(runtime_root),
    }:
        raise contract.QualificationError("Process stage differs from its original outside-owner admission.")
    receiver._require_existing_job()
    runner, sdk_sources = sources.sdk(root, rows, prepared["case_packets"])
    boundary.require(time.monotonic, work=True)
    output = runtime_root / "artifacts"
    output.mkdir()
    request = {
        "nonce": prepared["nonce"], "deadline": boundary.start + contract.WORK_SECONDS,
        "original_start_monotonic": boundary.start,
        "output": str(output / "candidate"), "python": sys.executable,
        "sdk_root": str(root), "sources": sdk_sources, "case_packets": prepared["case_packets"],
    }
    request_path = output / "candidate-request.json"
    artifacts.write_new(request_path, request)
    boundary.require(time.monotonic, work=True)
    print(f"PROCESS SAFETY START: scope {scope}; process only", flush=True)
    code = candidate(
        [sys.executable, "-I", "-B", "-X", "utf8", runner.__file__, "process-safety", str(request_path)],
        output, boundary,
    )
    if type(code) is not int or code != 0:
        raise contract.QualificationError(f"Process candidate exited with {code}; no positive result.")
    report = artifacts.candidate_result(
        output / "candidate/process-safety-result.json", sdk_sources, boundary, time.monotonic,
    )
    junit = runner.verify_native_junit(output / "candidate", root, prepared["case_packets"])
    if json.loads(json.dumps(junit)) != report.get("native_junit") or junit.get("case_count") != 44:
        raise contract.QualificationError("Process candidate JUnit differs from its exact 44-case roster.")
    sources.load(launcher, manifest_sha256, boundary, time.monotonic)
    qualification = contract.Qualification(boundary, scope, manifest_sha256, runtime_root, mode=contract.PROCESS_MODE)
    contract.publish_work_completion(
        qualification, candidate_exit=code, workers_exited=True, clock=time.monotonic,
    )
    terminal = artifacts.index(output, qualification=qualification, summary={
        "execution_scope": scope, "qualification": contract.PROCESS_MODE,
        "desktop_observed": False, "desktop_qualified": False, "visible_controls_attempted": 0,
        "process_safety_positive": True, "monotonic_start": boundary.start,
        "evidence_phase": contract.DESCENDANT_PHASE, "original_job_drained": False,
        "original_owner_settlement_required": True, "controller_disposed": True,
        "publication_state": receiver.PROVISIONAL, "final_success_requires_post_publication_zero_exit": True,
    }, clock=time.monotonic)
    boundary.require(time.monotonic)
    return terminal


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scope", required=True, type=int)
    parser.add_argument("--acceptance", required=True)
    parser.add_argument("--manifest-sha256", required=True)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--original-start-monotonic", required=True, type=float)
    parser.add_argument("--original-start-utc", required=True)
    args = parser.parse_args()
    result = run(**vars(args))
    sys.stdout.write(json.dumps(result, ensure_ascii=True) + "\n")
    sys.stdout.flush()
    return 0 if time.monotonic() < args.original_start_monotonic + contract.WHOLE_SECONDS else 1


if __name__ == "__main__":
    raise SystemExit(main())
