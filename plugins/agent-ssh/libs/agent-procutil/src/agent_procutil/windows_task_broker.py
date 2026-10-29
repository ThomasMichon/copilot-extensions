"""Explicit consumer-owned Scheduled Task adoption and finite launch activation.

Only the owning canonical installer calls reconcile/remove. The spawn path
reads an already adopted registration and never installs, repairs or updates it.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import os
import re
import secrets
import stat
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from zdd.private_protocol import Deadline, PeerIdentity
from zdd.private_windows import WindowsListener, connect
from zdd.private_windows_native import native

from . import DaemonSpawnError, _validated_daemon_cwd
from .owned_process import run_caller_owned
from .windows_broker_retention import replace as _retain
from .windows_broker_native import NativeBrokerPeer, NativeChild, stage
from .windows_broker_protocol import (
    LaunchRequest, _path, _refuse, close_resource, launch, retire_child, retry_cleanup, serve,
)

CONFIG_ENV = "AGENT_PROCUTIL_WINDOWS_BROKER_CONFIG"
OWNER_ENV = "AGENT_PROCUTIL_WINDOWS_BROKER_OWNER"
_SCHEMA = "agent-procutil.windows-launch-broker"
_NONCE = re.compile(r"[0-9a-f]{64}")
_NATIVE_HRESULT = re.compile(r"broker-native-hresult:([0-9A-F]{8})")
_PHASE_TIME = re.compile(r"broker-phase-time:([a-z-]+):([0-9]{1,16})")
_ACTIVATION_PHASES = frozenset({
    "input", "input-read", "json-resolve", "json-ready", "principal",
    "connect", "folder", "lookup", "validated",
    "run-requested", "run-returned", "engine-ready", "revalidated",
    "process", "birth", "response",
})


def _activation_phase(stderr: bytes | str | None) -> str:
    phase = "unobserved"
    for line in (stderr or b"").splitlines():
        for candidate in _ACTIVATION_PHASES:
            marker = "broker-phase:" + candidate
            if line == (marker.encode("ascii") if isinstance(line, bytes) else marker):
                phase = candidate
                break
    return phase


def _phase_time(line: bytes | str) -> tuple[str, int] | None:
    if isinstance(line, bytes):
        try:
            line = line.decode("ascii")
        except UnicodeDecodeError:
            return None
    match = _PHASE_TIME.fullmatch(line)
    if match is not None and match[1] in _ACTIVATION_PHASES:
        return match[1], int(match[2])
    return None


def _activation_elapsed(stderr: bytes | str | None, started: float) -> str:
    phase = _activation_phase(stderr)
    elapsed = None
    # Stopwatch and perf_counter use the same system-wide Windows QPC clock.
    for line in (stderr or b"").splitlines():
        stamp = _phase_time(line)
        if stamp is not None and stamp[0] == phase:
            elapsed = stamp[1] - round(started * 1000)
    return str(elapsed) if elapsed is not None and 0 <= elapsed <= 600000 else "unobserved"


@dataclass(frozen=True)
class Registration:
    """Non-secret installation metadata, never child argv or credentials."""

    installation: str
    root: str
    pythonw: str
    entrypoint: str
    principal: str

    def __post_init__(self) -> None:
        if (
            not isinstance(self.installation, str)
            or not re.fullmatch(r"[A-Za-z0-9_.:/@+-]{1,256}", self.installation)
            or not isinstance(self.principal, str)
            or not re.fullmatch(r"S-1-[0-9-]{1,180}", self.principal)
        ):
            raise _refuse("installation identity refused")
        for path in (self.root, self.pythonw, self.entrypoint):
            _path(path)
            if path.startswith("\\\\") or '"' in path:
                raise _refuse("installation path refused")
        if (
            ntpath.basename(self.pythonw).lower() != "pythonw.exe"
            or ntpath.basename(self.entrypoint) != "windows_broker_entry.py"
        ):
            raise _refuse("installation path refused")

    @property
    def key(self) -> str:
        return hashlib.sha256(
            (self.installation + "\0" + self.principal).encode("utf-8"),
        ).hexdigest()

    @property
    def config_path(self) -> Path:
        return Path(ntpath.join(self.root, "windows-launch-broker.json"))

    @property
    def owner(self) -> str:
        return _SCHEMA + ":" + self.key

    @property
    def pending_path(self) -> Path:
        return self.config_path.with_name("windows-launch-broker.pending.json")

    def task(self) -> dict[str, object]:
        # Scheduler substitutions contain only nonce/PID/birth public metadata.
        args = subprocess.list2cmdline([
            "-I", self.entrypoint, str(self.config_path), "$(Arg0)", "$(Arg1)", "$(Arg2)",
        ])
        return {
            "owner": self.owner, "task_name": "CE-LaunchBroker-" + self.key,
            "principal": self.principal, "pythonw": self.pythonw,
            "arguments": args, "cwd": self.root,
        }

    def frame(self) -> dict[str, object]:
        return {
            "schema": _SCHEMA, "version": 1, "installation": self.installation,
            "root": self.root, "pythonw": self.pythonw, "entrypoint": self.entrypoint,
            "principal": self.principal,
        }

    def validate_files(self) -> None:
        _validated_daemon_cwd(self.root)
        root = Path(self.root).resolve()
        for path in (Path(self.pythonw), Path(self.entrypoint), root):
            if path.resolve() != path.absolute() or any(
                parent.is_symlink() or (parent / ".git").exists()
                for parent in (path, *path.parents)
            ):
                raise _refuse("transient installation refused")
        if (
            not Path(self.pythonw).is_file() or not Path(self.entrypoint).is_file()
            or not Path(self.entrypoint).is_relative_to(root)
            or native().current_sid() != self.principal
        ):
            raise _refuse("installation prerequisite unavailable")
        from .windows_broker_cell import is_cell, validate as validate_cell
        from .windows_broker_installation import installation_identity

        if is_cell(root):
            relative = Path(self.entrypoint).relative_to(root)
            if len(relative.parts) != 6 or relative.parts[0] != "versions":
                raise _refuse("cell broker action is outside its immutable runtime")
            if self.installation != installation_identity(root):
                raise _refuse("cell broker installation identity refused")
            validate_cell(root, root / "versions" / relative.parts[1])


def _registration(value: object) -> Registration:
    if type(value) is not dict or set(value) != {
        "schema", "version", "installation", "root", "pythonw", "entrypoint", "principal",
    } or (
        value["schema"] != _SCHEMA
        or type(value["version"]) is not int or value["version"] != 1
    ):
        raise _refuse("adoption required")
    return Registration(
        value["installation"], value["root"], value["pythonw"],
        value["entrypoint"], value["principal"],
    )


def _metadata(path: Path, limit: int = 16384) -> object:
    info = path.lstat()
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or info.st_size > limit
        or getattr(info, "st_file_attributes", 0) & 0x400
    ):
        raise _refuse("installation metadata refused")
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        raw = stream.read(limit + 1)
        after = os.fstat(stream.fileno())
    fields = ("st_dev", "st_ino", "st_size", "st_mtime_ns")
    if len(raw) > limit or any(
        getattr(info, field) != getattr(opened, field)
        or getattr(opened, field) != getattr(after, field)
        or getattr(after, field) != getattr(path.lstat(), field)
        for field in fields
    ):
        raise _refuse("installation metadata changed")
    return json.loads(raw)


def load(path: Path, installation: str) -> Registration:
    """Read bounded owned metadata; an ambient pointer is not installation identity."""
    try:
        config = _registration(_metadata(path))
        if config.installation != installation or path.absolute() != config.config_path:
            raise _refuse("installation identity refused")
        config.validate_files()
    except (OSError, ValueError, TypeError, KeyError):
        raise _refuse("adoption required") from None
    if os.path.lexists(config.pending_path):
        raise _refuse("installer recovery required")
    return config


def task_operation(
    action: str, config: Registration, deadline: Deadline, **metadata: object,
) -> dict[str, object]:
    """Bound the native scheduler adapter; stdin contains no launch secrets."""
    if action not in {
        "install", "update", "activate", "status", "stop", "uninstall", "inspect", "recover",
    }:
        raise _refuse("task operation refused")
    if os.name != "nt":
        raise _refuse("Windows prerequisite unavailable")
    config.validate_files()
    powershell = Path(os.environ["SystemRoot"]) / (
        r"System32\WindowsPowerShell\v1.0\powershell.exe"
    )
    started = time.perf_counter()
    try:
        result = run_caller_owned(
            [
                str(powershell), "-NoProfile", "-NonInteractive", "-File",
                str(Path(__file__).with_name("windows_broker_task.ps1")), "-Action", action,
            ],
            timeout=deadline.remaining(), max_stdout_bytes=65536, max_stderr_bytes=65536,
            input_text=json.dumps({"config": config.task(), **metadata}, ensure_ascii=True),
        )
    except subprocess.TimeoutExpired as error:
        phase = _activation_phase(error.stderr)
        elapsed = _activation_elapsed(error.stderr, started)
        raise _refuse(
            "task " + action + " timed out (phase=" + phase + "; elapsed-ms=" + elapsed + ")",
        ) from None
    if result.returncode != 0:
        codes = [
            match.group(1) for line in result.stderr.splitlines()
            if (match := _NATIVE_HRESULT.fullmatch(line)) is not None
        ]
        reason = "\n".join(
            line for line in result.stderr.splitlines()
            if line not in {"broker-phase:" + phase for phase in _ACTIVATION_PHASES}
            and _NATIVE_HRESULT.fullmatch(line) is None
            and _phase_time(line) is None
        ).strip()
        allowed = {
            "broker-registration-owner-refused", "broker-registration-action-refused",
            "broker-registration-principal-refused", "broker-registration-policy-refused",
            "broker-active-transactions-remain", "broker-principal-refused",
            "broker-adoption-required", "broker-admission-disabled", "broker-activation-deadline",
            "broker-registration-rollback-unconfirmed",
        }
        reason = reason if reason in allowed else "scheduler-operation-failed"
        phase = _activation_phase(result.stderr)
        elapsed = _activation_elapsed(result.stderr, started)
        code = codes[-1] if codes else "unobserved"
        raise _refuse(
            "task " + action + " refused (" + reason + "; phase=" + phase
            + "; elapsed-ms=" + elapsed + "; code=" + code + ")",
        )
    try:
        value = json.loads(result.stdout)
    except (ValueError, TypeError):
        raise _refuse("task response refused") from None
    if type(value) is not dict:
        raise _refuse("task response refused")
    deadline.remaining()
    return value


def _write_metadata(path: Path, value: object) -> None:
    # The installer already owns the private root and its execution transaction.
    temporary = path.with_name(path.name + "." + secrets.token_hex(16) + ".tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=True)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _publish(config: Registration) -> None:
    _write_metadata(config.config_path, config.frame())


def _same_installation(first: Registration, second: Registration) -> bool:
    return (
        first.installation == second.installation and first.principal == second.principal
        and first.root == second.root
    )


def _published(config: Registration) -> Registration | None:
    try:
        value = _registration(_metadata(config.config_path))
    except FileNotFoundError:
        return None
    if not _same_installation(config, value):
        raise _refuse("installation transition refused")
    return value


def recover(config: Registration) -> bool:
    """Restore an interrupted publication only under the owning installer.

    The canonical execution driver must already own mutation and settle any
    interrupted execution fence. This is not a queue, lock or launch-time repair.
    Both generations must remain retained until this record is removed.
    """
    config.validate_files()
    try:
        record = _metadata(config.pending_path, 65536)
    except FileNotFoundError:
        return False
    if (
        type(record) is not dict or set(record) != {
            "schema", "version", "desired", "previous", "published", "accepting",
        }
        or record["schema"] != _SCHEMA + ".publication"
        or type(record["version"]) is not int or record["version"] != 1
        or type(record["accepting"]) is not bool
    ):
        raise _refuse("installer recovery record refused")
    desired = _registration(record["desired"])
    previous = None if record["previous"] is None else _registration(record["previous"])
    published = None if record["published"] is None else _registration(record["published"])
    for candidate in (desired, previous, published):
        if candidate is not None:
            if not _same_installation(config, candidate):
                raise _refuse("installation transition refused")
            candidate.validate_files()
    if (
        (previous is None and record["accepting"])
        or (published is not None and published != previous)
        or _published(config) not in (published, desired)
    ):
        raise _refuse("installer recovery state refused")
    _retain(config, [candidate for candidate in (desired, previous, published) if candidate is not None])
    if previous is not None:
        _qualify(previous)
    response = task_operation(
        "recover", desired, Deadline(30),
        previous=None if previous is None else previous.task(),
        accepting=record["accepting"],
    )
    if set(response) != {"recovered"} or response["recovered"] is not True:
        raise _refuse("installer recovery unconfirmed", cleanup_confirmed=False)
    if (
        _metadata(config.pending_path, 65536) != record
        or _published(config) not in (published, desired)
    ):
        raise _refuse("installer recovery state changed", cleanup_confirmed=False)
    # Recovery can be interrupted too. Keep the original record until both
    # external effects are restored, so the next explicit installer can repeat.
    if published is None:
        config.config_path.unlink(missing_ok=True)
    elif _published(config) != published:
        _publish(published)
    if _metadata(config.pending_path, 65536) != record or _published(config) != published:
        raise _refuse("installer recovery state changed", cleanup_confirmed=False)
    config.pending_path.unlink()
    _retain(config, [] if published is None else [published])
    return True


def _qualify(config: Registration) -> None:
    """Import the exact immutable action runtime before changing registration."""
    result = run_caller_owned(
        [config.pythonw, "-I", config.entrypoint, "--check-runtime"],
        timeout=15, max_stdout_bytes=4096, max_stderr_bytes=4096,
    )
    if (
        result.returncode != 0
        or result.stdout != "agent-procutil.windows-launch-broker.runtime-ready.v1\n"
        or result.stderr
    ):
        raise _refuse("runtime health qualification failed")


def reconcile(config: Registration, *, previous: Registration | None = None) -> None:
    """Installer integration after immutable-slot qualification, under its driver.

    No dependency installation or independent mutation lock is introduced here.
    The consumer supplies its validated installation identity and private root.
    A replacement is not activated while the old finite task accepts work.
    """
    config.validate_files()
    if previous is not None and not _same_installation(config, previous):
        raise _refuse("installation transition refused")
    recover(config)
    _qualify(config)
    published = _published(config)
    expected = previous if previous is not None else config
    if published is not None and published != expected:
        raise _refuse("installation transition refused")
    observed = task_operation("inspect", expected, Deadline(30))
    if (
        type(observed.get("installed")) is not bool
        or type(observed.get("accepting")) is not bool
        or (not observed["installed"] and observed["accepting"])
        or (published is not None and not observed["installed"])
    ):
        raise _refuse("installation inspection refused")
    if published == config and observed["installed"] and observed["accepting"]:
        _retain(config, [config])
        return
    original = expected if observed["installed"] else None
    _retain(config, [config, *([] if original is None else [original])])
    record = {
        "schema": _SCHEMA + ".publication", "version": 1,
        "desired": config.frame(),
        "previous": None if original is None else original.frame(),
        "published": None if published is None else published.frame(),
        "accepting": observed["accepting"],
    }
    _write_metadata(config.pending_path, record)
    action = "install" if previous is None else "update"
    try:
        task_operation(
            action, config, Deadline(30),
            **({} if previous is None else {"previous": previous.task()}),
        )
        _publish(config)
        if _metadata(config.pending_path, 65536) != record or _published(config) != config:
            raise _refuse("installation publication changed")
    except BaseException:
        recover(config)
        raise
    config.pending_path.unlink()
    _retain(config, [config])


def uninstall(config: Registration) -> None:
    """Remove only the owned finite registration, then release its runtimes."""
    config.validate_files()
    recover(config)
    published = _published(config)
    if published is not None and published != config:
        raise _refuse("installation transition refused")
    observed = task_operation("inspect", config, Deadline(30))
    if (
        type(observed.get("installed")) is not bool
        or type(observed.get("accepting")) is not bool
        or (not observed["installed"] and observed["accepting"])
    ):
        raise _refuse("installation inspection refused")
    if observed["installed"]:
        response = task_operation("uninstall", config, Deadline(30))
        if response.get("installed") is not False or response != {
            "phase": "drained", "installed": False, "child_control": "retained-by-consumer",
        }:
            raise _refuse("installation removal unconfirmed", cleanup_confirmed=False)
    if _published(config) != published:
        raise _refuse("installation publication changed", cleanup_confirmed=False)
    config.config_path.unlink(missing_ok=True)
    _retain(config, [])


def pipe_path(config: Registration, nonce: str) -> str:
    if not isinstance(nonce, str) or _NONCE.fullmatch(nonce) is None:
        raise _refuse("rendezvous refused")
    return "\\\\.\\pipe\\ce-launch-" + config.key + "-" + nonce


def spawn(
    command: list[str], *, cwd: str, env: dict[str, str], creationflags: int,
    log_path: str, stderr_log_path: str | None,
    instance_receipt=None,
) -> NativeChild:
    """Use an adopted broker; never provision or retry an ambiguous transaction."""
    peer, channel, child = None, None, None
    transaction_started = False
    try:
        if os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
            raise _refuse("contained activation refused")
        from .windows_broker_installation import load_current

        config = load_current()
        request = LaunchRequest.parse({
            "argv": command, "cwd": cwd, "env": env, "creationflags": creationflags,
            "log_path": log_path, "stderr_log_path": stderr_log_path,
        })
        deadline = Deadline(30)
        retry_cleanup(deadline)
        api = native()
        caller = api.identity(api.k.GetCurrentProcess(), os.getpid())
        nonce = secrets.token_hex(32)
        activated = task_operation(
            "activate", config, deadline, nonce=nonce,
            client_pid=caller.pid, client_birth=caller.start_time,
        )
        if (
            set(activated) != {"instance", "pid", "birth", "principal"}
            or not isinstance(activated["instance"], str)
            or re.fullmatch(
                r"\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}",
                activated["instance"],
            ) is None
        ):
            raise _refuse("activation identity refused")
        expected = PeerIdentity(activated["pid"], activated["birth"], activated["principal"])
        if expected.principal != caller.principal:
            raise _refuse("activation identity refused")
        peer = NativeBrokerPeer(expected)
        if os.path.normcase(api.process_image(peer.process._handle)) != os.path.normcase(
            config.pythonw,
        ):
            raise _refuse("activation image refused")
        channel = connect(pipe_path(config, nonce), expected, deadline)

        def bind_child(child: NativeChild) -> None:
            from ._daemon_instance_identity import created_process_identity
            from ._daemon_instance_store import bind_process

            identity = created_process_identity(child)
            if identity is None:
                raise _refuse("child birth identity unavailable", cleanup_confirmed=False)
            bind_process(instance_receipt, child.pid, observed_birth=identity[1])

        transaction_started = True
        owned_channel, channel = channel, None
        child = launch(
            owned_channel, peer, request, deadline,
            before_admit=bind_child if instance_receipt is not None else None,
        )
        closing, peer = peer, None
        close_resource(closing)
        return child
    except Exception as failure:
        error = failure if isinstance(failure, DaemonSpawnError) else _refuse(
            "launch failed", cleanup_confirmed=not transaction_started,
        )
        if not transaction_started:
            error.process_created = False
            error.admission_sent = False
        if child is not None:
            from ._daemon_instance_identity import created_process_identity

            error.process_created = True
            error.admission_sent = True
            try:
                error.process_identity = created_process_identity(child)
            except Exception:
                error.cleanup_confirmed = False
            try:
                retire_child(child, Deadline(5))
            except Exception:
                error.cleanup_confirmed = False
        for resource in (channel, peer):
            if resource is not None:
                try:
                    close_resource(resource)
                except Exception:
                    error.cleanup_confirmed = False
        raise error from None


def serve_main(args: list[str]) -> int:
    """One native-authenticated request, then exit after custody transfer."""
    if len(args) != 4 or os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1":
        return 2
    listener = None
    try:
        path, nonce, pid, birth = args
        # The installer-owned action is scoped to this exact config location.
        with Path(path).open("rb") as stream:
            metadata = json.loads(stream.read(16385))
        config = load(Path(path), metadata["installation"])
        actual = Path(__file__).with_name("windows_broker_entry.py").resolve()
        if actual != Path(config.entrypoint):
            raise _refuse("activation generation refused")
        from .windows_broker_cell import validate as validate_cell

        validate_cell(
            Path(config.root), Path(config.entrypoint).parents[3], require_active=True,
        )
        if not pid.isdecimal() or not birth.isdecimal():
            raise _refuse("client identity refused")
        expected = PeerIdentity(int(pid), birth, config.principal)
        deadline = Deadline(30)
        listener = WindowsListener(pipe_path(config, nonce))
        channel = listener.accept(deadline, expected)
        serve(channel, stage, deadline)
        return 0
    except Exception:
        # No exception value, request field or credential reaches a log/console.
        # Without successful finite completion, never claim broker-side cleanup.
        return 2
    finally:
        if listener is not None:
            listener.close()
