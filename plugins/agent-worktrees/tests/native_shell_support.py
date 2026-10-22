"""Lazy, bounded shell admission for explicitly selected native tests."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

from agent_procutil import no_window_kwargs


class ShellUnavailable(RuntimeError):
    pass


def require_native_tier() -> None:
    if os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1":
        raise ShellUnavailable("native shell tests require --allow-explicit-tiers")


def run_headless(argv: list[str], *, timeout: float = 30, **kwargs):
    return subprocess.run(argv, timeout=timeout, **no_window_kwargs(), **kwargs)


def _is_wsl_launcher(candidate: str) -> bool:
    parts = candidate.replace("\\", "/").casefold().split("/")
    return "windowsapps" in parts or (
        len(parts) >= 2 and parts[-2] == "system32" and parts[-1] in {"bash", "bash.exe"}
    )


def resolve_native_bash() -> str:
    require_native_tier()
    return admit_native_bash()


def admit_native_bash() -> str:
    """Validate a shell without opting an ordinary test into optional tiers."""
    windows = sys.platform == "win32"
    git_bash = Path(r"C:\Program Files\Git\bin\bash.exe")
    if windows and git_bash.is_file():
        candidate = str(git_bash)
    elif windows:
        search_path = os.pathsep.join(
            part
            for part in os.get_exec_path()
            if not _is_wsl_launcher(part.rstrip("\\/") + "/bash.exe")
        )
        candidate = shutil.which("bash", path=search_path)
    else:
        candidate = shutil.which("bash")
    if candidate is None:
        raise ShellUnavailable("Bash is unavailable")
    if windows and _is_wsl_launcher(candidate):
        raise ShellUnavailable("Bash resolves to a Windows WSL launcher")
    probes = [
        ("exit-code", "exit 7", lambda result: result.returncode == 7),
        (
            "pipefail",
            "set -euo pipefail\n"
            "_x=$(printf 'a\\n' | grep 'zzz' | head -1 | sed 's/a/b/')\n"
            "echo REACHED\n",
            lambda result: result.returncode != 0 and "REACHED" not in result.stdout,
        ),
    ]
    if windows:
        probes.append(("cygpath", "cygpath -u -- /", lambda result: result.returncode == 0))
    for name, script, conforms in probes:
        try:
            result = run_headless(
                [candidate, "-c", script],
                capture_output=True,
                text=True,
                timeout=5,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            raise ShellUnavailable(f"Bash {name} probe failed: {type(exc).__name__}") from exc
        if not conforms(result):
            raise ShellUnavailable(f"Bash {name} probe is non-conformant")
    return candidate


def resolve_native_powershell() -> str:
    require_native_tier()
    candidate = (
        (shutil.which("powershell.exe") if sys.platform == "win32" else None)
        or shutil.which("pwsh")
        or shutil.which("powershell")
    )
    if candidate is None:
        raise ShellUnavailable("PowerShell is unavailable")
    return candidate


def bash_path(bash: str, path: Path) -> str:
    if sys.platform != "win32":
        return str(path)
    result = run_headless(
        [bash, "-c", 'cygpath -u -- "$1"', "_", str(path)],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    converted = result.stdout.strip()
    assert converted, "cygpath returned an empty path"
    return converted
