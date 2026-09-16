from __future__ import annotations

import os
import re
from pathlib import Path

from scan_plugin_sources import PluginSource

BLOCKING = "blocking"
WARNING = "warning"

CONFIG_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".psd1", ".env", ".ini", ".conf"}
PRUNE_DIRS = {
    ".git",
    "node_modules",
    ".venv",
    "venv",
    "dist",
    "build",
    "__pycache__",
    "logs",
    ".mypy_cache",
    ".pytest_cache",
    "target",
    ".idea",
    "site-packages",
}
SECRET_KEY = re.compile(
    r"""(?ix)
    \b(password|passwd|secret|token|api[_-]?key|access[_-]?key|
       client[_-]?secret|private[_-]?key)\b
    \s*[:=]\s*
    (?P<val>.+)$
    """
)
SAFE_VALUE = re.compile(
    r"""(?ix)
    ^\s*["'`]?(
      \$ |
      ` |
      < |
      \{ |
      \[ | \( |
      null|none|true|false|changeme|example|your[_-]|xxx+|\.\.\.|
      placeholder|redacted|required|optional|vault|env: |
      ["']["']
    )
    """
)
CREDENTIAL_SHAPE = re.compile(r"""^["']?[A-Za-z0-9+/=_.\-]{12,}["']?[,\s]*$""")
SSH_RAW_IP = re.compile(
    r"""(?ix)
    \b(ssh|scp|rsync)\b
    [^\n]*?
    (?<![\w.])
    (?:[\w.-]+@)?
    (?P<ip>(?:\d{1,3}\.){3}\d{1,3})
    """
)
NEGATIVE_EXAMPLE = re.compile(
    r"(?i)\b(wrong|never|don'?t|do not|avoid|bad|incorrect|counter-?example)\b|\u274c"
)


def _walk_customization_files(root: Path):
    """Yield customization-surface files, pruning heavy/irrelevant trees."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames if d not in PRUNE_DIRS and not (d.startswith(".") and d != ".github")
        ]
        for filename in filenames:
            yield Path(dirpath) / filename


def scan_text_files(
    root: Path,
    report,
    plugin_sources: list[PluginSource] | None = None,
) -> None:
    scan_roots = [
        source.payload_root for source in (plugin_sources or []) if source.controlled
    ]
    scan_roots.append(root)
    seen: set[Path] = set()
    for scan_root in scan_roots:
        owned_plugin_payload = scan_root != root
        for path in _walk_customization_files(scan_root):
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            name = path.name
            suffix = path.suffix.lower()
            parts = set(path.parts)
            under_github = ".github" in parts
            is_mcp = name in (".mcp.json", "mcp-config.json")
            is_surface_md = (
                name == "SKILL.md"
                or name.endswith(".agent.md")
                or name in {
                    "AGENTS.md",
                    "CLAUDE.md",
                    "GEMINI.md",
                    "copilot-instructions.md",
                }
                or name.endswith(".instructions.md")
            )
            config_target = suffix in CONFIG_SUFFIXES and (
                under_github or is_mcp or "plugins" in parts or owned_plugin_payload
            )
            if not (config_target or is_surface_md):
                continue
            try:
                lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
            except OSError:
                continue
            for number, line in enumerate(lines, 1):
                if config_target:
                    secret_match = SECRET_KEY.search(line)
                    if secret_match:
                        value = secret_match.group("val").strip().strip(",")
                        token = value.split()[0] if value.split() else value
                        if not SAFE_VALUE.match(value) and CREDENTIAL_SHAPE.match(token):
                            report.add(
                                BLOCKING,
                                "secret",
                                f"{path}:{number}",
                                "possible hardcoded secret assigned to "
                                f"{secret_match.group(1)}",
                            )
                ip_match = SSH_RAW_IP.search(line)
                if ip_match:
                    ip = ip_match.group("ip")
                    window = "\n".join(lines[max(0, number - 4) : number])
                    if (
                        not ip.startswith(("0.", "127.", "255."))
                        and not NEGATIVE_EXAMPLE.search(window)
                    ):
                        report.add(
                            WARNING,
                            "raw-ip",
                            f"{path}:{number}",
                            f"ssh/scp/rsync targets raw IP {ip} (use an alias)",
                        )
