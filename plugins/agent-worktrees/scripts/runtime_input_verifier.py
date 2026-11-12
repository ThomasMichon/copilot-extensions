"""Invocation-local live input checks across candidate-owned publication."""
from __future__ import annotations

import ast
from contextlib import contextmanager, nullcontext
import json
import math
import os
from pathlib import Path
import re
import select
import subprocess
import threading
import time
import uuid

CONTRACT = "agent-worktrees.runtime-input-verifier"
ABI = 1
ENVIRONMENT = "AGENT_WORKTREES_RUNTIME_INPUT_VERIFIER"
_clients = {}
_launch_lock = threading.Lock()


class VerificationRefused(RuntimeError):
    pass


def _record(record):
    if (
        not isinstance(record, dict) or set(record) != {"contract", "version", "id", "deadline"}
        or record["contract"] != CONTRACT or type(record["version"]) is not int or record["version"] != ABI
        or not isinstance(record["id"], str) or not re.fullmatch(r"[0-9a-f]{32}", record["id"])
        or isinstance(record["deadline"], bool) or not isinstance(record["deadline"], (int, float))
        or not math.isfinite(record["deadline"])
    ):
        raise VerificationRefused("Unsupported live input-verifier contract")
    return dict(record)


def require_source_contract(plugin: Path) -> None:
    path = plugin / "src" / "agent_worktrees" / "runtime_candidate.py"
    try:
        if path.is_symlink() or path.stat().st_size > 1 << 20:
            raise ValueError
        module = ast.parse(path.read_text(encoding="utf-8"))
        declarations = [
            ast.literal_eval(node.value) for node in module.body
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "INPUT_VERIFIER_ABI" for target in node.targets)
        ]
    except (OSError, ValueError, SyntaxError):
        declarations = []
    if len(declarations) != 1 or type(declarations[0]) is not int or declarations[0] != ABI:
        raise VerificationRefused("Selected Worktrees source does not support live input verification")


def _line(descriptor: int, deadline: float, *, idle: bool = False) -> bytes | None:
    result = bytearray()
    if os.name == "nt":
        import ctypes
        import msvcrt
        from ctypes import wintypes

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.PeekNamedPipe.argtypes = [
            wintypes.HANDLE, ctypes.c_void_p, wintypes.DWORD, ctypes.c_void_p,
            ctypes.POINTER(wintypes.DWORD), ctypes.c_void_p,
        ]
        kernel.PeekNamedPipe.restype = wintypes.BOOL
        handle = msvcrt.get_osfhandle(descriptor)
        available = wintypes.DWORD()
    idle_deadline = min(deadline, time.monotonic() + 0.1) if idle else deadline
    while len(result) <= 128:
        remaining = (idle_deadline if idle and not result else deadline) - time.monotonic()
        if remaining <= 0:
            if idle and not result:
                return None
            raise VerificationRefused("Live input verification deadline expired")
        if os.name == "nt":
            if not kernel.PeekNamedPipe(
                handle, None, 0, None, ctypes.byref(available), None,
            ):
                raise VerificationRefused("Live input-verifier owner disconnected")
            if not available.value:
                time.sleep(min(0.02, remaining))
                continue
        elif not select.select([descriptor], [], [], remaining)[0]:
            continue
        content = os.read(descriptor, 1)
        if not content:
            raise VerificationRefused("Live input-verifier owner disconnected")
        result.extend(content)
        if content == b"\n":
            return bytes(result)
    raise VerificationRefused("Live input-verifier message exceeds its bound")


def _write(descriptor: int, content: bytes) -> None:
    try:
        if len(content) > 128 or os.write(descriptor, content) != len(content):
            raise OSError
    except OSError:
        raise VerificationRefused("Live input-verifier channel closed") from None


class LiveInputVerifier:
    """Keep the caller's actual verification callback in its owning process."""

    def __init__(self, verify, *, deadline: float):
        if not callable(verify):
            raise VerificationRefused("Live input verification requires its actual owner")
        self.identity = _record({
            "contract": CONTRACT, "version": ABI, "id": uuid.uuid4().hex, "deadline": deadline,
        })
        self.callback = verify
        self.failure = None
        self.descriptors = []
        self.requests = self.replies = None
        self.active = False

    def __enter__(self):
        if self.active:
            raise VerificationRefused("Live input-verifier scope is already active")
        self.active = True
        try:
            self.check()
        except BaseException:
            self.active = False
            raise
        return self

    def __exit__(self, *exc):
        self.active = False
        for descriptor in self.descriptors:
            os.close(descriptor)
        self.descriptors.clear()

    def check(self):
        if not self.active:
            raise VerificationRefused("Live input-verifier scope is closed")
        if self.failure is not None:
            raise VerificationRefused("Current runtime inputs were refused") from self.failure
        if time.monotonic() >= self.identity["deadline"]:
            raise VerificationRefused("Live input verification deadline expired")
        self.callback()

    @contextmanager
    def child_launch(self, environment: dict):
        self.check()
        requests, child_write = os.pipe()
        child_read, replies = os.pipe()
        self.requests, self.replies = requests, replies
        self.descriptors.extend((requests, replies))
        endpoints = (child_read, child_write)
        with _launch_lock:
            try:
                if os.name == "nt":
                    import msvcrt

                    handles = [msvcrt.get_osfhandle(descriptor) for descriptor in endpoints]
                    for handle in handles:
                        os.set_handle_inheritable(handle, True)
                    startup = subprocess.STARTUPINFO()
                    startup.lpAttributeList = {"handle_list": handles}
                    options = {"startupinfo": startup, "close_fds": True}
                else:
                    handles = list(endpoints)
                    options = {"pass_fds": endpoints}
                options["env"] = {
                    **environment,
                    ENVIRONMENT: json.dumps({"identity": self.identity, "endpoints": handles}),
                }
                yield options
            finally:
                if os.name == "nt":
                    for descriptor in endpoints:
                        os.set_handle_inheritable(msvcrt.get_osfhandle(descriptor), False)
                for descriptor in endpoints:
                    os.close(descriptor)

    def wait(self, process):
        interruption = None
        while process.poll() is None:
            try:
                request = _line(self.requests, self.identity["deadline"], idle=True)
                if request is None:
                    continue
                match = re.fullmatch(
                    self.identity["id"].encode() + rb":([1-9][0-9]{0,9})\n", request,
                )
                if match is None:
                    raise VerificationRefused("Invalid live input-verifier request")
                try:
                    self.check()
                    outcome = b":ok\n"
                except BaseException as error:
                    if self.failure is None:
                        self.failure = error
                    if isinstance(error, (KeyboardInterrupt, SystemExit)):
                        interruption = error
                    outcome = b":refused\n"
                _write(self.replies, request[:-1] + outcome)
            except (KeyboardInterrupt, SystemExit) as error:
                interruption = error
                self.failure = error
            except (OSError, VerificationRefused):
                time.sleep(0.02)
        result = process.communicate()
        if interruption is not None:
            raise interruption
        return result


class _Client:
    def __init__(self, identity, endpoints):
        self.identity = identity
        self.lock = threading.Lock()
        self.sequence = 0
        self.closed = False
        if os.name == "nt":
            import msvcrt

            endpoints = [
                msvcrt.open_osfhandle(endpoints[0], os.O_RDONLY | os.O_BINARY),
                msvcrt.open_osfhandle(endpoints[1], os.O_WRONLY | os.O_BINARY),
            ]
        self.read, self.write = endpoints
        for descriptor in endpoints:
            os.set_inheritable(descriptor, False)

    def check(self):
        with self.lock:
            if self.closed or time.monotonic() >= self.identity["deadline"]:
                raise VerificationRefused("Live input-verifier scope is closed or expired")
            self.sequence += 1
            request = f"{self.identity['id']}:{self.sequence}".encode()
            _write(self.write, request + b"\n")
            response = _line(self.read, self.identity["deadline"])
            if response != request + b":ok\n":
                raise VerificationRefused("Current runtime inputs were refused")

    def close(self):
        if not self.closed:
            self.closed = True
            os.close(self.read)
            os.close(self.write)


def current_client(identity):
    identity = _record(identity)
    key = identity["id"]
    if key in _clients:
        client = _clients[key]
        if client.identity != identity:
            raise VerificationRefused("Live input-verifier identity changed")
        return client
    raw = os.environ.pop(ENVIRONMENT, "")
    try:
        packet = json.loads(raw)
        endpoints = packet["endpoints"]
        if (
            set(packet) != {"identity", "endpoints"} or _record(packet["identity"]) != identity
            or not isinstance(endpoints, list) or len(endpoints) != 2 or endpoints[0] == endpoints[1]
            or any(type(value) is not int or value <= 2 for value in endpoints)
        ):
            raise ValueError
    except (KeyError, TypeError, ValueError):
        raise VerificationRefused("Live input-verifier custody is missing or mismatched") from None
    client = _Client(identity, endpoints)
    _clients[key] = client
    return client


def close_clients():
    for client in _clients.values():
        client.close()
    _clients.clear()


def wait_for_settlement(argv, environment, *, verifier=None):
    with (verifier.child_launch(environment) if verifier is not None
          else nullcontext({"env": environment})) as launch:
        process = subprocess.Popen(
            argv, **launch, stdin=subprocess.DEVNULL,
            executable=argv[0] if os.name == "nt" else None,
            text=True, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
    try:
        stdout, stderr = verifier.wait(process) if verifier is not None else process.communicate()
    except BaseException:
        # An interrupted caller still owns the child's actual publication outcome.
        if verifier is not None:
            verifier.wait(process)
        else:
            process.communicate()
        raise
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
