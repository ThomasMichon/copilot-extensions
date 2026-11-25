#!/usr/bin/env python3
"""Reject plugin hooks that start Python (docs/patterns/hook-architecture.md).

Hooks do their primary logic in native shell and read files that services
publish. Existing hooks that still start Python are listed in
``tools/hook-architecture-allowlist.json``; that list may only shrink.
"""

from __future__ import annotations

import json
import re
import sys
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
ALLOWLIST = REPO / "tools" / "hook-architecture-allowlist.json"
STATUSES = frozenset({"pending-conversion", "binstub-runtime"})
SHELL_KEYS = ("bash", "powershell")
SCRIPT_REF = re.compile(r"[A-Za-z0-9_.-]+\.(?:sh|ps1|cmd)\b")
SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("python command", re.compile(
        r"(?i)(?:command\s+-v|Get-Command|\bwhich)\s+(?:[^;|&\n]*?\s)?(?:python3?|py)\b"
        r"|(?:^|[\s;|&(`{])(?:&\s*)?(?:python3?|py)(?:\.exe)?\s+(?:-[A-Za-z0-9](?:\s|$)|['\"$])"
    )),
    ("interpreter variable call", re.compile(
        r"(?i)(?:(?:^|[\s;|({])&|-FilePath)\s+['\"]?\$\{?\w*py\w*\}?"
        r"|\"\$\{?\w*py\w*\}?\"\s+[-\"$]"
    )),
    ("uv launch", re.compile(r"(?i)(?:^|[\s;|&(`{'\"])uv(?:\.exe)?['\"]?\s+run\b|\buvx\b")),
    ("Python selector helper", re.compile(
        r"(?i)hook-uv-python|bootstrap-python|_bootstrap_python|\b[a-z]+(?:_[a-z]+)*_python\b"
        r"|Invoke-UvBootstrap\w*"
    )),
    ("inline Python heredoc", re.compile(r"<<-?\s*['\"]?PY['\"]?")),
)


@dataclass(frozen=True)
class Finding:
    """One hook source that starts Python."""

    key: str
    reason: str


def _code_lines(text: str, powershell: bool) -> list[str]:
    lines: list[str] = []
    in_block = False
    for line in text.splitlines():
        stripped = line.strip()
        if powershell:
            if in_block:
                if "#>" in stripped:
                    in_block = False
                continue
            if stripped.startswith("<#"):
                in_block = "#>" not in stripped[2:]
                continue
        if stripped.startswith("#") or stripped.startswith("::") or stripped.lower().startswith("rem "):
            continue
        lines.append(line)
    return lines


def python_starts(text: str, powershell: bool) -> list[str]:
    """Return the signal names that show the text starts Python."""
    code = "\n".join(_code_lines(text, powershell))
    return [name for name, pattern in SIGNALS if pattern.search(code)]


def _relative(path: Path, repo: Path) -> str:
    return path.relative_to(repo).as_posix()


def scan(repo: Path) -> list[Finding]:
    """Find every hooks.json command and directly named script that starts Python."""
    findings: dict[str, Finding] = {}
    for hooks_json in sorted((repo / "plugins").glob("*/hooks.json")):
        plugin = hooks_json.parent
        data = json.loads(hooks_json.read_text(encoding="utf-8"))
        hooks = data.get("hooks", {}) if isinstance(data, dict) else {}
        for event, entries in sorted(hooks.items()):
            if not isinstance(entries, list):
                continue
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                for shell in SHELL_KEYS:
                    command = entry.get(shell)
                    if not isinstance(command, str):
                        continue
                    key = f"{_relative(hooks_json, repo)}#{event}[{index}].{shell}"
                    signals = python_starts(command, shell == "powershell")
                    if signals:
                        findings[key] = Finding(key, ", ".join(signals))
                    for name in sorted(set(SCRIPT_REF.findall(command))):
                        script = plugin / "scripts" / name
                        if not script.is_file():
                            continue
                        script_key = _relative(script, repo)
                        if script_key in findings:
                            continue
                        signals = python_starts(
                            script.read_text(encoding="utf-8", errors="replace"),
                            script.suffix == ".ps1",
                        )
                        if signals:
                            findings[script_key] = Finding(script_key, ", ".join(signals))
    return sorted(findings.values(), key=lambda finding: finding.key)


def load_allowlist(path: Path) -> dict[str, dict[str, str]]:
    """Load and shape-check the allowlist."""
    data = json.loads(path.read_text(encoding="utf-8"))
    entries = data.get("entries") if isinstance(data, dict) else None
    if not isinstance(entries, dict):
        raise ValueError(f"{path.name}: expected an object with an 'entries' object")
    result: dict[str, dict[str, str]] = {}
    for key, value in entries.items():
        if not isinstance(value, dict):
            raise ValueError(f"{path.name}: entry {key} must be an object")
        status = value.get("status")
        reason = value.get("reason")
        if status not in STATUSES:
            raise ValueError(f"{path.name}: entry {key} has unknown status {status!r}")
        if not isinstance(reason, str) or not reason.strip():
            raise ValueError(f"{path.name}: entry {key} needs a reason")
        result[str(key)] = {"status": str(status), "reason": reason}
    return result


def find_problems(repo: Path, allowlist_path: Path) -> list[str]:
    """Compare the scan with the allowlist in both directions."""
    try:
        allowed = load_allowlist(allowlist_path)
    except (OSError, ValueError) as error:
        return [str(error)]
    findings = {finding.key: finding for finding in scan(repo)}
    problems = [
        f"{key} starts Python ({finding.reason}) and is not in {allowlist_path.name}"
        for key, finding in sorted(findings.items())
        if key not in allowed
    ]
    problems.extend(
        f"{key} no longer starts Python; remove it from {allowlist_path.name}"
        for key in sorted(allowed)
        if key not in findings
    )
    return problems


def main(argv: list[str] | None = None) -> int:
    """Run the check against this repository."""
    args = sys.argv[1:] if argv is None else argv
    if args == ["--list"]:
        for finding in scan(REPO):
            print(f"{finding.key}: {finding.reason}")
        return 0
    if args:
        print("usage: check-hook-architecture.py [--list]", file=sys.stderr)
        return 2
    problems = find_problems(REPO, ALLOWLIST)
    if problems:
        print("[FAIL] Hook architecture (docs/patterns/hook-architecture.md):")
        for problem in problems:
            print(f"  - {problem}")
        print(
            "\nHooks run native shell and read published files. A hook may start "
            "Python only as a listed, fire-and-forget exception."
        )
        return 1
    print(f"[OK] Hook architecture: {len(load_allowlist(ALLOWLIST))} listed exceptions, no new Python starts")
    return 0


if __name__ == "__main__":
    sys.exit(main())
