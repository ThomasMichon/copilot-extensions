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
SCRIPT_REF = re.compile(r"[A-Za-z0-9_./\\-]+\.(?:sh|ps1|cmd)\b")
# A command name sits at the start of a line, after a separator or call
# operator, or after a keyword that runs its argument as a command.
_COMMAND_POSITION = (
    r"(?:^|[;|&(`{!@]"
    r"|\b(?:if|elif|while|until|then|do|else|exec|nohup|time|command|env|sudo|call)\s)\s*"
)
# An interpreter named directly or through a qualified path, optionally quoted.
_INTERPRETER = r"['\"]?(?:[^\s'\";|&]*[/\\])?(?:python[0-9.]*|pythonw|py)(?:\.exe)?['\"]?(?=\s|$)"
SIGNALS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("python command", re.compile(
        r"(?im)(?:command\s+-v|Get-Command|\bwhich)\s+(?:[^;|&\n]*?\s)?(?:python3?|py)\b"
        + r"|" + _COMMAND_POSITION
        + r"(?:&\s*)?" + _INTERPRETER
    )),
    ("interpreter variable call", re.compile(
        r"(?im)(?:(?:^|[\s;|({])&|-FilePath)\s+['\"]?\$\{?\w*py\w*\}?"
        r"|\"\$\{?\w*py\w*\}?\"\s+\S"
        r"|" + _COMMAND_POSITION + r"\$\{?\w*py\w*\}?\s+(?![-+*/.]?=)\S"
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


def _strip_inline_comment(line: str, powershell: bool) -> str:
    """Drop a trailing ``#`` comment, keeping ``#`` inside quotes or words."""
    escape = "`" if powershell else "\\"
    quote = ""
    index = 0
    while index < len(line):
        char = line[index]
        if char == escape and quote != "'":
            index += 2
            continue
        if quote:
            if char == quote:
                quote = ""
        elif char in "'\"":
            quote = char
        elif char == "#" and (index == 0 or line[index - 1].isspace() or line[index - 1] == ";"):
            return line[:index]
        index += 1
    return line


def _code_lines(text: str, powershell: bool, cmd: bool = False) -> list[str]:
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
        if cmd:
            if stripped.startswith("::") or stripped.lower().startswith("rem ") or stripped.lower() == "rem":
                continue
            lines.append(line)
            continue
        if stripped.startswith("#"):
            continue
        lines.append(_strip_inline_comment(line, powershell))
    return lines


def python_starts(text: str, powershell: bool, cmd: bool = False) -> list[str]:
    """Return the signal names that show the text starts Python."""
    code = "\n".join(_code_lines(text, powershell, cmd))
    return [name for name, pattern in SIGNALS if pattern.search(code)]


def _resolve_script(plugin: Path, reference: str) -> Path | None:
    """Resolve a script named in a hook command to a file in the plugin."""
    relative = reference.replace("\\", "/").lstrip("./")
    parts = [part for part in relative.split("/") if part and part != "."]
    if not parts:
        return None
    # The reference may carry a variable tail (``$r/scripts/x.sh`` yields
    # ``r/scripts/x.sh``), so try each shorter suffix of the path as well.
    candidates = [plugin.joinpath(*parts[start:]) for start in range(len(parts))]
    if len(parts) == 1:
        candidates.insert(0, plugin / "scripts" / parts[0])
    root = plugin.resolve()
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_file() and (resolved == root or root in resolved.parents):
            return candidate
    return None


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
                        script = _resolve_script(plugin, name)
                        if script is None:
                            continue
                        script_key = _relative(script, repo)
                        if script_key in findings:
                            continue
                        signals = python_starts(
                            script.read_text(encoding="utf-8", errors="replace"),
                            script.suffix == ".ps1",
                            script.suffix == ".cmd",
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
