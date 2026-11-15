"""Stdlib-only MCP-start snapshot, embedded in the anonymous client overlay."""

import json
import os
from pathlib import Path
import select
import shutil
import subprocess
import sys

WINDOWS_ORIGIN = globals().get("WindowsOrigin")
ENTRY_CAPABILITY = "optional-physical-owner-entry-v1"
ENVIRONMENT_KEYS = (
    "AGENT_MCP_HOME", "AGENT_MCP_MARKETPLACE_ROOT", "COPILOT_EXTENSIONS_CONTEXT",
    "AGENT_MCP_PAYLOAD_ROOT", "COPILOT_EXTENSIONS_BOOT_TRACE_PLUGIN",
    "COPILOT_EXTENSIONS_BOOT_TRACE_SHIM_START_MS",
    "AGENT_RT_ROOT", "PYTHONUTF8", "PYTHONPATH", "PYTHONHOME",
)


class Origin:
    """Keep the version-1 Linux native owner pinned without an installed runtime."""

    def __init__(self, pid, birth):
        if type(pid) is not int or not 0 < pid < 1 << 31 or not isinstance(birth, str):
            raise ValueError("Invalid originating native identity")
        self.pid, self.birth, self.descriptor = pid, birth, None

    def __enter__(self):
        native = getattr(os, "pidfd_open", None)
        if native is None:
            import ctypes

            libc = ctypes.CDLL(None, use_errno=True)
            native = libc.pidfd_open
            native.argtypes = [ctypes.c_int, ctypes.c_uint]
            native.restype = ctypes.c_int
            descriptor = native(self.pid, 0)
            if descriptor < 0:
                number = ctypes.get_errno()
                raise OSError(number, os.strerror(number))
        else:
            descriptor = native(self.pid)
        self.descriptor = descriptor
        try:
            data = Path(f"/proc/{self.pid}/stat").read_text(
                encoding="ascii", errors="surrogateescape",
            )
            fields = data[data.rindex(")") + 2:].split()
            boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
            namespace = os.stat(f"/proc/{self.pid}/ns/pid").st_ino
            actual = f"linux:{boot}:{namespace}:{int(fields[19])}"
            if fields[0] == "Z" or actual != self.birth:
                raise ProcessLookupError("Originating owner changed or exited")
            self.observer = select.poll()
            self.observer.register(descriptor, select.POLLIN)
            self.require_alive()
        except BaseException:
            os.close(descriptor)
            self.descriptor = None
            raise
        return self

    def require_alive(self):
        """Refuse a dead or unobservable retained owner before an exec."""
        events = self.observer.poll(0)
        if any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events):
            raise OSError("Originating owner observation unavailable")
        if any(flags & (select.POLLIN | select.POLLHUP) for _, flags in events):
            raise ProcessLookupError("Originating owner exited before MCP entry")

    def __exit__(self, *_args):
        os.close(self.descriptor)
        self.descriptor = None


def receiver(report):
    """Accept an optional entry declaration, never infer it from pair support."""
    contract = report.get("contract", {})
    entry = report.get("entry", {})
    if not (
        report.get("supported") is True and report.get("invocation_allowed") is True
        and contract.get("name") == "agent-mcp.session-owner-handoff"
        and type(contract.get("minimum")) is int and contract["minimum"] == 1
        and type(contract.get("maximum")) is int and contract["maximum"] >= 1
        and isinstance(contract.get("capabilities"), list)
        and ENTRY_CAPABILITY in contract["capabilities"]
        and isinstance(entry, dict)
        and isinstance(entry.get("environment"), dict)
        and set(entry.get("environment", {})) == set(ENVIRONMENT_KEYS)
    ):
        return None
    for key in ("python", "prefix", "module", "cwd"):
        value = entry.get(key)
        if not isinstance(value, str) or "\0" in value or not Path(value).is_absolute():
            return None
    if not Path(entry["module"]).is_relative_to(Path(entry["prefix"])):
        return None
    if any(
        value is not None and (not isinstance(value, str) or "\0" in value)
        for value in entry["environment"].values()
    ):
        return None
    return entry


def _windows_argv(command):
    return [shutil.which(command[0]) or command[0], *command[1:]]


def _windows_start(command, environment):
    return subprocess.Popen(
        _windows_argv(command), env=environment,
        stdin=sys.stdin, stdout=sys.stdout, stderr=sys.stderr,
        creationflags=subprocess.CREATE_NO_WINDOW,  # headless-guard: allow anonymous stdlib snapshot before provider imports
    )


def _ordinary(original, environment):
    if sys.platform == "win32":
        return _windows_start(original, environment).wait()
    os.execvpe(original[0], original, environment)
    raise RuntimeError("Ordinary MCP exec unexpectedly returned")


def _start_owned(request, owner):
    original = request["original"]
    environment = os.environ.copy()
    try:
        result = subprocess.run(
            _windows_argv(request["query"]) if sys.platform == "win32" else request["query"],
            env={**environment, "AGENT_MCP_NO_SELFPROVISION": "1"},
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
            **({"creationflags": subprocess.CREATE_NO_WINDOW} if sys.platform == "win32" else {}),  # headless-guard: allow anonymous stdlib snapshot before provider imports
        )
        report = json.loads(result.stdout)
        entry = receiver(report) if result.returncode == 0 else None
    except (OSError, ValueError, TypeError, AttributeError, subprocess.TimeoutExpired):
        entry = None
    owner.require_alive()
    if entry is None:
        return _ordinary(original, environment)
    else:
        request["receiver"] = entry
        request["selection"] = report.get("selection")
        command = [
            entry["python"], "-I", "-m", "agent_mcp", "session-owner-entry",
            json.dumps(request, ensure_ascii=True),
        ]
        if sys.platform == "win32":
            try:
                child = _windows_start(command, environment)
            except OSError:
                owner.require_alive()
                return _ordinary(original, environment)
            # Creation accepted the child. Exit/wait failure can never replay it.
            return child.wait()
        try:
            os.execve(command[0], command, environment)
        except OSError:
            # A failed exec accepted no application bytes.
            owner.require_alive()
            return _ordinary(original, environment)
    raise RuntimeError("MCP entry exec unexpectedly returned")


def start(request):
    """Pin before probing and refuse owner loss on every pre-effect entry path."""
    if sys.platform == "win32":
        if WINDOWS_ORIGIN is None:
            raise ValueError("Windows bootstrap requires an embedded native owner snapshot")
        with WINDOWS_ORIGIN(request["binding"]) as owner:
            request["pid"], request["birth"] = owner.pid, owner.birth
            return _start_owned(request, owner)
    if not sys.platform.startswith("linux"):
        raise ValueError("Anonymous owner entry bootstrap requires Linux or Windows")
    with Origin(request["pid"], request["birth"]) as owner:
        return _start_owned(request, owner)


if __name__ == "__main__":
    try:
        raise SystemExit(start(json.loads(sys.argv[1])))
    except (OSError, ValueError) as error:
        print(f"agent-mcp: owner bootstrap refused: {error}", file=sys.stderr)
        raise SystemExit(1)
