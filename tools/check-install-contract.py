#!/usr/bin/env python3
"""Enforce the install contract (docs/install-contract.md) across plugins.

Each plugin with native installers (scripts/install.ps1 / install.sh) must:
  1. install the package via `uv pip install` (no file-copy of the package),
  2. emit no binstub that sets PYTHONPATH to a runtime lib/ dir,
  3. write a schema_version 3 deploy manifest with a `source` block,
  4. carry a source-kind resolver identical (per language) across plugins.

Run manually:  python tools/check-install-contract.py
Exit code 0 = conformant, 1 = violations (suitable for a pre-push hook).
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"

# A binstub/install script must not point PYTHONPATH at a runtime lib/ dir.
FORBIDDEN_PYTHONPATH = re.compile(r"PYTHONPATH[^\n]*\.agent-[a-z]+[\\/]lib", re.IGNORECASE)


def _extract_block(text: str, start_marker: str, open_char: str, close_char: str) -> str | None:
    """Return the balanced {...} block beginning at the first start_marker line."""
    idx = text.find(start_marker)
    if idx < 0:
        return None
    brace = text.find(open_char, idx)
    if brace < 0:
        return None
    depth = 0
    for i in range(brace, len(text)):
        if text[i] == open_char:
            depth += 1
        elif text[i] == close_char:
            depth -= 1
            if depth == 0:
                return text[idx : i + 1]
    return None


def _norm(s: str | None) -> str | None:
    if s is None:
        return None
    return re.sub(r"\s+", " ", s).strip()


def check() -> int:
    violations: list[str] = []
    ps1_resolvers: dict[str, str | None] = {}
    sh_resolvers: dict[str, str | None] = {}

    plugins = sorted(
        p for p in PLUGINS_DIR.iterdir()
        if (p / "scripts" / "install.ps1").exists()
        or (p / "scripts" / "install.sh").exists()
    )
    if not plugins:
        print("No plugins with install scripts found.", file=sys.stderr)
        return 1

    for plugin in plugins:
        name = plugin.name
        for script in ("install.ps1", "install.sh"):
            path = plugin / "scripts" / script
            if not path.exists():
                violations.append(f"{name}: missing scripts/{script}")
                continue
            text = path.read_text(encoding="utf-8", errors="replace")

            if "uv pip install" not in text:
                violations.append(f"{name}/{script}: no 'uv pip install' (package must not be file-copied)")
            if FORBIDDEN_PYTHONPATH.search(text):
                violations.append(f"{name}/{script}: binstub sets PYTHONPATH to a runtime lib/ dir")
            if "schema_version" not in text or '"source"' not in text and "source " not in text:
                violations.append(f"{name}/{script}: no schema_version 3 manifest with a source block")
            elif not re.search(r"schema_version[\"'=:\s]+3", text):
                violations.append(f"{name}/{script}: manifest is not schema_version 3")

            if script == "install.ps1":
                ps1_resolvers[name] = _norm(_extract_block(text, "function Get-SourceKind", "{", "}"))
            else:
                sh_resolvers[name] = _norm(_extract_block(text, "_source_kind()", "{", "}"))

    _check_identical("Get-SourceKind (ps1)", ps1_resolvers, violations)
    _check_identical("_source_kind (sh)", sh_resolvers, violations)

    if violations:
        print("Install-contract violations:", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        print("\nSee docs/install-contract.md.", file=sys.stderr)
        return 1
    print(f"Install contract OK ({len(plugins)} plugins).")
    return 0


def _check_identical(label: str, resolvers: dict[str, str | None], violations: list[str]) -> None:
    present = {k: v for k, v in resolvers.items() if v}
    missing = [k for k, v in resolvers.items() if not v]
    for k in missing:
        violations.append(f"{k}: missing {label} source-kind resolver")
    distinct = set(present.values())
    if len(distinct) > 1:
        violations.append(f"{label} resolver differs across plugins: {sorted(present)}")


if __name__ == "__main__":
    raise SystemExit(check())
