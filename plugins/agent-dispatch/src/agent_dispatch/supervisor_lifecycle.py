"""Source-owned supervisor lifecycle evidence, never inferred human intent."""

from __future__ import annotations

import argparse
import logging
import os
import re
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from zdd import lifecycle
if TYPE_CHECKING:
    from .supervisor_daemon import SupervisorDaemon

log = logging.getLogger("agent-dispatch.supervisor-lifecycle")
_ORIGINS = ("installer.sh", "installer.ps1", "supervisor-daemon", "retirement-cli")
_ENABLED_STATES = {
    "enabled", "enabled-runtime", "disabled", "masked", "masked-runtime",
    "static", "linked", "linked-runtime", "alias", "indirect", "generated",
    "transient", "bad", "absent", "unknown",
}
_ACTIVE_STATES = {
    "active", "inactive", "failed", "activating", "deactivating", "reloading",
    "maintenance", "refreshing", "absent", "unknown",
}


@dataclass(frozen=True)
class Invocation:
    origin: str
    verb: str
    correlation: str
    initiator_pid: int
    parent_pid: int

    @classmethod
    def create(cls, origin: str, verb: str) -> Invocation:
        return cls(origin, verb, f"{os.getpid()}-{time.time_ns()}", os.getpid(), os.getppid())


def selection_facts(env_file: Path | None, *, windows_adapter: bool = False) -> dict[str, object]:
    values: dict[str, str] = {}
    state = "not-sampled" if env_file is None else "absent"
    if env_file is not None:
        try:
            for line in env_file.read_text(encoding="utf-8").splitlines():
                key, separator, value = line.partition("=")
                if windows_adapter:
                    key = key.upper()
                if separator and key.strip() in {
                    "AGENT_DISPATCH_SUPERVISE_MODE", "AGENT_DISPATCH_SUPERVISE_LABELS",
                    "AGENT_DISPATCH_SUPERVISE_MACHINE",
                }:
                    parsed = value.replace('"', "").replace("'", "").strip()
                    key = key.strip()
                    if windows_adapter and key in {
                        "AGENT_DISPATCH_SUPERVISE_MODE", "AGENT_DISPATCH_SUPERVISE_LABELS",
                    }:
                        if key == "AGENT_DISPATCH_SUPERVISE_MODE":
                            if parsed.casefold() == "serve":
                                values[key] = "serve"
                        elif re.sub(r"[\s,]", "", parsed):
                            values[key] = parsed
                    else:
                        values[key] = parsed
            state = "read"
        except FileNotFoundError:
            pass
        except (OSError, UnicodeError):
            state = "unreadable"
            log.warning("supervisor lifecycle audit: selection env is unreadable")
    mode = re.sub(r"\s", "", values.get("AGENT_DISPATCH_SUPERVISE_MODE", ""))
    labels = re.sub(r"[\s,]", "", values.get("AGENT_DISPATCH_SUPERVISE_LABELS", ""))
    if state in {"not-sampled", "unreadable"}:
        return {
            "env_state": state, "mode": None, "label_opt_in": None,
            "configured_enabled": None, "machine": None,
            "scope_source": "not-sampled", "profile": env_file.stem if env_file else None,
        }
    return {
        "env_state": state,
        "mode": "serve" if mode == "serve" else "legacy",
        "label_opt_in": bool(labels),
        "configured_enabled": mode == "serve" or bool(labels),
        "machine": values.get("AGENT_DISPATCH_SUPERVISE_MACHINE") or None,
        "scope_source": "explicit-machine" if values.get("AGENT_DISPATCH_SUPERVISE_MACHINE") else "runtime-resolution",
        "profile": env_file.stem if env_file is not None else None,
    }


def audit(
    root: Path, action: str, reason: str, *,
    invocation: Invocation, outcome: str = "begin", version: str | None = None,
    unit: str | None = None, env_file: Path | None = None,
    native_enabled: str = "unknown", native_active: str = "unknown",
    machine: str | None = None, environment: str | None = None,
    exit_code: int | None = None,
    native_main_pid: int | None = None,
    selected_pids: list[int] | None = None, retired_pids: list[int] | None = None,
) -> bool:
    detail = {
        "schema": "agent-dispatch.supervisor-lifecycle/v1",
        "attribution": "source-owned-action",
        "reason": reason,
        "origin": invocation.origin,
        "verb": invocation.verb,
        "correlation": invocation.correlation,
        "initiator_pid": invocation.initiator_pid,
        "parent_pid": invocation.parent_pid,
        "unit": unit,
        "native_enabled": native_enabled if native_enabled in _ENABLED_STATES else "unknown",
        "native_active": native_active if native_active in _ACTIVE_STATES else "unknown",
        "native_main_pid": native_main_pid if native_main_pid and native_main_pid > 0 else None,
        "selection_observation": "audit-time",
        # A disabled unit is an observation, not proof of who disabled it.
        "native_enablement_provenance": "unknown",
        "desired_enabled": False if reason in {
            "prior-native-disabled", "prior-native-masked", "installer-inert",
            "explicit-no-supervisor", "explicit-client-only", "client-only-policy",
        } else True if reason == "configured-opt-in" else None,
        **selection_facts(env_file, windows_adapter=invocation.origin == "installer.ps1"),
    }
    if machine is not None:
        detail.update(machine=machine, scope_source="resolved-runtime", mode="serve")
    if environment is not None:
        detail["environment"] = environment
    if exit_code is not None:
        detail["exit_code"] = exit_code
    if selected_pids is not None:
        detail["selected_pids"] = selected_pids
    if retired_pids is not None:
        detail["retired_pids"] = retired_pids
    result = lifecycle.record(
        root, f"supervisor-{action}", service="agent-dispatch",
        outcome=outcome, version=version, detail=detail,
    )
    if result is None:
        log.warning("supervisor lifecycle audit unavailable for %s (%s); action is not suppressed", action, reason)
        return False
    return True


def daemon_audit(daemon: SupervisorDaemon, action: str, reason: str, *, outcome: str = "begin") -> None:
    try:
        root = daemon._self_update_install_dir()
        invocation = getattr(daemon, "_lifecycle_invocation", None)
        if invocation is None:
            invocation = Invocation.create("supervisor-daemon", "serve")
            daemon._lifecycle_invocation = invocation
        audit(
            root, action, reason, invocation=invocation, outcome=outcome,
            version=daemon._self_update_running_version,
            machine=daemon.machine, environment=daemon.env or "default",
            unit=getattr(daemon, "_self_update_native_unit", None),
        )
    except Exception:
        log.warning("supervisor lifecycle audit unavailable for %s; action is not suppressed", action, exc_info=True)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--action", required=True)
    parser.add_argument("--reason", required=True)
    parser.add_argument("--origin", choices=_ORIGINS, required=True)
    parser.add_argument("--verb", required=True)
    parser.add_argument("--correlation", required=True)
    parser.add_argument("--initiator-pid", type=int, required=True)
    parser.add_argument("--parent-pid", type=int, required=True)
    parser.add_argument("--outcome", choices=("begin", "ok", "fail", "skipped"), default="begin")
    parser.add_argument("--version")
    parser.add_argument("--unit")
    parser.add_argument("--env-file", type=Path)
    parser.add_argument("--native-enabled", default="unknown")
    parser.add_argument("--native-active", default="unknown")
    parser.add_argument("--exit-code", type=int)
    parser.add_argument("--native-main-pid", type=int)
    args = parser.parse_args(argv)
    invocation = Invocation(args.origin, args.verb, args.correlation, args.initiator_pid, args.parent_pid)
    ok = audit(
        args.root, args.action, args.reason, invocation=invocation,
        outcome=args.outcome, version=args.version, unit=args.unit,
        env_file=args.env_file, native_enabled=args.native_enabled,
        native_active=args.native_active, exit_code=args.exit_code,
        native_main_pid=args.native_main_pid,
    )
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
