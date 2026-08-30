"""Stable launcher for provider-owned commands in the active runtime."""

from __future__ import annotations

import os
import sys
from pathlib import Path

_ACTIVE_RUNTIME_ARG = "--agent-containers-active-runtime"


def _active_python(root: Path) -> Path:
    try:
        version = (root / "current-version").read_text(encoding="utf-8").strip()
    except (OSError, UnicodeError) as exc:
        raise RuntimeError(
            f"agent-containers active runtime marker is unreadable: "
            f"{root / 'current-version'}"
        ) from exc
    if not version:
        raise RuntimeError("agent-containers current-version is empty")
    scripts = "Scripts" if os.name == "nt" else "bin"
    executable = "python.exe" if os.name == "nt" else "python"
    python = root / "versions" / version / scripts / executable
    if not python.is_file():
        raise RuntimeError(
            f"agent-containers active runtime interpreter is missing: {python}"
        )
    return python


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == _ACTIVE_RUNTIME_ARG:
        del sys.argv[1]
        from agent_containers.provider_cli import main as provider_main

        return provider_main()

    launcher = Path(__file__).resolve()
    python = _active_python(launcher.parent)
    os.execv(
        str(python),
        [
            str(python),
            "-I",
            str(launcher),
            _ACTIVE_RUNTIME_ARG,
            *sys.argv[1:],
        ],
    )
    return 127


if __name__ == "__main__":
    raise SystemExit(main())
