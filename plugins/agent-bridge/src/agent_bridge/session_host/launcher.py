"""Bridge startup custody adapter for the independent Session Host."""

from __future__ import annotations

import os
import sys
from typing import Any

from agent_remote_driver.session_host import launcher as _core
from agent_remote_driver.session_host.launcher import (  # noqa: F401
    HostHandle, _ACP_STDIO_LIMIT_BYTES, _CrashTailCapture,
    _detach_stdio_from_frontend, _kill_host_process_tree, _resolve_child_exe,
    _write_host_state, apply_host_survival, contained_test_mode, host_spawn_kwargs,
    subprocess,
)


def launch_session_host(
    child_argv: list[str], *, acquisition_launch: Any = None,
    retained_recovery: Any = None, **kwargs: Any,
) -> HostHandle:
    """Retain original operation custody while the core owns actual execution."""
    from ..portable_policy import selected_context

    if acquisition_launch is not None:
        from ..acquisition_launch_owner import AcquisitionLaunchOwner
        from ..creation_launch_owner import CreationLaunchOwner
        from ..runtime_custody import RuntimeCustodyError

        if (
            type(acquisition_launch) not in (AcquisitionLaunchOwner, CreationLaunchOwner)
            or kwargs.get("session_id", "") != acquisition_launch.session_id
        ):
            raise RuntimeCustodyError("Local Host launch requires its original local owner")
        acquisition_launch.require_current()
    if retained_recovery is not None:
        from ..retained_session_recovery import RetainedRecovery

        if (
            type(retained_recovery) is not RetainedRecovery or acquisition_launch is not None
            or kwargs.get("session_id", "") != retained_recovery.session_id
        ):
            raise ValueError("Retained Host launch requires its original native recovery owner")
        retained_recovery.require_current(check_task=False)
    return _core.launch_session_host(
        child_argv, acquisition_launch=retained_recovery or acquisition_launch,
        policy_context=selected_context(), **kwargs,
    )


def _receiving_check(child_argv: list[str], cwd: str | None, env: dict, session_id: str):
    cwd = cwd or os.getcwd()
    admission = env.get(_core._ACQUISITION_ENV) or os.environ.get(_core._ACQUISITION_ENV)

    def before_exec() -> None:
        if admission is not None:
            from ..local_host_admission import require_child_admission

            require_child_admission(admission, cwd=cwd, session_id=session_id, argv=child_argv)
            effective_env = {**os.environ, **env}
            require_child_admission(
                admission, cwd=cwd, session_id=effective_env.get("AGENT_BRIDGE_SESSION_ID", ""),
            )

    return before_exec


async def _spawn_child(
    argv: list[str], cwd: str | None, env: dict[str, str] | None, **kwargs: Any,
) -> Any:
    """Retained direct-import compatibility; new launches run the core archive."""
    effective_env = {**os.environ, **(env or {})}
    kwargs.setdefault("before_exec", _receiving_check(
        argv, cwd, env or {}, effective_env.get("AGENT_BRIDGE_SESSION_ID", ""),
    ))
    return await _core._spawn_child(argv, cwd, env, **kwargs)


async def run_host(child_argv: list[str], **kwargs: Any) -> None:
    """Supply fresh Bridge-specific startup checks without changing core lifetime."""
    from ..portable_policy import selected_context

    before_exec = _receiving_check(
        child_argv, kwargs.get("cwd"), kwargs.get("env") or {}, kwargs.get("session_id", ""),
    )
    if kwargs.get("policy_context") is None:
        kwargs["policy_context"] = selected_context()
    kwargs.setdefault("lifetime_policy", "legacy")
    await _core.run_host(child_argv, before_exec=before_exec, **kwargs)


def main(argv: list[str] | None = None) -> int:
    """Keep the retained Bridge entrypoint and its original-child admission."""
    argv = list(sys.argv[1:] if argv is None else argv)
    if argv[:2] == ["--preference-exec", "--"]:
        from agent_remote_driver.preference_exec import main as preference_exec_main

        raise SystemExit(preference_exec_main(argv[2:]))
    if argv == ["--support-json"]:
        return _core.main(argv)
    options = argv[:argv.index("--")] if "--" in argv else argv
    if not any(arg.split("=", 1)[0] == "--lifetime-policy" for arg in options):
        argv = ["--lifetime-policy", "legacy", *argv]
    # Parsing remains canonical; only the receiving admission is Bridge-owned.
    return _core.main(argv, run=run_host)


if __name__ == "__main__":
    sys.exit(main())
