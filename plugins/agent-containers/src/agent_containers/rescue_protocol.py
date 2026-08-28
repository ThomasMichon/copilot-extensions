"""Descriptor-bound in-container protocol for restricted evidence rescue."""

from __future__ import annotations

import hashlib
import os
import queue
import re
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from agent_procutil import no_window_flags

from .restricted_exec import sanitized_exec_prefix

_INVENTORY_LIMIT = 4 * 1024**2
_DIAGNOSTIC_LIMIT = 64 * 1024
_NODE_COMMON = r"""
const fs = require("fs");
const C = fs.constants;
const flags = C.O_RDONLY | C.O_DIRECTORY | C.O_NOFOLLOW | C.O_NONBLOCK;
function component(value) {
  if (!value || value === "." || value === ".." || value.includes("/") || value.includes("\0")) {
    throw new Error("invalid path component");
  }
  return value;
}
function openDir(parent, name) {
  const path = parent === null ? name : `/proc/self/fd/${parent}/${component(name)}`;
  return fs.openSync(path, flags);
}
function openAbsolute(path) {
  if (!path || !path.startsWith("/")) throw new Error("home is not absolute");
  let fd = fs.openSync("/", flags);
  for (const part of path.split("/").filter(Boolean)) {
    const child = openDir(fd, part);
    fs.closeSync(fd);
    fd = child;
  }
  return fd;
}
function openSession(session) {
  const home = openAbsolute(process.env.HOME);
  const copilot = openDir(home, ".copilot");
  const state = openDir(copilot, "session-state");
  return openDir(state, session);
}
function kind(entry) {
  if (entry.isDirectory()) return "d";
  if (entry.isFile()) return "f";
  if (entry.isSymbolicLink()) return "l";
  if (entry.isFIFO()) return "p";
  if (entry.isSocket()) return "s";
  if (entry.isCharacterDevice()) return "c";
  if (entry.isBlockDevice()) return "b";
  return "?";
}
function emit(...values) {
  for (const value of values) {
    fs.writeSync(1, Buffer.from(String(value)));
    fs.writeSync(1, Buffer.from([0]));
  }
}
"""
_ROOT_INVENTORY_SCRIPT = _NODE_COMMON + r"""
try {
  const home = openAbsolute(process.env.HOME);
  const copilot = openDir(home, ".copilot");
  const state = openDir(copilot, "session-state");
  emit("@root", "present");
  for (const entry of fs.readdirSync(`/proc/self/fd/${state}`, {withFileTypes: true})) {
    emit(entry.name, kind(entry));
  }
} catch (error) {
  if (error && error.code === "ENOENT") {
    emit("@root", "missing");
  } else {
    throw error;
  }
}
"""
_SESSION_INVENTORY_SCRIPT = _NODE_COMMON + r"""
const session = component(process.argv[1]);
const sessionFd = openSession(session);
const highGrowth = new Set(["files", "rewind-file-snapshots", "research"]);
function scan(fd, prefix, depth) {
  for (const entry of fs.readdirSync(`/proc/self/fd/${fd}`, {withFileTypes: true})) {
    const relative = prefix ? `${prefix}/${entry.name}` : entry.name;
    emit(relative, kind(entry));
    if (depth > 0 && entry.isDirectory() && !highGrowth.has(relative)) {
      try {
        const child = openDir(fd, entry.name);
        scan(child, relative, depth - 1);
        fs.closeSync(child);
      } catch (_error) {
        emit(`${relative}/<unreadable>`, "?");
      }
    }
  }
}
scan(sessionFd, "", 1);
"""
_MEMBER_STREAM_SCRIPT = _NODE_COMMON + r"""
function control(value) {
  fs.writeSync(2, value);
}
function main() {
  const session = component(process.argv[1]);
  const relative = process.argv[2];
  const maxBytes = Number(process.argv[3]);
  const parts = relative.split("/").map(component);
  let parent = openSession(session);
  let fd;
  try {
    for (const part of parts.slice(0, -1)) {
      const child = openDir(parent, part);
      fs.closeSync(parent);
      parent = child;
    }
    fd = fs.openSync(
      `/proc/self/fd/${parent}/${parts[parts.length - 1]}`,
      C.O_RDONLY | C.O_NOFOLLOW | C.O_NONBLOCK
    );
  } catch (error) {
    if (error && error.code === "ENOENT") {
      control("MISSING\n");
      return;
    }
    if (error && error.code === "ELOOP") {
      control("EXCLUDED\tsymlink\n");
      return;
    }
    if (error && ["ENOTDIR", "ENXIO", "ENODEV", "EOPNOTSUPP"].includes(error.code)) {
      control("EXCLUDED\tirregular\n");
      return;
    }
    throw error;
  }
  const stat = fs.fstatSync(fd);
  if (!stat.isFile()) {
    control("EXCLUDED\tirregular\n");
    return;
  }
  if (stat.size > maxBytes) {
    control(`EXCLUDED\toversize\t${stat.size}\n`);
    return;
  }
  control(`OK\t${stat.size}\n`);
  if (stat.size === 0) {
    fs.closeSync(fd);
    return;
  }
  const stream = fs.createReadStream(null, {
    fd,
    autoClose: true,
    start: 0,
    end: stat.size - 1,
  });
  stream.pipe(process.stdout);
}
main();
"""


class RescueError(RuntimeError):
    """A restricted evidence capture could not be verified and published."""


@dataclass
class StreamResult:
    """Host-observed result for one descriptor-bound member operation."""

    status: str
    size: int = 0
    sha256: str | None = None
    reason: str | None = None


@dataclass
class RootInventory:
    """NUL-framed session-state root observation."""

    state: str
    entries: list[tuple[str, str]]


def _creation_flags() -> int:
    return no_window_flags()


def _remaining(deadline: float | None, default: float) -> float:
    if deadline is None:
        return default
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise RescueError("session evidence rescue exceeded its operation deadline")
    return min(default, remaining)


def _docker_bytes(
    container: str,
    user: str,
    node_path: str,
    home: str,
    script: str,
    *args: str,
    deadline: float | None,
) -> bytes:
    try:
        proc = subprocess.Popen(
            [
                *sanitized_exec_prefix(container, user, home),
                node_path,
                "-e",
                script,
                *args,
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
    except FileNotFoundError:
        raise RescueError("docker CLI not found on PATH") from None
    if proc.stdout is None or proc.stderr is None:
        proc.terminate()
        raise RescueError("session evidence inventory did not expose pipes")

    chunks: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=4)
    cancel_readers = threading.Event()
    diagnostics = bytearray()
    diagnostic_overflow = threading.Event()

    def enqueue(item: bytes | BaseException | None) -> None:
        while not cancel_readers.is_set():
            try:
                chunks.put(item, timeout=0.05)
                return
            except queue.Full:
                continue

    def read_stdout() -> None:
        try:
            while True:
                chunk = proc.stdout.read(65536)
                if not chunk:
                    enqueue(None)
                    return
                enqueue(chunk)
        except BaseException as exc:
            enqueue(exc)

    def read_stderr() -> None:
        try:
            while not cancel_readers.is_set():
                chunk = proc.stderr.read(65536)
                if not chunk:
                    return
                available = _DIAGNOSTIC_LIMIT - len(diagnostics)
                if available > 0:
                    diagnostics.extend(chunk[:available])
                if len(chunk) > available:
                    diagnostic_overflow.set()
                    proc.terminate()
                    enqueue(
                        RescueError(
                            "session evidence helper diagnostics exceeded "
                            "their byte limit"
                        )
                    )
                    return
        except BaseException as exc:
            enqueue(exc)

    stdout_reader = threading.Thread(target=read_stdout, daemon=True)
    stderr_reader = threading.Thread(target=read_stderr, daemon=True)
    stdout_reader.start()
    stderr_reader.start()
    payload = bytearray()
    try:
        while True:
            try:
                item = chunks.get(timeout=_remaining(deadline, 30.0))
            except queue.Empty as exc:
                raise RescueError(
                    "session evidence inventory exceeded its deadline"
                ) from exc
            if item is None:
                break
            if isinstance(item, RescueError):
                raise item
            if isinstance(item, BaseException):
                raise RescueError("session evidence inventory stream failed") from item
            if len(payload) + len(item) > _INVENTORY_LIMIT:
                proc.terminate()
                raise RescueError(
                    "session evidence inventory exceeded its byte limit"
                )
            payload.extend(item)
        return_code = proc.wait(timeout=_remaining(deadline, 30.0))
        stdout_reader.join(timeout=0.1)
        stderr_reader.join(timeout=0.1)
        cancel_readers.set()
    except Exception:
        cancel_readers.set()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        stdout_reader.join(timeout=0.1)
        stderr_reader.join(timeout=0.1)
        raise
    if diagnostic_overflow.is_set():
        raise RescueError(
            "session evidence helper diagnostics exceeded their byte limit"
        )
    if return_code != 0:
        detail = bytes(diagnostics).decode("utf-8", errors="replace").strip()
        raise RescueError(f"session evidence inventory failed: {detail or 'unknown error'}")
    return bytes(payload)


def _decode_nul_records(payload: bytes, width: int) -> list[tuple[str, ...]]:
    if payload and not payload.endswith(b"\0"):
        raise RescueError("session evidence inventory returned invalid framing")
    fields = payload.split(b"\0")
    if fields and fields[-1] == b"":
        fields.pop()
    if len(fields) % width:
        raise RescueError("session evidence inventory returned invalid framing")
    records = []
    for index in range(0, len(fields), width):
        try:
            records.append(
                tuple(
                    field.decode("utf-8", errors="strict")
                    for field in fields[index : index + width]
                )
            )
        except UnicodeDecodeError as exc:
            raise RescueError("session evidence inventory returned invalid UTF-8") from exc
    return records


def _inventory_root(
    container: str,
    user: str,
    node_path: str,
    home: str,
    *,
    deadline: float | None,
) -> RootInventory:
    records = _decode_nul_records(
        _docker_bytes(
            container,
            user,
            node_path,
            home,
            _ROOT_INVENTORY_SCRIPT,
            deadline=deadline,
        ),
        2,
    )
    if not records or records[0][0] != "@root":
        raise RescueError("session evidence inventory omitted root state")
    state = records[0][1]
    if state not in {"present", "missing"}:
        raise RescueError("session evidence inventory returned invalid root state")
    return RootInventory(state, [(name, kind) for name, kind in records[1:]])


def _inventory_session(
    container: str,
    user: str,
    node_path: str,
    home: str,
    session_id: str,
    *,
    deadline: float | None,
) -> list[tuple[str, str]]:
    return [
        (path, kind)
        for path, kind in _decode_nul_records(
            _docker_bytes(
                container,
                user,
                node_path,
                home,
                _SESSION_INVENTORY_SCRIPT,
                session_id,
                deadline=deadline,
            ),
            2,
        )
    ]


def _stream_member(
    container: str,
    user: str,
    node_path: str,
    home: str,
    session_id: str,
    relative: str,
    destination: Path,
    *,
    max_bytes: int,
    deadline: float | None,
) -> StreamResult:
    """Open no-follow beneath anchored directory fds and stream that same fd."""
    try:
        proc = subprocess.Popen(
            [
                *sanitized_exec_prefix(container, user, home),
                node_path,
                "-e",
                _MEMBER_STREAM_SCRIPT,
                session_id,
                relative,
                str(max_bytes),
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_creation_flags(),
        )
    except FileNotFoundError:
        raise RescueError("docker CLI not found on PATH") from None
    if proc.stdout is None or proc.stderr is None:
        proc.terminate()
        raise RescueError("docker session evidence stream did not expose pipes")

    digest = hashlib.sha256()
    received = 0
    chunks: queue.Queue[bytes | BaseException | None] = queue.Queue(maxsize=4)
    cancel_reader = threading.Event()

    def enqueue(item: bytes | BaseException | None) -> None:
        while not cancel_reader.is_set():
            try:
                chunks.put(item, timeout=0.05)
                return
            except queue.Full:
                continue

    def read_stdout() -> None:
        try:
            while True:
                chunk = proc.stdout.read(1024 * 1024)
                if not chunk:
                    enqueue(None)
                    return
                enqueue(chunk)
        except BaseException as exc:
            enqueue(exc)

    reader = threading.Thread(target=read_stdout, daemon=True)
    reader.start()
    try:
        with destination.open("xb") as target:
            os.chmod(destination, 0o600)
            while True:
                try:
                    item = chunks.get(timeout=_remaining(deadline, 30.0))
                except queue.Empty as exc:
                    raise RescueError(
                        "session evidence member stream exceeded its deadline"
                    ) from exc
                if item is None:
                    break
                if isinstance(item, RescueError):
                    raise item
                if isinstance(item, BaseException):
                    raise RescueError("session evidence member stream failed") from item
                chunk = item
                received += len(chunk)
                if received > max_bytes:
                    proc.terminate()
                    raise RescueError(
                        f"session evidence member exceeded {max_bytes} bytes"
                    )
                target.write(chunk)
                digest.update(chunk)
            target.flush()
            os.fsync(target.fileno())
        return_code = proc.wait(timeout=_remaining(deadline, 30.0))
        cancel_reader.set()
        reader.join(timeout=0.1)
        control = proc.stderr.read().decode("utf-8", errors="replace").strip()
    except Exception:
        cancel_reader.set()
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=0.5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        reader.join(timeout=0.1)
        destination.unlink(missing_ok=True)
        raise
    if return_code != 0:
        destination.unlink(missing_ok=True)
        raise RescueError(f"session evidence stream failed: {control or 'unknown error'}")
    if control == "MISSING":
        destination.unlink(missing_ok=True)
        return StreamResult("missing", reason="missing")
    if control.startswith("EXCLUDED\t"):
        destination.unlink(missing_ok=True)
        parts = control.split("\t")
        size = int(parts[2]) if len(parts) > 2 and parts[2].isdigit() else 0
        return StreamResult("excluded", size=size, reason=parts[1])
    match = re.fullmatch(r"OK\t(\d+)", control)
    if not match:
        destination.unlink(missing_ok=True)
        raise RescueError("session evidence stream returned invalid control data")
    expected_size = int(match.group(1))
    if received != expected_size:
        destination.unlink(missing_ok=True)
        raise RescueError("session evidence member changed during descriptor read")
    result = StreamResult("captured", received, digest.hexdigest())
    _verify_host_file(destination, result)
    return result


def _verify_host_file(path: Path, expected: StreamResult) -> None:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            size += len(chunk)
            digest.update(chunk)
    if size != expected.size or digest.hexdigest() != expected.sha256:
        raise RescueError("host staging verification failed")
