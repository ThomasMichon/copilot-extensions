"""A bounded spare UI process inside the existing Manager mux resident."""

from __future__ import annotations

import base64
import logging
import json
import os
import secrets
import shlex
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from agent_procutil import no_window_kwargs
from zdd.diagnostics import process_start_time

from . import engine_client
from .mux_daemon_process import scrub_session_credentials
from .warm_picker_worker import publish
from .warm_picker_contract import KIND as KIND, CONTEXT_KEYS, invocation_context, mux_arguments, runtime_command, runtime_identity, worker_environment

MAX_ACTIVE = 4
STARTUP_TIMEOUT = 30.0
CLAIM_TIMEOUT = 15.0
RESULT_TIMEOUT = 30.0
@dataclass
class Slot:
    directory: Path
    session: str
    created: float
    claimed: float | None = None
    completed: float | None = None
    next_probe: float = 0.0
    incarnation: str | None = None
    context: dict[str, str] | None = None


class WarmPickerPool:
    """Prepare spares after admitted Picker demand, never during daemon startup."""

    def __init__(self, root: Path) -> None:
        self.directory = root / "picker-sessions" / secrets.token_hex(12)
        self.owner_start = process_start_time(os.getpid())
        try:
            self.engine = engine_client.engine_base_command()
        except (engine_client.EngineError, OSError, ValueError) as error:
            logging.getLogger(__name__).warning("Warm Picker engine is unavailable: %s", type(error).__name__)
            self.engine = None
        self.environment = worker_environment(dict(os.environ))
        self.context = invocation_context(self.environment, manager_root=root)
        self.mux: str | None = None
        self._mux_resolved = False
        self.mux_args = mux_arguments(self.environment)
        self._lock = threading.RLock()
        self._slots: list[Slot] = []
        self._accepting = True
        self._last_spawn = 0.0

    def _mux(self, *args: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            [self.mux, *self.mux_args, *args], stdin=subprocess.DEVNULL, capture_output=True,
            text=True, timeout=3, env=scrub_session_credentials(dict(os.environ)),
            **no_window_kwargs(),
        )

    def _alive(self, slot: Slot) -> bool:
        return self._mux("has-session", "-t", slot.session).returncode == 0

    def _incarnation(self, slot: Slot) -> str | None:
        from .mux_discovery import _valid_native_incarnation

        result = self._mux("display-message", "-p", "-t", slot.session, "#{session_id}:#{session_created}")
        value = (result.stdout or "").strip()
        return value if result.returncode == 0 and _valid_native_incarnation(value) else None

    def _worker_ready(self, slot: Slot) -> bool:
        from zdd.diagnostics import _pid_alive

        try:
            ready = json.loads((slot.directory / "ready.json").read_text(encoding="utf-8"))
            pid = ready["pid"]
            context = ready.get("context")
            valid_context = (
                isinstance(context, dict)
                and set(context) == {*CONTEXT_KEYS, "environment_sha256"}
                and all(context.get(key) == self.context[key] for key in CONTEXT_KEYS)
                and isinstance(context.get("environment_sha256"), str)
                and len(context["environment_sha256"]) == 64
                and all(char in "0123456789abcdef" for char in context["environment_sha256"])
            )
            slot.context = context if valid_context else None
            return (
                slot.incarnation is not None and type(pid) is int and pid > 0 and _pid_alive(pid)
                and ready.get("start") is not None
                and process_start_time(pid) == ready["start"]
                and valid_context
            )
        except (OSError, TypeError, ValueError, KeyError):
            return False

    def _remove(self, slot: Slot) -> None:
        current = self._incarnation(slot)
        if slot.incarnation is None or current != slot.incarnation:
            if self._alive(slot):
                raise RuntimeError("warm UI session identity changed; refusing retirement")
            shutil.rmtree(slot.directory)
            self._slots.remove(slot)
            return
        result = self._mux("kill-session", "-t", slot.session)
        if result.returncode and self._alive(slot):
            raise RuntimeError("owned warm UI session could not be retired")
        shutil.rmtree(slot.directory)
        self._slots.remove(slot)

    def _spawn(self) -> None:
        from .manager_config import automatic_maintenance_enabled

        if not automatic_maintenance_enabled():
            return
        if self.owner_start is None or self.context is None:
            return
        identifier = secrets.token_hex(12)
        directory = self.directory / identifier
        argv = runtime_command(
            "worktree_manager.warm_picker_worker", str(directory),
            str(os.getpid()), str(self.owner_start),
        )
        directory.mkdir(parents=True, mode=0o700)
        slot = Slot(directory, "picker-" + identifier, time.monotonic())
        if os.name == "nt":
            powershell = shutil.which("pwsh")
            if not powershell:
                shutil.rmtree(directory)
                return
            script = "& " + " ".join("'" + part.replace("'", "''") + "'" for part in argv)
            encoded = base64.b64encode((script + "; exit $LASTEXITCODE").encode("utf-16-le")).decode("ascii")
            command = [powershell, "-NoLogo", "-NoProfile", "-EncodedCommand", encoded]
        else:
            command = [shlex.join(argv)]
        environment = dict(self.environment)
        for key in (engine_client.ENGINE_ARGV_ENV, engine_client.ENGINE_CMD_ENV,
                    "WORKTREE_MANAGER_RESIDENT_BOOTSTRAP"):
            environment.pop(key, None)
        self._slots.append(slot)
        result = subprocess.run(
            [self.mux, *self.mux_args, "new-session", "-d", "-s", slot.session, "--", *command],
            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=5,
            env=environment, **no_window_kwargs(),
        )
        slot.incarnation = self._incarnation(slot)
        if result.returncode:
            self._remove(slot)

    def pump(self) -> None:
        try:
            self._pump()
        except (OSError, RuntimeError, subprocess.SubprocessError):
            logging.getLogger(__name__).exception("Warm Picker maintenance failed")

    def _pump(self) -> None:
        if not self.mux or not self.engine or self.owner_start is None or self.context is None:
            return
        with self._lock:
            now = time.monotonic()
            for slot in list(self._slots):
                try:
                    result = slot.directory / "result.json"
                    if (slot.directory / "consumed").exists():
                        self._remove(slot)
                    elif slot.claimed is None:
                        if slot.incarnation is None:
                            slot.incarnation = self._incarnation(slot)
                        expired = now - slot.created > STARTUP_TIMEOUT and not (slot.directory / "ready.json").exists()
                        exited = (slot.directory / "ready.json").exists() and not self._worker_ready(slot)
                        if not self._accepting or expired or exited:
                            self._remove(slot)
                    elif result.is_file():
                        slot.completed = slot.completed or now
                        if now - slot.completed > RESULT_TIMEOUT:
                            self._remove(slot)
                    elif now - slot.claimed > CLAIM_TIMEOUT and now >= slot.next_probe:
                        from .mux_attached_clients import mux_attached_clients

                        slot.next_probe = now + 5.0
                        if (
                            not (slot.directory / "attached").exists()
                            or mux_attached_clients(
                                self.mux, slot.session, timeout_s=0.25, mux_args=self.mux_args,
                            ) == 0
                            or not self._alive(slot)
                        ):
                            self._remove(slot)
                        break
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    logging.getLogger(__name__).exception("Warm Picker session maintenance failed")
            if (
                self._accepting and len(self._slots) < MAX_ACTIVE + 1
                and not any(slot.claimed is None for slot in self._slots)
                and now - self._last_spawn >= 1.0
            ):
                self._last_spawn = now
                self._spawn()

    def acquire(self, payload: dict) -> dict:
        from .runtime_identity import route_version

        if self.owner_start is None:
            return {"available": False, "reason": "unsupported-process-identity"}
        if self.context is None:
            return {"available": False, "reason": "scope-mismatch"}
        handoff = payload.get("handoff")
        bootstrap = handoff.get("bootstrap") if isinstance(handoff, dict) else None
        if (
            not (
                payload.get("manager_runtime") == runtime_identity()
                or payload.get("manager_runtime") is None
                and payload.get("provider_generation") == route_version()
            )
            or payload.get("engine") != self.engine
            or not isinstance(payload.get("context"), dict)
            or any(payload["context"].get(key) != self.context[key] for key in CONTEXT_KEYS)
            or not isinstance(payload.get("cwd"), str)
            or not Path(payload["cwd"]).is_absolute()
            or not Path(payload["cwd"]).is_dir()
            or not isinstance(bootstrap, dict) or bootstrap.get("headless") is not False
            or not isinstance(bootstrap.get("project"), str) or not bootstrap["project"]
        ):
            return {"available": False, "reason": "scope-mismatch"}
        with self._lock:
            if not self._accepting:
                return {"available": False, "reason": "draining"}
            if sum(slot.claimed is not None for slot in self._slots) >= MAX_ACTIVE:
                return {"available": False, "reason": "capacity"}
            if not self._mux_resolved:
                self.mux = shutil.which("psmux" if os.name == "nt" else "tmux")
                self._mux_resolved = True
            mismatched = False
            for slot in self._slots:
                if slot.claimed is None and self._worker_ready(slot):
                    if payload["context"] != slot.context:
                        mismatched = True
                        continue
                    if self._incarnation(slot) != slot.incarnation:
                        continue
                    completion = runtime_command(
                        "worktree_manager", "--resident-result",
                        "--project", bootstrap["project"], "--directory", str(slot.directory),
                        "--manager-root", str(self.directory.parent.parent),
                    )
                    publish(slot.directory / "request.json", payload)
                    slot.claimed = time.monotonic()
                    return {
                        "available": True, "session": slot.session,
                        "directory": str(slot.directory), "mux": self.mux,
                        "attach_command": [self.mux, *self.mux_args, "attach-session", "-t", slot.session],
                        "complete_command": completion,
                    }
        return {"available": False, "reason": "scope-mismatch" if mismatched else "warming"}

    @property
    def busy(self) -> bool:
        with self._lock:
            return any(
                slot.claimed is not None and not (slot.directory / "consumed").exists()
                for slot in self._slots
            )

    def pause(self) -> None:
        with self._lock:
            self._accepting = False

    def resume(self) -> None:
        with self._lock:
            self._accepting = True

    def close(self) -> None:
        with self._lock:
            self._accepting = False
            for slot in list(self._slots):
                try:
                    self._remove(slot)
                except (OSError, RuntimeError, subprocess.SubprocessError):
                    logging.getLogger(__name__).exception("Warm Picker final retirement failed")
