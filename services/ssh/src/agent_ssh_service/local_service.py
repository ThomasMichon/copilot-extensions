"""Local lifecycle glue for this directory service, not a manager process."""
from __future__ import annotations

import os
from pathlib import Path
import time
from typing import Any

from agent_procutil import spawn_daemon
from agent_procutil.daemon_instances import DaemonInstanceSpec
from fleet_manager.private_state import private_directory

from .config import Document, ServiceConfig, check
from .local_control import LocalControl, config_identity, records, request


class LocalStartupUncertain(RuntimeError):
    """Keep the original native candidate attributable when readiness is unknown."""

    def __init__(self, process: Any) -> None:
        self.process = process
        super().__init__("local-startup-outcome-unknown")


def _matching(binding: Any, config: ServiceConfig) -> list[Document]:
    expected = config_identity(binding, config)
    matches = []
    for record in records(config):
        check(Path(record["root"]).samefile(binding.root), "foreign-local-owner-preserved")
        if (record["slot"] == binding.slot.name
                and record["source"] == binding.identity.document()
                and record["config_identity"] == expected):
            matches.append(record)
    return matches


def start(binding: Any, policy_path: Path, config: ServiceConfig) -> Document:
    """Reuse only an attested exact owner; do not stop a predecessor to install."""
    from .source_start import ensure_selected, load_provider

    ensure_selected(binding)
    deadline = time.monotonic() + 60

    def call(record: Document, operation: str) -> Document:
        return request(config, record, operation, timeout=deadline - time.monotonic())

    data = Path(config.to_dict()["data_dir"])
    private_directory(data)
    existing = _matching(binding, config)
    for record in existing:
        try:
            health = call(record, "health")
        except (OSError, ValueError, RuntimeError, TimeoutError):
            continue
        if health["ready"]:
            return {"state": "ready", "reused": True}
    check(time.monotonic() < deadline, "local-start-deadline")
    logs = binding.root / "logs"
    private_directory(logs)
    bootstrap = binding.slot / ".runtime-bootstrap" / "runtime_launcher.py"
    check(bootstrap.is_file(), "installed-retention-bootstrap-required")
    _, engine = load_provider(binding.slot)._resources(binding.slot)
    process = spawn_daemon(
        [engine._native_retained_path(binding.python), "-I", "-S", "-B", str(bootstrap),
         "-m", "agent_ssh_service",
         "__local-serve", "--config", str(policy_path)],
        cwd=data, log_path=logs / "ssh-service.log",
        instance=DaemonInstanceSpec(
            "agent-ssh-service", data, Path(__file__).with_name("__main__.py"),
        ),
    )
    try:
        while True:
            if process.poll() is not None:
                raise RuntimeError("local-service-start-failed")
            for record in _matching(binding, config):
                if record["peer"]["pid"] != process.pid:
                    continue
                preparation = call(record, "prepare")
                check(preparation["prepared"], "local-service-preparation-refused")
                health = call(record, "activate")
                if health["ready"]:
                    return {"state": "ready", "reused": False}
            if time.monotonic() >= deadline:
                raise TimeoutError("local-start-deadline")
            time.sleep(0.1)
    except Exception as error:
        raise LocalStartupUncertain(process) from error


def stop(binding: Any, config: ServiceConfig) -> None:
    """Explicit stop of the exact managed cohort; no PID/legacy fallback."""
    owners = _matching(binding, config)
    check(len(owners) == 1, "exact-local-owner-required")
    check(owners[0]["peer"]["pid"] != os.getpid(), "local-self-stop-refused")
    request(config, owners[0], "stop", wait_exit=True)


async def serve(binding: Any, config: ServiceConfig) -> int:
    """Keep local control inside the actual admitted foreground lifetime."""
    from .__main__ import _foreground

    control = LocalControl(binding, config)
    try:
        return await _foreground(config, lifecycle=control)
    finally:
        control.close()
