"""Old portable Host entrypoint; preserve its explicit guest policy-owner check."""

from __future__ import annotations

import os
from typing import Any

from agent_remote_driver.session_host import launcher
from agent_remote_driver.session_host.receiver_policy import require_path_hosting


async def run_host(child_argv: list[str], **kwargs: Any) -> None:
    """Compose the legacy policy boundary with canonical Host execution."""
    if kwargs.get("lifetime_policy", "legacy") != "legacy":
        raise ValueError("Portable legacy Host requires the legacy lifetime policy")
    if kwargs.get("ws_port") is not None:
        raise ValueError("Portable legacy Host does not support WebSocket")

    def before_exec() -> None:
        if (
            (kwargs.get("env") or {}).get(launcher._ACQUISITION_ENV)
            or os.environ.get(launcher._ACQUISITION_ENV)
        ):
            raise ValueError("Portable legacy Host cannot validate original local launch custody")
        if kwargs.get("policy_context") is None:
            require_path_hosting(kwargs.get("cwd"))

    before_exec()
    kwargs.setdefault("lifetime_policy", "legacy")
    await launcher.run_host(child_argv, before_exec=before_exec, **kwargs)


def main(argv: list[str] | None = None) -> int:
    """Preserve the supported old portable entrypoint."""
    return launcher.main(argv, run=run_host, legacy=True)
