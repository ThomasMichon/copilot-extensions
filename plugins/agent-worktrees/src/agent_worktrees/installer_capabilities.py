"""Installer capability probes."""

from __future__ import annotations

from pathlib import Path


def posix_zero_downtime_flag_supported(plugin_dir: Path) -> bool:
    install_sh = plugin_dir / "scripts" / "install.sh"
    try:
        return install_sh.is_file() and "--zero-downtime" in install_sh.read_text(encoding="utf-8")
    except OSError:
        return False
