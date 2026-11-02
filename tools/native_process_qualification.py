"""Explicit outside-driver entrypoint for one accepted process-only qualification."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import time
from typing import Any

sys.dont_write_bytecode = True

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


def require_capacity() -> None:
    import ctypes

    class Memory(ctypes.Structure):
        _fields_ = [
            ("length", ctypes.c_uint32), ("load", ctypes.c_uint32),
            *[(name, ctypes.c_uint64) for name in (
                "total_physical", "available_physical", "total_page", "available_page",
                "total_virtual", "available_virtual", "extended_virtual",
            )],
        ]

    status = Memory()
    status.length = ctypes.sizeof(status)
    query = ctypes.WinDLL("kernel32", use_last_error=True).GlobalMemoryStatusEx
    query.argtypes, query.restype = [ctypes.POINTER(Memory)], ctypes.c_int32
    if not query(ctypes.byref(status)):
        raise contract.QualificationError(f"Native memory admission unavailable: WinError {ctypes.get_last_error()}.")
    if status.available_physical < 8 * 1024**3:
        raise contract.QualificationError("Native qualification requires the original 8 GiB available memory admission.")


def execute(
    request_path: Path, *, runtime_root: Path,
    original_start_monotonic: float, original_start_utc: str,
) -> dict[str, Any]:
    boundary = contract.Boundary.supplied(original_start_monotonic, original_start_utc, time.monotonic)
    if os.name != "nt":
        raise contract.QualificationError("Process qualification requires native Windows; no fallback.")
    if (
        not runtime_root.is_absolute() or runtime_root.resolve() != runtime_root
        or not runtime_root.is_dir() or any(runtime_root.iterdir())
    ):
        raise contract.QualificationError("Outside driver requires the original existing empty ordinary root.")
    sources.ordinary(request_path)
    request = contract.decode_object(receiver._read_bounded(request_path, boundary, time.monotonic))
    if set(request) != {"command", "scope", "cwd", "manifest_sha256"}:
        raise contract.QualificationError("Process qualification retains the exact four-field receiver request.")
    command, scope, manifest_sha256 = request["command"], request["scope"], request["manifest_sha256"]
    if type(scope) is not int or scope <= 0 or not isinstance(request["cwd"], str):
        raise contract.QualificationError("Process qualification requires its exact accepted scope and parent.")
    cwd = Path(request["cwd"])
    if cwd != runtime_root.parent or cwd.resolve() != cwd:
        raise contract.QualificationError("Process qualification cannot replace its original parent/root.")
    receiver._command(command, scope, manifest_sha256, boundary, time.monotonic, runtime_root=runtime_root)
    launcher = Path(command[5])
    if launcher != Path(__file__).with_name(sources.LAUNCHER).resolve():
        raise contract.QualificationError("Outside process driver cannot select a foreign or functional launcher.")
    source_root, prepared, rows = sources.load(launcher, manifest_sha256, boundary, time.monotonic)
    if runtime_root.is_relative_to(source_root):
        raise contract.QualificationError("Original runtime evidence must remain outside the immutable prepared tree.")
    sources.sdk(source_root, rows, prepared["case_packets"])
    # Import the sole owner only after its complete source-tree admission.
    if __package__:
        from . import plugin_test_containment as owner
    else:
        import plugin_test_containment as owner
    sources.load(launcher, manifest_sha256, boundary, time.monotonic)
    boundary.require(time.monotonic, work=True)
    evidence = runtime_root / "evidence"
    evidence.mkdir()
    artifacts.write_new(evidence / "owner-admission.json", {
        "scope": scope, "acceptance": command[10], "manifest_sha256": manifest_sha256,
        "monotonic_start": boundary.start, "started_utc": boundary.utc, "runtime_root": str(runtime_root),
    })
    # The retained copy, not a mutable transport file, is consumed by the worker.
    sealed_request = evidence / "receiver-request.json"
    artifacts.write_new(sealed_request, request)
    qualification = contract.Qualification(
        boundary, scope, manifest_sha256, runtime_root, mode=contract.PROCESS_MODE,
    )
    env = owner.isolated_environment(os.environ, runtime_root)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    require_capacity()
    boundary.require(time.monotonic, work=True)
    result = owner.run_contained(
        [
            sys.executable, "-B", "-X", "utf8", str(launcher.with_name("native_qualification_receiver.py")),
            str(sealed_request), "--original-start-monotonic", repr(boundary.start),
            "--original-start-utc", boundary.utc, "--runtime-root", str(runtime_root),
        ],
        cwd=cwd, env=env, sandbox=runtime_root, limits=owner.Limits(wall_seconds=contract.WORK_SECONDS),
        qualification=qualification,
    )
    qualification.require_settled(time.monotonic)
    if type(result) is not int or result != 0:
        raise contract.QualificationError("Original containment owner did not return zero.")
    sources.load(launcher, manifest_sha256, boundary, time.monotonic)
    for name, stream in zip(("stdout", "stderr"), qualification.capture.streams):
        with (evidence / f"receiver-{name}.bin").open("xb") as target:
            target.write(stream.data)
    report = {
        "kind": "native-process-qualification", "passed": True,
        "qualification": contract.PROCESS_MODE, "evidence_phase": "original-owner-settled-v1",
        "scope": scope, "manifest_sha256": manifest_sha256, "runtime_root": str(runtime_root),
        "monotonic_start": boundary.start, "started_utc": boundary.utc,
        "original_job_drained": qualification.job_zero_observed,
        "original_job_handle_closed": qualification.job_handle_closed,
        "worker_reaped": qualification.worker_reaped, "worker_handle_closed": qualification.worker_handle_closed,
        "lifecycle_settled": qualification.lifecycle_settled, "registry_unchanged": qualification.registry_unchanged,
        "forced_termination": qualification.forced_termination,
        "desktop_observed": False, "desktop_qualified": False, "visible_controls_attempted": 0,
        "publication_state": receiver.PROVISIONAL, "final_success_requires_post_publication_zero_exit": True,
        "receiver": qualification.receiver,
    }
    boundary.require(time.monotonic)
    artifacts.write_new(evidence / "owner-result.json", report)
    boundary.require(time.monotonic)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    parser.add_argument("--execute-accepted-scope", required=True, action="store_true")
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--original-start-monotonic", required=True, type=float)
    parser.add_argument("--original-start-utc", required=True)
    args = parser.parse_args()
    report = execute(
        args.request, runtime_root=args.runtime_root,
        original_start_monotonic=args.original_start_monotonic, original_start_utc=args.original_start_utc,
    )
    sys.stdout.write(json.dumps(report, ensure_ascii=True) + "\n")
    sys.stdout.flush()
    return 0 if time.monotonic() < args.original_start_monotonic + contract.WHOLE_SECONDS else 1


if __name__ == "__main__":
    raise SystemExit(main())
