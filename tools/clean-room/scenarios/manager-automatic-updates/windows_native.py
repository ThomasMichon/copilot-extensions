"""Actual Windows Autorun, Job-lifetime and desktop-observation fixture adapter."""

from __future__ import annotations

import hashlib
import ctypes
import importlib
import json
from pathlib import Path
import shutil
import subprocess
import sys
from typing import Any

from identity import ProcessPin, until
from windows_api import NativeBlocked, WinAPI, WindowObserver, require_desktop


def preflight(api: WinAPI | None = None) -> dict[str, bool]:
    owned = api is None
    api = api or WinAPI()
    try:
        try:
            return require_desktop(api.facts())
        except (OSError, ValueError, AttributeError, ctypes.ArgumentError) as exc:
            raise NativeBlocked(
                "desktop-inspection-failed", f"{type(exc).__name__}: {exc}",
            ) from exc
    finally:
        if owned:
            api.close()


class StartupSession:
    def __init__(
        self, root: Path, environment: dict[str, str], observer: WindowObserver,
        *, api: WinAPI | None = None, autostart: Any = None, procutil: Any = None,
    ) -> None:
        self.root, self.environment, self.observer = root, dict(environment), observer
        self.api = api or observer.api
        self.autostart = autostart
        self.procutil = procutil
        self.registered = False
        self.job = None
        self.caller = None
        self.control: ProcessPin | None = None
        self.output = None
        self.launch_attempted = False
        self.readiness_verified = False
        self.result: dict[str, Any] = {}

    def start(self) -> None:
        self.result["preflight"] = preflight(self.api)
        module = self.autostart or importlib.import_module("worktree_manager.automatic_updates_autostart")
        self.autostart = module
        previous = module.inspect_autostart(self.root)
        if previous.get("registered"):
            raise NativeBlocked("preexisting-startup", "fixture registration was not fresh")
        if previous.get("activation") == "blocked":
            raise NativeBlocked("startup-inspection-blocked", str(previous.get("blockers")))
        # Recheck after preparation, immediately before the only registry-writing call.
        preflight(self.api)
        registered = module.register_autostart(self.root)
        self.registered = registered.get("registered") is True
        if not self.registered or registered.get("activation") == "blocked":
            raise NativeBlocked("fixture-registration-blocked", str(registered.get("blockers")))
        owner = json.loads(
            (self.root / "automatic-updates/autostart.json").read_text(encoding="utf-8")
        )
        command = module._registry_read(registered["name"])
        if not isinstance(command, str) or command != owner["entry"]:
            raise NativeBlocked("autorun-owner-mismatch", "exact owned REG_SZ command cannot be verified")
        if len(command.encode("utf-16-le")) // 2 > 260:
            raise NativeBlocked(
                "autorun-command-too-long",
                "the actual Run command exceeds 260 UTF-16 units; choose a shorter private scratch path",
            )
        self.result["registered_command_sha256"] = hashlib.sha256(command.encode()).hexdigest()
        environment = dict(self.environment)
        # Only this explicit native fixture tests escape from its own inner Job.
        environment.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
        environment.pop("PYTEST_CURRENT_TEST", None)
        directory = self.root / "probe"
        helper = directory / "windows-job.py"
        shutil.copy2(Path(__file__).with_name("windows_job.py"), helper)
        spec = directory / "job-spec.json"
        spec.write_text(json.dumps({
            "root": str(self.root), "registered_command": command, "environment": environment,
        }), encoding="utf-8")
        pu = self.procutil or importlib.import_module("agent_procutil")
        python, extra = pu.direct_python(sys.executable)
        self.output = (directory / "job-caller.log").open("ab")
        self.caller, self.job = pu.spawn_sync_in_kill_on_close_job(
            [python, "-I", "-S", "-B", str(helper), str(spec)],
            require_job=True, allow_breakaway=True, cwd=self.root,
            env={**environment, **extra}, stdin=subprocess.DEVNULL,
            stdout=self.output, stderr=subprocess.STDOUT, **pu.no_window_kwargs(),
        )
        self.launch_attempted = True
        if self.job is None:
            raise NativeBlocked("strict-job-unavailable", "canonical primitive did not assign an owned Job")
        self.observer.add_pid(self.caller.pid)
        code = self.caller.wait(timeout=120)
        result = json.loads((directory / "job-launch.json").read_text(encoding="utf-8"))
        if type(result.get("control_pid")) is int and result["control_pid"] > 0:
            try:
                self.control = ProcessPin(
                    result["control_pid"], terminate_control=True,
                    expected_birth=result.get("control_birth"),
                )
            except (OSError, RuntimeError, ValueError) as error:
                raise NativeBlocked("control-identity-unavailable", str(error)) from error
            self.result["control_identity_verified"] = True
            self.observer.add_pid(result["control_pid"])
        if code or result.get("error") or result.get("gui_returncode") != 0:
            raise NativeBlocked("registered-command-failed", str(result))
        if result.get("executed_command_sha256") != self.result["registered_command_sha256"]:
            raise NativeBlocked("registered-command-changed", "GUI caller did not execute the exact Run value")
        self.result.update(
            registered_command_executed=result["registered_command_executed"],
            executed_command_sha256=result["executed_command_sha256"],
        )
        if self.control is None or self.control.exited():
            raise NativeBlocked("control-not-live", "contained control exited before Job closure")

    def verify_job_close(self, status: dict, process: Any) -> dict:
        self.observer.add_pid(status["pid"])
        self.readiness_verified = True
        if self.job is None or self.control is None:
            raise NativeBlocked("job-proof-missing", "owned inner Job/control are unavailable")
        birth = status.get("birth")
        if not isinstance(birth, str) or not birth:
            raise NativeBlocked("worker-identity-unavailable", "health omitted the kernel birth identity")
        worker = ProcessPin(status["pid"], expected_birth=birth)
        try:
            if worker.exited() or self.control.exited():
                raise NativeBlocked("job-precondition-failed", "worker and control must both be live")
            self.job.close()
            self.job = None
            until(self.control.exited, bool, timeout=8, label="contained control dies with inner Job")
            after = process.worker_status(self.root)
            if worker.exited() or not after.get("running") or after.get("worker_id") != status["worker_id"]:
                raise NativeBlocked("worker-died-with-job", "authenticated worker did not survive caller Job closure")
            self.result.update(
                inner_job_closed=True, contained_control_exit_verified=True,
                daemon_kernel_handle_live_after_job_close=True,
                authenticated_health_after_job_close=True,
            )
            return dict(self.result)
        finally:
            worker.close()

    def close_job(self) -> None:
        if self.job is not None:
            self.job.close()
            self.job = None
        if self.caller is not None:
            self.caller.wait(timeout=10)
        if self.control is not None:
            if not self.control.exited():
                self.control.stop_contained_control()
                self.result["control_required_forced_cleanup"] = True
            until(self.control.exited, bool, timeout=10, label="owned Job control cleanup")
            self.control.close()
            self.control = None
        if self.output is not None:
            self.output.close()
            self.output = None

    def retire(self) -> None:
        if self.registered:
            result = self.autostart.remove_autostart(self.root)
            if result.get("registered") or result.get("activation") == "blocked":
                raise NativeBlocked("fixture-autorun-retirement-failed", str(result.get("blockers")))
            self.registered = False
        if self.launch_attempted and not self.readiness_verified:
            raise NativeBlocked(
                "unattested-startup-cleanup",
                "startup never reached authenticated readiness; retain the fixture for owned-process inspection",
            )


def observation_passed(packet: dict, handover: dict) -> bool:
    lifetime = handover.get("windows_job_lifetime", {})
    return bool(
        packet.get("passed") is True and handover.get("observed_cycles", 0) >= 2
        and packet.get("observed_seconds", 0) >= 300
        and handover.get("observed_cycle_spacing_seconds", 0) >= 300
        and lifetime.get("inner_job_closed") is True
        and lifetime.get("contained_control_exit_verified") is True
        and lifetime.get("control_identity_verified") is True
        and lifetime.get("daemon_kernel_handle_live_after_job_close") is True
        and lifetime.get("authenticated_health_after_job_close") is True
        and lifetime.get("registered_command_executed") is True
        and isinstance(lifetime.get("registered_command_sha256"), str)
        and len(lifetime["registered_command_sha256"]) == 64
        and lifetime.get("executed_command_sha256") == lifetime["registered_command_sha256"]
    )
