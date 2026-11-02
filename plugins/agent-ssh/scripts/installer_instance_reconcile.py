"""Finite installer adapter over the shared native daemon-instance owner."""

from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import sys


def reconcile_installer_instances(
    helper_root: Path, *, registry_root: Path | None = None,
) -> bool:
    """Use this adapter's wrapper and the caller's explicit native binding root."""
    if registry_root is None and (
        os.environ.get("COPILOT_EXTENSIONS_TEST_CONTAINED") == "1"
        or os.environ.get("PYTEST_CURRENT_TEST")
    ):
        return True
    try:
        adapter_root = Path(__file__).resolve().parent
        if os.name == "nt":
            shell = Path(os.environ["SystemRoot"]) / "System32/WindowsPowerShell/v1.0/powershell.exe"
            helper = adapter_root / "installer-instance-reconcile.ps1"
            def quote(value: object) -> str:
                return "'" + str(value).replace("'", "''") + "'"
            command = (
                f". {quote(helper)}; Invoke-InstallerInstanceReconcile "
                f"-HelperRoot {quote(helper_root)}"
            )
            if registry_root is not None:
                command += f" -RegistryRoot {quote(registry_root)}"
            argv = [str(shell), "-NoLogo", "-NoProfile", "-NonInteractive", "-Command", command]
        else:
            shell = shutil.which("bash")
            if shell is None:
                raise RuntimeError("Native installer reconciliation requires Bash")
            helper = adapter_root / "installer-instance-reconcile.sh"
            argv = [
                shell, "-c", '. "$1"; reconcile_installer_instances "$2" "$3"',
                "installer-instance-reconcile", str(helper), str(helper_root),
                str(registry_root) if registry_root is not None else "",
            ]
        if helper.is_symlink() or not helper.is_file():
            raise FileNotFoundError("The installer adapter's own native wrapper is unavailable")
        result = subprocess.run(
            argv, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
            timeout=15, check=False,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode:
            raise RuntimeError("Native installer reconciliation failed")
        return True
    except (OSError, RuntimeError, subprocess.SubprocessError) as error:
        print(
            f"[installer-instance-gc] deferred: native-reconcile-failed ({type(error).__name__})",
            file=sys.stderr,
        )
        return False
