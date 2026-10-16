"""Installer process adaptation and watchdog deadlines for the updater."""

from __future__ import annotations

import json
import os
import re
import subprocess
from collections.abc import Mapping
from pathlib import Path

from agent_procutil import no_window_flags

from . import installer_run, output
from .installer_capabilities import (
    HUMAN_UTF8_ENV,
    powershell_file_target,
    powershell_human_utf8_supported,
)


def _installer_timeout(
    name: str, environment: Mapping[str, str], plugin_dir: Path | None = None,
) -> int:
    """Leave the installer watchdog time to terminate its tree and publish failure."""
    variable = re.sub(r"[^A-Za-z0-9]+", "_", name).upper() + "_INSTALL_DEADLINE_SEC"
    raw = environment.get(variable) or environment.get("COPILOT_PLUGIN_INSTALL_DEADLINE_SEC")
    data = {}
    if plugin_dir is not None:
        manifest = plugin_dir / "plugin.json"
        try:
            manifest_text = manifest.read_text(encoding="utf-8")
        except FileNotFoundError:
            pass
        else:
            data = json.loads(manifest_text)
            if not isinstance(data, dict):
                raise ValueError(f"{name}: installer manifest must be an object")
    if raw:
        try:
            deadline = int(raw)
        except ValueError as error:
            raise ValueError(f"{name}: invalid installer deadline {raw!r}; expected integer seconds") from error
        if not -(2**31) <= deadline < 2**31:
            raise ValueError(f"{name}: installer deadline must fit 32-bit integer seconds")
    else:
        deadline = data.get("installerDeadlineSeconds", 480)
        if type(deadline) is not int or not 0 < deadline < 2**31:
            raise ValueError(f"{name}: installerDeadlineSeconds must be a positive integer")
    cutover = data.get("cutoverDeadlineSeconds", 300)
    if type(cutover) is not int or not 0 < cutover < 2**31:
        raise ValueError(f"{name}: cutoverDeadlineSeconds must be a positive integer")
    # Disabling the inner watchdog must not disable the updater's safety bound.
    return (deadline if deadline > 0 else 480) + cutover + 30


def installer_deadline_status(error: Exception) -> str:
    """Status for a failed :func:`_installer_timeout`, keeping its cause.

    An unreadable manifest is an OS failure (its ``[Errno N]`` marker classifies
    it), never an invalid deadline value.
    """
    if isinstance(error, OSError):
        return f"installer manifest unreadable: {error}"
    return f"invalid installer deadline: {error}"


def _run_human_installer(argv, *, label: str, receipt_owned: bool = False, **kwargs):
    """Adapt only known-human installer streams, never a parsed JSON command."""
    run = installer_run.run_installer if receipt_owned else subprocess.run

    def complete(human, result):
        if human is not None:
            status = result.status if receipt_owned else f"exit {result.returncode}"
            succeeded = status == "OK" if receipt_owned else result.returncode == 0
            human.complete("done" if succeeded else "failed", status)

    with output.human_operation(
        label, enabled=output.current_operation() is not None,
    ) as human:
        target = powershell_file_target(argv)
        if human is not None:
            # Admission changes only this child's process environment, never
            # its host/options/target invocation or the parent environment.
            environment = dict(kwargs.get("env", os.environ))
            environment.pop(HUMAN_UTF8_ENV, None)
            kwargs["env"] = environment
            host = Path(str(argv[0])).name.lower() if argv else ""
            captured_powershell = (
                os.name == "nt" and target is not None
                and powershell_human_utf8_supported(target)
            )
            captured_posix = (
                os.name != "nt" and host in {"bash", "sh"} and len(argv) > 1
                and Path(str(argv[1])).suffix == ".sh"
            )
            if not (captured_powershell or captured_posix):
                human.progress(
                    "native UTF-8 output contract unavailable; retaining inherited installer streams",
                    state="warning",
                )
                result = run(argv, **kwargs)
                complete(human, result)
                return result
            if captured_powershell:
                environment[HUMAN_UTF8_ENV] = "1"
        with output.native_output() as fd:
            if fd is not None:
                if receipt_owned:
                    kwargs["output_fd"] = fd
                else:
                    kwargs.update(stdout=fd, stderr=fd)
                if os.name == "nt" and not receipt_owned:
                    kwargs.setdefault("creationflags", no_window_flags())
            result = run(argv, **kwargs)
        complete(human, result)
        return result
