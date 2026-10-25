"""Installer capability probes."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

HUMAN_UTF8_ENV = "COPILOT_EXTENSIONS_HUMAN_UTF8"


def powershell_file_target(argv) -> Path | None:
    if not argv or Path(str(argv[0])).name.lower() not in {
        "powershell", "powershell.exe", "pwsh", "pwsh.exe",
    }:
        return None
    for index, argument in enumerate(argv[:-1]):
        if str(argument).lower() == "-file":
            return Path(argv[index + 1])
    return None


def powershell_human_utf8_supported(target: Path) -> bool:
    """Read explicit, entrypoint-bound package metadata; never guess from source."""
    try:
        declaration = target.with_suffix(".human-output.json")
        helper = target.parent / "installer-human-output.ps1"
        if any(path.is_symlink() for path in (target, helper, declaration, target.parent)):
            return False
        data = json.loads(declaration.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return False

        def digest(path):
            return hashlib.sha256(path.read_bytes().replace(b"\r\n", b"\n")).hexdigest()

        return (
            data.get("version") == 1 and data.get("encoding") == "utf-8"
            and data.get("entrypoint") == target.name
            and data.get("initializer") == helper.name
            and data.get("entrypoint_sha256") == digest(target)
            and data.get("initializer_sha256") == digest(helper)
        )
    except (OSError, ValueError):
        return False


def posix_zero_downtime_flag_supported(plugin_dir: Path) -> bool:
    install_sh = plugin_dir / "scripts" / "install.sh"
    try:
        return install_sh.is_file() and "--zero-downtime" in install_sh.read_text(encoding="utf-8")
    except OSError:
        return False
