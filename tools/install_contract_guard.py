"""Shared checks used by the repository install-contract guard."""

from __future__ import annotations

import re
import tempfile
from pathlib import Path


def declares_stamp_provision(text: str, ext: str) -> bool:
    """Whether an entrypoint declares both deferred-provisioning actions."""
    if ext == "ps1":
        return ("'stamp'" in text) and ("'provision'" in text)
    return bool(re.search(r"\bstamp[)|]", text)) and bool(re.search(r"\bprovision[)|]", text))


PERSISTENT_ENV_START = (
    "# === install-contract:test-persistent-environment "
    "-- keep byte-identical across installers ==="
)
PERSISTENT_ENV_END = "# === end install-contract:test-persistent-environment ==="

_ENV_CALL = re.compile(
    r"\[(?:System\.)?Environment\]\s*::\s*"
    r"(?P<method>Get|Set)EnvironmentVariable\s*\(",
    re.IGNORECASE,
)
_PROCESS_TARGET = re.compile(
    r"^\s*(?:['\"]Process['\"]|"
    r"\[(?:System\.)?EnvironmentVariableTarget\]\s*::\s*Process)\s*$",
    re.IGNORECASE,
)
_REGISTRY_ENVIRONMENT_PATH = re.compile(
    r"(?:"
    r"(?:HKCU|Registry::HKEY_CURRENT_USER|HKEY_CURRENT_USER)\s*:"
    r"?[\\/]+Environment\b"
    r"|"
    r"(?:HKLM|Registry::HKEY_LOCAL_MACHINE|HKEY_LOCAL_MACHINE)\s*:"
    r"?[\\/]+SYSTEM[\\/]+CurrentControlSet[\\/]+Control[\\/]+"
    r"Session Manager[\\/]+Environment\b"
    r")",
    re.IGNORECASE,
)
_REGISTRY_API_ENVIRONMENT = re.compile(
    r"\[(?:Microsoft\.)?Win32\.Registry\]\s*::\s*"
    r"(?P<hive>CurrentUser|LocalMachine)"
    r"(?:(?!\n\s*\n).){0,500}?"
    r"(?:Environment|Session Manager[\\/]+Environment)",
    re.IGNORECASE | re.DOTALL,
)


def _without_canonical_adapter(text: str) -> str:
    start = text.find(PERSISTENT_ENV_START)
    if start < 0:
        return text
    end = text.find(PERSISTENT_ENV_END, start)
    if end < 0:
        return text
    end += len(PERSISTENT_ENV_END)
    return text[:start] + ("\n" * text[start:end].count("\n")) + text[end:]


def _lexical_views(text: str) -> tuple[str, str]:
    """Return comment-free source and its string-masked call-search view."""
    source = list(text)
    calls = list(text)
    index = 0
    length = len(text)
    quote: str | None = None
    block_comment = False
    while index < length:
        char = text[index]
        if block_comment:
            if char == "#" and text[index : index + 2] == "#>":
                source[index : index + 2] = calls[index : index + 2] = "  "
                block_comment = False
                index += 2
            else:
                if char != "\n":
                    source[index] = calls[index] = " "
                index += 1
            continue
        if quote:
            if quote == "'" and char == "'" and text[index : index + 2] == "''":
                calls[index : index + 2] = "  "
                index += 2
                continue
            if char == "`" and quote == '"':
                calls[index : index + 2] = "  "
                index += 2
                continue
            if char == quote:
                quote = None
            if char != "\n":
                calls[index] = " "
            index += 1
            continue
        if char == "<" and text[index : index + 2] == "<#":
            source[index : index + 2] = calls[index : index + 2] = "  "
            block_comment = True
            index += 2
            continue
        if char == "#":
            while index < length and text[index] != "\n":
                source[index] = calls[index] = " "
                index += 1
            continue
        if char in {"'", '"'}:
            quote = char
            calls[index] = " "
        index += 1
    return "".join(source), "".join(calls)


def _call_arguments(text: str, open_paren: int) -> tuple[str | None, int]:
    depth = 1
    index = open_paren + 1
    quote: str | None = None
    block_comment = False
    while index < len(text):
        if block_comment:
            if text.startswith("#>", index):
                block_comment = False
                index += 2
            else:
                index += 1
            continue
        if quote:
            if quote == "'" and text[index : index + 2] == "''":
                index += 2
                continue
            if quote == '"' and text[index] == "`":
                index += 2
                continue
            if text[index] == quote:
                quote = None
            index += 1
            continue
        if text.startswith("<#", index):
            block_comment = True
            index += 2
            continue
        if text[index] == "#":
            newline = text.find("\n", index)
            index = len(text) if newline < 0 else newline + 1
            continue
        if text[index] in {"'", '"'}:
            quote = text[index]
        elif text[index] == "(":
            depth += 1
        elif text[index] == ")":
            depth -= 1
            if depth == 0:
                return text[open_paren + 1 : index], index + 1
        index += 1
    return None, len(text)


def _split_arguments(arguments: str) -> list[str]:
    parts: list[str] = []
    start = 0
    depths = {"(": 0, "[": 0, "{": 0}
    closing = {")": "(", "]": "[", "}": "{"}
    quote: str | None = None
    index = 0
    while index < len(arguments):
        char = arguments[index]
        if quote:
            if quote == "'" and arguments[index : index + 2] == "''":
                index += 2
                continue
            if quote == '"' and char == "`":
                index += 2
                continue
            if char == quote:
                quote = None
            index += 1
            continue
        if char in {"'", '"'}:
            quote = char
        elif char in depths:
            depths[char] += 1
        elif char in closing:
            opener = closing[char]
            depths[opener] = max(0, depths[opener] - 1)
        elif char == "," and not any(depths.values()):
            parts.append(arguments[start:index].strip())
            start = index + 1
        index += 1
    parts.append(arguments[start:].strip())
    return parts


# Directories a repo-wide `.ps1` sweep must skip: not tracked source, or
# tracked-but-irrelevant (a local/CI-built venv, not a script this repo wrote).
IGNORED_SCAN_DIR_NAMES = {
    ".git",
    ".venv",
    "venv",
    ".venv-tools",
    ".test-venvs",
    "node_modules",
}


def is_ignored_scan_path(path) -> bool:
    """True when any path component names a directory a repo-wide `.ps1`
    persistent-environment sweep must not descend into (see
    ``check-install-contract.py``'s repo-wide backstop)."""
    return any(part in IGNORED_SCAN_DIR_NAMES for part in path.parts)


def check_runtime_retention(runtime) -> None:
    """Exercise the canonical lifetime contract without an installed runtime."""
    with tempfile.TemporaryDirectory(prefix="runtime-retention-contract-") as temporary:
        root = Path(temporary)
        candidate = runtime.slot(root, "candidate")
        payload = candidate / "payload"
        payload.write_text("unchanged", encoding="utf-8")
        with runtime.construction_lease(root, "candidate", timeout=0):
            assert runtime.gc(root) == [], "GC removed a construction owner"
            assert runtime.toss_incomplete(root) == [], "toss removed a construction owner"
        with runtime.retention_lease(root, "candidate", timeout=0):
            assert runtime.gc(root) == [], "GC removed a retained candidate"
            assert runtime.toss_incomplete(root) == [], "toss removed a retained candidate"
            try:
                runtime.slot(root, "candidate", clean_incomplete=True)
            except RuntimeError:
                pass
            else:
                raise AssertionError("incomplete cleanup admitted a retained candidate")
            assert payload.read_text(encoding="utf-8") == "unchanged"
        assert runtime.toss_incomplete(root) == ["candidate"], "released candidate was not reclaimed"
        marker = runtime.mark_complete(root, "candidate", payload_hash="same", pid=11)
        before = marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns
        runtime.mark_complete(root, "candidate", payload_hash="same", pid=22)
        assert (marker.read_bytes(), marker.stat().st_ino, marker.stat().st_mtime_ns) == before, (
            "matching completion rewrote immutable evidence"
        )
        try:
            runtime.mark_complete(root, "candidate", payload_hash="different")
        except RuntimeError:
            pass
        else:
            raise AssertionError("numbered completion accepted conflicting content")
        assert marker.read_bytes() == before[0], "conflict changed immutable evidence"
        with runtime.retention_lease(root, "candidate", payload_hash="same", timeout=0):
            runtime.activate(root, "candidate")
        assert runtime.gc(root) == [], "GC removed the published candidate"


def persistent_environment_violations(text: str) -> list[str]:
    """Return direct persistent Windows environment access outside the adapter."""
    source, call_scan = _lexical_views(_without_canonical_adapter(text))
    violations: list[str] = []
    for match in _ENV_CALL.finditer(call_scan):
        arguments, _ = _call_arguments(source, match.end() - 1)
        line = source.count("\n", 0, match.start()) + 1
        if arguments is None:
            violations.append(f"line {line}: unterminated .NET environment call")
            continue
        parts = _split_arguments(arguments)
        required_process_arity = 1 if match.group("method").lower() == "get" else 2
        if len(parts) <= required_process_arity:
            continue
        target = parts[-1]
        if not _PROCESS_TARGET.fullmatch(target):
            violations.append(
                f"line {line}: direct .NET environment target {target!r}"
            )

    for match in _REGISTRY_ENVIRONMENT_PATH.finditer(source):
        line = source.count("\n", 0, match.start()) + 1
        violations.append(
            f"line {line}: direct persistent environment registry path"
        )
    for match in _REGISTRY_API_ENVIRONMENT.finditer(source):
        line = source.count("\n", 0, match.start()) + 1
        violations.append(
            f"line {line}: direct {match.group('hive')} environment registry API"
        )
    return violations
