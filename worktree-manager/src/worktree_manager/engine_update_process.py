"""Selected-engine update handoff with inherited streams and scoped custody."""

from __future__ import annotations

import os
import subprocess
import sys
from typing import Any


def run_engine_passthrough(
    project: str | None, args: list[str], *,
    timeout: int | None = None, environment: dict[str, str] | None = None,
    terminal_aware: bool = False, headless: bool = False,
    engine_command: list[str] | None = None,
) -> int:
    """Run the exact selected engine with inherited stdio and no effectful retry."""
    from . import engine_client as ec
    from . import picker_review
    from .engine_toolchain_context import require_context
    from .update_driver_cli import handoff_environment, handoff_launch_options
    from agent_procutil import no_window_kwargs

    picker_review.require_writable()
    base = engine_command if engine_command is not None else (
        ec.engine_base_command() if environment is None else ec.installed_engine_command(environment)
    )
    if base is None:
        raise ec.EngineError(f"the {ec.ENGINE_BIN} engine is not installed", install_hint=True)
    cmd = [*base, *(["--project", project] if project else []), *args]
    handoff = handoff_environment(args)
    child_environment = ec._engine_environment() if environment is None else ec._engine_environment(environment)
    require_context(project, args, list(base), child_environment)
    headless_options: dict[str, Any] = no_window_kwargs() if headless else {}
    if headless and os.name == "nt":
        headless_options.update(stdout=sys.stdout, stderr=sys.stderr)
    try:
        return subprocess.run(
            cmd, timeout=timeout, check=False,
            **{**handoff_launch_options(child_environment, handoff, update=args[:1] == ["update"]),
               **(ec._terminal_aware_launch_options() if terminal_aware else {}),
               **headless_options},
        ).returncode
    except subprocess.TimeoutExpired as error:
        raise ec.EngineError(f"{ec.ENGINE_BIN} {' '.join(args)} timed out") from error
    except (OSError, ValueError) as error:
        raise ec.EngineError(f"could not run {ec.ENGINE_BIN}: {error}") from error
