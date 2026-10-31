"""Capture a finite native qualification launcher inside its existing caller Job."""

from __future__ import annotations

import argparse
import base64
from dataclasses import dataclass, field
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import threading
import time
from typing import Any, BinaryIO, Callable

WHOLE_SECONDS = 180
WORK_SECONDS = 165
STREAM_BYTES = 1024 * 1024
PROVISIONAL = "provisional-requires-zero-exit-after-output-flush-and-fresh-whole-bound"


class ReceiverError(RuntimeError):
    pass


@dataclass(frozen=True)
class Boundary:
    start: float
    utc: str

    def require(self, clock: Callable[[], float], *, work: bool = False) -> float:
        now = clock()
        elapsed = now - self.start
        if not math.isfinite(now) or not math.isfinite(self.start) or not 0 <= elapsed < (
            WORK_SECONDS if work else WHOLE_SECONDS
        ):
            raise ReceiverError("Original qualification deadline exhausted; no replay or rearming.")
        return now


@dataclass
class Stream:
    data: bytearray = field(default_factory=bytearray)
    errors: list[str] = field(default_factory=list)

    def read(self, source: BinaryIO) -> None:
        try:
            while chunk := source.read(4096):
                if len(self.data) + len(chunk) > STREAM_BYTES:
                    raise ReceiverError("Native launcher output exceeded its bounded stream.")
                self.data.extend(chunk)
        except (OSError, ValueError, ReceiverError) as error:
            self.errors.append(f"{type(error).__name__}: {error}")


def _object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ReceiverError("Native evidence contains duplicate JSON keys.")
        result[key] = value
    return result


def _json(data: bytes) -> dict[str, Any]:
    value = json.loads(data.decode("utf-8"), object_pairs_hook=_object)
    if not isinstance(value, dict):
        raise ReceiverError("Native evidence must be a JSON object.")
    return value


def terminal(
    output: bytes, *, boundary: Boundary, scope: int, native_exit: int,
    captured_at: float,
) -> dict[str, Any]:
    prefix, separator, data = output.partition(b"\n")
    if not separator or not prefix.startswith(f"FUNCTIONAL START: scope {scope};".encode("ascii")):
        raise ReceiverError("Native launcher did not publish its expected start and terminal.")
    value = _json(data)
    if (
        type(native_exit) is not int or native_exit != 0
        or type(scope) is not int or scope <= 0
        or type(value.get("execution_scope")) is not int or value["execution_scope"] != scope
        or value.get("qualification") != "functional-only"
        or value.get("desktop_qualified") is not False
        or type(value.get("visible_controls_attempted")) is not int
        or value["visible_controls_attempted"] != 0
        or value.get("functional_positive") is not True
        or value.get("original_job_drained") is not True
        or value.get("controller_disposed") is not True
        or value.get("publication_state") != PROVISIONAL
        or value.get("final_success_requires_post_publication_zero_exit") is not True
        or type(value.get("monotonic_start")) not in (int, float)
        or value["monotonic_start"] != boundary.start
        or not math.isfinite(captured_at)
        or not 0 <= captured_at - boundary.start < WHOLE_SECONDS
    ):
        raise ReceiverError("Native exit, terminal custody or original-clock capture is unqualified.")
    for name in ("summary_sha256", "index_sha256"):
        if not isinstance(value.get(name), str) or re.fullmatch(r"[a-f0-9]{64}", value[name]) is None:
            raise ReceiverError("Native terminal lacks an exact final artifact digest.")
    return value


def _digest(path: Path, boundary: Boundary, clock: Callable[[], float]) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        while True:
            boundary.require(clock)
            chunk = stream.read(65536)
            if not chunk:
                break
            value.update(chunk)
    boundary.require(clock)
    return value.hexdigest()


def _read_bounded(path: Path, boundary: Boundary, clock: Callable[[], float]) -> bytes:
    boundary.require(clock)
    with path.open("rb") as source:
        data = source.read(STREAM_BYTES + 1)
    boundary.require(clock)
    if len(data) > STREAM_BYTES:
        raise ReceiverError("Native evidence exceeded its bounded input.")
    return data


def _command(
    command: list[str], scope: int, manifest_sha256: str,
    boundary: Boundary, clock: Callable[[], float],
) -> list[str]:
    if (
        not isinstance(command, list) or len(command) != 13
        or any(not isinstance(arg, str) or not arg or "\0" in arg for arg in command)
        or command[1:5] != ["-NoLogo", "-NoProfile", "-NonInteractive", "-File"]
        or command[6:10] != ["-ExecuteAcceptedScope", "-ExecutionScope", str(scope), "-ParentAcceptanceReference"]
        or command[11:] != ["-ExpectedPreparedManifestSha256", manifest_sha256]
        or not command[10].strip()
    ):
        raise ReceiverError("Receiver command is not the exact accepted native launcher invocation.")
    launcher = Path(command[5])
    if not launcher.is_absolute() or launcher.resolve() != launcher or not launcher.is_file():
        raise ReceiverError("Receiver launcher must be an ordinary absolute prepared file.")
    manifest = launcher.with_name("prepared-manifest.json")
    manifest_data = _read_bounded(manifest, boundary, clock)
    if hashlib.sha256(manifest_data).hexdigest() != manifest_sha256:
        raise ReceiverError("Receiver preparation manifest changed or exceeded its bound.")
    rows = _json(manifest_data)["files"]
    if not isinstance(rows, list) or not rows:
        raise ReceiverError("Receiver preparation source closure is unavailable.")
    names = set()
    for row in rows:
        if (
            not isinstance(row, dict) or set(row) != {"name", "bytes", "sha256"}
            or not isinstance(row["name"], str) or Path(row["name"]).name != row["name"]
            or row["name"] in {"", ".", ".."} or any(c in row["name"] for c in ("\\", "/", ":", "\0"))
            or row["name"].casefold() in names or type(row["bytes"]) is not int or row["bytes"] < 0
        ):
            raise ReceiverError("Receiver preparation source closure contains unsafe or duplicate names.")
        names.add(row["name"].casefold())
        path = launcher.parent / row["name"]
        if (
            path.resolve() != path or not path.is_file() or path.stat().st_size != row["bytes"]
            or _digest(path, boundary, clock) != row["sha256"]
        ):
            raise ReceiverError("Receiver preparation source closure changed.")
    if launcher.name.casefold() not in names or "native_qualification_receiver.py" not in names:
        raise ReceiverError("Receiver and launcher must both belong to the accepted prepared closure.")
    if Path(__file__).resolve() != launcher.with_name("native_qualification_receiver.py"):
        raise ReceiverError("Receiver belongs to a different source preparation.")
    return [
        *command, "-OriginalStartMonotonic", repr(boundary.start),
        "-OriginalStartUtc", boundary.utc,
    ]


def _require_existing_job() -> None:
    import ctypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    current = kernel.GetCurrentProcess
    current.argtypes, current.restype = [], ctypes.c_void_p
    query = kernel.IsProcessInJob
    query.argtypes = [ctypes.c_void_p, ctypes.c_void_p, ctypes.POINTER(ctypes.c_int32)]
    query.restype = ctypes.c_int32
    member = ctypes.c_int32()
    if not query(current(), None, ctypes.byref(member)):
        raise ReceiverError(f"Receiver Job membership is unavailable: WinError {ctypes.get_last_error()}.")
    if not member.value:
        raise ReceiverError("Receiver requires its existing caller Job; it never creates or escapes one.")


def verify_artifacts(
    value: dict[str, Any], *, parent: Path, manifest_sha256: str,
    boundary: Boundary, clock: Callable[[], float],
) -> None:
    root = Path(value["root"])
    if root.parent != parent or not root.is_dir() or root.resolve() != root:
        raise ReceiverError("Native runtime root is outside its original ordinary parent.")
    if _digest(root / "summary.json", boundary, clock) != value["summary_sha256"]:
        raise ReceiverError("Final native artifact digest changed after capture.")
    index_path = root / "sha256.json"
    index_data = _read_bounded(index_path, boundary, clock)
    if hashlib.sha256(index_data).hexdigest() != value["index_sha256"]:
        raise ReceiverError("Final native artifact digest changed after capture.")
    index = _json(index_data)
    rows = index["artifacts"]
    if (
        index.get("preparation_manifest_sha256") != manifest_sha256
        or type(index.get("execution_scope")) is not int
        or index["execution_scope"] != value["execution_scope"]
        or not isinstance(rows, list) or type(index.get("artifact_count")) is not int
        or len(rows) != index["artifact_count"]
    ):
        raise ReceiverError("Native artifact index is not bound to the accepted preparation.")
    names = set()
    for row in rows:
        boundary.require(clock)
        if not isinstance(row, dict) or set(row) != {"path", "bytes", "sha256"}:
            raise ReceiverError("Native artifact index contains an invalid row.")
        relative = row["path"]
        if not isinstance(relative, str) or not relative or any(c in relative for c in (":", "\0")):
            raise ReceiverError("Native artifact index contains an unsafe path.")
        parts = relative.replace("\\", "/").split("/")
        if any(part in {"", ".", ".."} for part in parts) or PurePosixPath("/".join(parts)).is_absolute():
            raise ReceiverError("Native artifact index escaped its root.")
        name = "/".join(parts)
        if name.casefold() in names or name.casefold() == "sha256.json":
            raise ReceiverError("Native artifact index repeats or includes its own path.")
        names.add(name.casefold())
        path = root.joinpath(*parts)
        if (
            path.resolve() != path or path.is_symlink() or not path.is_file()
            or type(row["bytes"]) is not int or row["bytes"] < 0
            or path.stat().st_size != row["bytes"]
            or not isinstance(row["sha256"], str)
            or _digest(path, boundary, clock) != row["sha256"]
        ):
            raise ReceiverError("Native indexed bytes are changed or redirected.")
    actual = {p.relative_to(root).as_posix().casefold() for p in root.rglob("*") if p.is_file()}
    if actual != names | {"sha256.json"}:
        raise ReceiverError("Native final index does not cover the complete runtime file tree.")
    boundary.require(clock)


def receive(
    command: list[str], *, scope: int, cwd: Path, manifest_sha256: str,
    clock: Callable[[], float] = time.monotonic,
) -> dict[str, Any]:
    boundary = Boundary(clock(), datetime.now(timezone.utc).isoformat())
    report: dict[str, Any] = {
        "kind": "native-qualification-receiver", "passed": False,
        "execution_scope": scope, "monotonic_start": boundary.start,
        "started_utc": boundary.utc, "native_exit": None,
        "native_exited": False, "streams_closed": False, "readers_settled": False,
        "process_handle_closed": False, "errors": [],
        "desktop_qualified": False, "visible_controls_attempted": 0,
    }
    process = None
    readers: list[threading.Thread] = []
    streams = [Stream(), Stream()]
    try:
        if os.name != "nt":
            raise ReceiverError("Receiver execution requires native Windows and existing caller containment.")
        if (
            type(scope) is not int or scope <= 0
            or not cwd.is_absolute() or cwd.resolve() != cwd or not cwd.is_dir()
            or not isinstance(manifest_sha256, str) or re.fullmatch(r"[a-f0-9]{64}", manifest_sha256) is None
        ):
            raise ReceiverError("Receiver requires exact accepted native inputs.")
        argv = _command(command, scope, manifest_sha256, boundary, clock)
        _require_existing_job()
        boundary.require(clock, work=True)
        process = subprocess.Popen(
            argv, cwd=cwd, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
            stderr=subprocess.PIPE, bufsize=0, creationflags=subprocess.CREATE_NO_WINDOW,
        )
        assert process.stdout is not None and process.stderr is not None
        for source, stream in zip((process.stdout, process.stderr), streams):
            reader = threading.Thread(target=stream.read, args=(source,), daemon=True)
            readers.append(reader)
            reader.start()
        report["native_exit"] = process.wait(timeout=max(0, boundary.start + WHOLE_SECONDS - clock()))
        report["native_exited"] = True
        for reader in readers:
            reader.join(max(0, boundary.start + WHOLE_SECONDS - clock()))
        report["readers_settled"] = all(not reader.is_alive() for reader in readers)
        if not report["readers_settled"] or any(stream.errors for stream in streams):
            raise ReceiverError("Native captured streams did not settle without errors.")
    except (OSError, ValueError, KeyError, TypeError, RuntimeError, subprocess.SubprocessError) as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    finally:
        if process is not None:
            failures = []
            for source in (process.stdout, process.stderr):
                if source is not None:
                    try:
                        source.close()
                    except OSError as error:
                        failures.append(f"Stream close: {error}")
            report["streams_closed"] = not failures
            handle = getattr(process, "_handle", None)
            try:
                if handle is not None:
                    handle.Close()
                report["process_handle_closed"] = True
            except OSError as error:
                failures.append(f"Process handle close: {error}")
            report["errors"].extend(failures)
        report["reader_errors"] = [list(stream.errors) for stream in streams]
        report["captured_streams"] = {
            name: {
                "bytes": len(stream.data),
                "sha256": hashlib.sha256(stream.data).hexdigest(),
                "base64": base64.b64encode(stream.data).decode("ascii"),
                "complete": report["readers_settled"] and not stream.errors,
            }
            for name, stream in zip(("stdout", "stderr"), streams)
        }
    try:
        captured_at = boundary.require(clock)
        report["post_capture_monotonic"] = captured_at
        if (
            report["errors"] or not report["native_exited"] or not report["readers_settled"]
            or not report["streams_closed"] or not report["process_handle_closed"]
        ):
            raise ReceiverError("Native exit or stream/handle custody is incomplete; no replay.")
        value = terminal(
            bytes(streams[0].data), boundary=boundary, scope=scope,
            native_exit=report["native_exit"], captured_at=captured_at,
        )
        _command(command, scope, manifest_sha256, boundary, clock)
        report["preparation_sources_unchanged_after"] = True
        verify_artifacts(value, parent=cwd, manifest_sha256=manifest_sha256, boundary=boundary, clock=clock)
        report["terminal"] = value
        report["post_artifact_verification_monotonic"] = boundary.require(clock)
        report["passed"] = True
    except (OSError, ValueError, KeyError, TypeError, RuntimeError) as error:
        report["errors"].append(f"{type(error).__name__}: {error}")
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("request", type=Path)
    args = parser.parse_args()
    request = _json(args.request.read_bytes())
    if set(request) != {"command", "scope", "cwd", "manifest_sha256"}:
        raise ReceiverError("Receiver request has unexpected fields.")
    report = receive(
        request["command"], scope=request["scope"], cwd=Path(request["cwd"]),
        manifest_sha256=request["manifest_sha256"],
    )
    encoded = json.dumps(report, ensure_ascii=True) + "\n"
    if time.monotonic() - report["monotonic_start"] >= WHOLE_SECONDS:
        report["passed"] = False
        report["errors"].append("Original bound exhausted before receiver publication.")
        encoded = json.dumps(report, ensure_ascii=True) + "\n"
    sys.stdout.write(encoded)
    sys.stdout.flush()
    return 0 if report["passed"] and time.monotonic() - report["monotonic_start"] < WHOLE_SECONDS else 1


if __name__ == "__main__":
    raise SystemExit(main())
