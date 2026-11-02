"""Strict process result and complete descendant artifact publication."""

from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Callable

if __package__:
    from . import native_qualification_contract as contract
    from .native_process_sources import digest
else:
    import native_qualification_contract as contract
    from native_process_sources import digest


def write_new(path: Path, value: Any) -> None:
    with path.open("x", encoding="utf-8") as target:
        json.dump(value, target, sort_keys=True, ensure_ascii=True)
        target.write("\n")


def candidate_result(
    path: Path, sources: list[dict[str, Any]], boundary: contract.Boundary,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    boundary.require(clock, work=True)
    with path.open("rb") as source:
        data = source.read(contract.STREAM_BYTES + 1)
    if path.resolve() != path or len(data) > contract.STREAM_BYTES:
        raise contract.QualificationError("Process candidate evidence is redirected or unbounded.")
    value = contract.decode_object(data)
    workers = value.get("workers")
    owner = value.get("process_owner")
    if (
        value.get("kind") != "sdk-process-safety" or value.get("passed") is not True
        or value.get("desktop_observed") is not False or value.get("desktop_qualified") is not False
        or type(value.get("visible_controls_attempted")) is not int or value["visible_controls_attempted"] != 0
        or value.get("source_before") != sources or value.get("source_after") != sources
        or not isinstance(owner, dict) or set(owner) != {"inherited_job_membership", "elevated"}
        or owner["inherited_job_membership"] is not True or owner["elevated"] is not False
        or not isinstance(workers, list) or len(workers) != 4
        or any(
            not isinstance(worker, dict) or type(worker.get("packet")) is not int or worker["packet"] != i
            or worker.get("succeeded") is not True or worker.get("cleanup_confirmed") is not True
            or type(worker.get("exit")) is not int or worker["exit"] != 0
            or worker.get("cleanup_errors") != [] or worker.get("reader_error") is not None
            or not isinstance(worker.get("readers_alive"), list) or len(worker["readers_alive"]) != 2
            or any(alive is not False for alive in worker["readers_alive"])
            for i, worker in enumerate(workers)
        )
    ):
        raise contract.QualificationError("Process candidate is not an exact settled four-worker result.")
    boundary.require(clock, work=True)
    return value


def index(
    root: Path, *, qualification: contract.Qualification, summary: dict[str, Any],
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    write_new(root / "summary.json", summary)
    artifacts = []
    pending = [root]
    while pending:
        qualification.boundary.require(clock)
        directory = pending.pop()
        with os.scandir(directory) as entries:
            for entry in entries:
                path = Path(entry.path)
                qualification.boundary.require(clock)
                if entry.is_symlink() or path.resolve() != path:
                    raise contract.QualificationError("Process artifacts contain an indirect path.")
                if entry.is_dir(follow_symlinks=False):
                    pending.append(path)
                elif entry.is_file(follow_symlinks=False):
                    artifacts.append({
                        "path": path.relative_to(root).as_posix(), "bytes": path.stat().st_size,
                        "sha256": digest(path, qualification.boundary, clock),
                    })
                else:
                    raise contract.QualificationError("Process artifacts contain an unsupported entry.")
    write_new(root / "sha256.json", {
        "preparation_manifest_sha256": qualification.manifest_sha256,
        "execution_scope": qualification.scope, "artifact_count": len(artifacts),
        "artifacts": sorted(artifacts, key=lambda row: row["path"]),
    })
    return {
        **summary, "root": str(root),
        "summary_sha256": digest(root / "summary.json", qualification.boundary, clock),
        "index_sha256": digest(root / "sha256.json", qualification.boundary, clock),
    }
