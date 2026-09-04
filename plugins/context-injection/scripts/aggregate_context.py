#!/usr/bin/env python3
"""Aggregate declared session-start context from the active plugin stack."""

from __future__ import annotations

import argparse
import hashlib
import heapq
import json
import os
import re
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor, TimeoutError
from contextlib import contextmanager
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import BinaryIO

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))
import session_context_conformance as conformance

SCHEMA = "copilot-extensions.session-context-contributors"
MAX_INPUT_BYTES = 64 * 1024
MAX_AGGREGATE_BYTES = 128 * 1024
MAX_INLINE_CONTEXT_BYTES = 8 * 1024
AGGREGATE_HEADROOM_BYTES = 4 * 1024
MAX_CONTRIBUTORS = 128
MAX_TIMEOUT_SECONDS = 10
MAX_TOTAL_TIMEOUT_SECONDS = 20
RENDEZVOUS_DEADLINE_SECONDS = 25
FAST_REPLAY_TTL_SECONDS = 60
MAX_WORKERS = 16
PROCESS_START_GRACE_SECONDS = 5 if os.name == "nt" else 0
COMMAND_CATALOG_BUDGET_BYTES = 32 * 1024
MAX_STACK_FINGERPRINT_FILE_BYTES = 1024 * 1024
SPILL_INDEX_ENTRY_BYTES = 320
SPILL_INDEX_SUMMARY_BYTES = 96
SPILL_INDEX_REFERENCE_COUNT = 1
ADOPTION_SCHEMA = "copilot-extensions.context-injection"
ADOPTION_CONFIG = Path(".context-injection/config.yaml")
MAX_ADOPTION_CONFIG_BYTES = 4096
ENGINE_SCHEMA = "copilot-extensions.context-injection-engine"
ENGINE_VERSION = 5
ADOPTED_AUTHORITY_SOURCE = "context-injection@copilot-extensions"
IDENTIFIER = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$")
SESSION_IDENTIFIER = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]{0,127})$")


@dataclass(frozen=True)
class ActivePlugin:
    source: str
    name: str
    marketplace: str
    root: Path


@dataclass(frozen=True)
class Contributor:
    source: str
    plugin_root: Path
    contributor_id: str
    order: int
    timeout_seconds: int
    max_bytes: int
    command: tuple[str, ...]


@dataclass(frozen=True)
class ContributorResult:
    ok: bool
    context: str | None = None


@dataclass(frozen=True)
class Adoption:
    authority_source: str


@dataclass(frozen=True)
class StagedLaunch:
    plugin_roots: tuple[Path, ...]


def _diagnose(message: str) -> None:
    print(f"[context-injection] {message}", file=sys.stderr)


def _emit_empty(message: str | None = None) -> int:
    if message:
        _diagnose(message)
    print("{}", end="")
    return 0


def _bounded_text(value: str, max_bytes: int) -> str:
    encoded = value.encode("utf-8")
    if len(encoded) <= max_bytes:
        return value
    kept = encoded[: max(0, max_bytes - 3)].decode(
        "utf-8",
        errors="ignore",
    ).rstrip()
    return kept + "..."


def _fragment_identity(fragment: str, source: str) -> str:
    lines = fragment.splitlines()
    return (
        lines[0].strip()
        if lines and lines[0].startswith("[context-contributor: ")
        else f"[context-contributor: {source}/aggregate]"
    )


def _fragment_index_entry(fragment: str, source: str) -> str:
    lines = fragment.splitlines()
    identity = _fragment_identity(fragment, source)
    header = identity.removeprefix(
        "[context-contributor: "
    ).removesuffix("]")
    body_lines = [
        line.strip()
        for line in lines[1:]
        if line.strip()
        and not line.startswith("[owner:")
        and not line.startswith("[producer-owner:")
    ]
    raw_summary = (
        body_lines[0] if body_lines else "Complete owned context is deferred."
    )
    summary = _bounded_text(
        raw_summary,
        (
            SPILL_INDEX_ENTRY_BYTES
            if raw_summary.startswith("CodeSpace routes")
            else SPILL_INDEX_SUMMARY_BYTES
        ),
    )
    references = []
    explicit_references = []
    for line in lines[1:]:
        _marker, separator, reference = line.partition("Read:")
        if separator:
            explicit_references.append(reference.strip())
    path_references = re.findall(
        r"(?:[A-Za-z]:\\[^\r\n`]+|(?<![A-Za-z0-9])/[^\s`\r\n]+)",
        fragment,
    )
    quoted_references = re.findall(r"`([^`\r\n]+)`", fragment)
    for reference in [
        *explicit_references,
        *path_references,
        *quoted_references,
    ]:
        reference = reference.split("; ", 1)[0].rstrip(".,;:").strip("`")
        if reference not in references:
            references.append(reference)
        if len(references) >= SPILL_INDEX_REFERENCE_COUNT:
            break
    entry = f"- {header}"
    if references:
        entry += " refs=" + ", ".join(f"`{reference}`" for reference in references)
    remaining = SPILL_INDEX_ENTRY_BYTES - len(entry.encode("utf-8"))
    if (
        not references or summary.startswith("CodeSpace routes")
    ) and remaining > len(" summary=...".encode("utf-8")):
        entry += " summary=" + _bounded_text(
            summary,
            remaining - len(" summary=".encode("utf-8")),
        )
    return _bounded_text(entry, SPILL_INDEX_ENTRY_BYTES)


def _render_spill_kernel(
    pointer: str,
    fragments: list[tuple[int, str, str]],
) -> str:
    ordered = sorted(fragments)
    catalogs = [
        item
        for item in ordered
        for _, source, _fragment in (item,)
        if source == "command-catalogs"
    ]
    candidates = [
        item
        for item in ordered
        for _, source, _fragment in (item,)
        if source != "command-catalogs"
    ]
    intro = (
        f"{pointer}\n\n"
        "[context-injection] The bounded critical kernel below is authoritative "
        "for first-turn decisions. Read the complete spill before any action "
        "that requires deferred details."
    )

    selected: list[tuple[int, str, str]] = []

    def render(
        full_fragments: list[tuple[int, str, str]],
        deferred_fragments: list[tuple[int, str, str]],
        *,
        catalog_fragments: list[tuple[int, str, str]] | None = None,
        footer: str = "",
    ) -> str:
        sections = [intro]
        rendered_catalogs = catalogs if catalog_fragments is None else catalog_fragments
        if rendered_catalogs:
            sections.extend(
                (
                    "## Exact session command catalogs",
                    *(fragment for _, _, fragment in rendered_catalogs),
                )
            )
        if full_fragments:
            sections.extend(
                (
                    "## Critical contributor fragments",
                    *(fragment for _, _, fragment in full_fragments),
                )
            )
        if deferred_fragments:
            sections.extend(
                (
                    "## Deferred contributor index",
                    "\n".join(
                        _fragment_index_entry(fragment, source)
                        for _, source, fragment in deferred_fragments
                    ),
                )
            )
        if footer:
            sections.extend(("## Deferred roster continuation", footer))
        return "\n\n".join(sections)

    def render_fallback() -> str:
        fitting_catalogs: list[tuple[int, str, str]] = []
        selected_fragments: list[tuple[int, str, str]] = []
        all_items = [*catalogs, *candidates]

        def deferred_items(
            catalog_items: list[tuple[int, str, str]],
            full_items: list[tuple[int, str, str]],
        ) -> list[tuple[int, str, str]]:
            retained = [*catalog_items, *full_items]
            return [item for item in all_items if item not in retained]

        def roster_footer(
            deferred: list[tuple[int, str, str]],
            indexed_count: int,
        ) -> str:
            identities = "\n".join(
                _fragment_identity(fragment, source)
                for _, source, fragment in deferred
            )
            digest = hashlib.sha256(identities.encode("utf-8")).hexdigest()[:16]
            return (
                f"indexed={indexed_count}; total={len(deferred)}; "
                f"remaining={len(deferred) - indexed_count}; "
                f"roster-sha256={digest}. "
                "The complete attributable roster is in the spill."
            )

        admission_order = [
            *candidates[:1],
            *catalogs,
        ]
        for item in admission_order:
            if item[1] == "command-catalogs":
                proposed_catalogs = [*fitting_catalogs, item]
                proposed_fragments = selected_fragments
            else:
                proposed_catalogs = fitting_catalogs
                proposed_fragments = [*selected_fragments, item]
            deferred = deferred_items(proposed_catalogs, proposed_fragments)
            rendered = render(
                proposed_fragments,
                [],
                catalog_fragments=proposed_catalogs,
                footer=roster_footer(deferred, 0) if deferred else "",
            )
            if len(rendered.encode("utf-8")) <= MAX_INLINE_CONTEXT_BYTES:
                fitting_catalogs = proposed_catalogs
                selected_fragments = proposed_fragments

        if candidates and candidates[0] not in selected_fragments:
            return pointer

        deferred = deferred_items(fitting_catalogs, selected_fragments)
        indexed: list[tuple[int, str, str]] = []
        for fragment in deferred:
            proposed = [*indexed, fragment]
            remaining = len(deferred) - len(proposed)
            rendered = render(
                selected_fragments,
                proposed,
                catalog_fragments=fitting_catalogs,
                footer=roster_footer(deferred, len(proposed)) if remaining else "",
            )
            if len(rendered.encode("utf-8")) <= MAX_INLINE_CONTEXT_BYTES:
                indexed = proposed
        remaining = len(deferred) - len(indexed)
        return render(
            selected_fragments,
            indexed,
            catalog_fragments=fitting_catalogs,
            footer=roster_footer(deferred, len(indexed)) if remaining else "",
        )

    base = render(selected, candidates)
    if len(base.encode("utf-8")) > MAX_INLINE_CONTEXT_BYTES:
        return render_fallback()

    for fragment in candidates:
        proposed = [*selected, fragment]
        deferred = [
            candidate
            for candidate in candidates
            if candidate not in proposed
        ]
        rendered = render(proposed, deferred)
        if len(rendered.encode("utf-8")) <= MAX_INLINE_CONTEXT_BYTES:
            selected = proposed
    if candidates and candidates[0] not in selected:
        return render_fallback()
    return render(
        selected,
        [fragment for fragment in candidates if fragment not in selected],
    )


def _spill_context(
    session_id: str,
    canonical_cwd: str,
    context: str,
    fragments: list[tuple[int, str, str]] | None = None,
) -> str | None:
    if not SESSION_IDENTIFIER.fullmatch(session_id):
        return None
    cwd_digest = hashlib.sha256(canonical_cwd.encode("utf-8")).hexdigest()[:24]
    try:
        state_root = (
            Path.home() / ".copilot" / "session-state"
        ).resolve()
        session_root = (state_root / session_id).resolve()
        session_root.relative_to(state_root)
        files = session_root / "files"
        files.mkdir(parents=True, exist_ok=True)
        if files.is_symlink():
            return None
        target = files / f"startup-context-{cwd_digest}.md"
        content = (
            "# Aggregated startup context\n\n"
            f"{context}\n"
        )
        with tempfile.NamedTemporaryFile(
            mode="w",
            encoding="utf-8",
            newline="\n",
            dir=files,
            prefix=f".startup-context.{cwd_digest}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            temporary = Path(handle.name)
            handle.write(content)
        if os.name != "nt":
            temporary.chmod(0o600)
        os.replace(temporary, target)
        if os.name != "nt":
            target.chmod(0o600)
    except (OSError, ValueError):
        return None
    pointer = (
        "[context-injection] delivery=spill; complete-context=true; "
        f"path=`{target}`. Before acting beyond the bounded kernel, read that "
        "complete startup context. It contains authoritative policy, routing, "
        "readiness, and exact command catalogs. Until loaded, preserve worktree "
        "boundaries and do not publish or mutate external state."
    )
    if fragments is None:
        return pointer
    kernel = _render_spill_kernel(pointer, fragments)
    if len(kernel.encode("utf-8")) > MAX_INLINE_CONTEXT_BYTES:
        return pointer
    return kernel


@lru_cache(maxsize=512)
def _load_json(path: Path) -> dict | None:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    return value if isinstance(value, dict) else None


def _load_adoption_yaml(path: Path) -> dict | None:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(content.encode("utf-8")) > MAX_ADOPTION_CONFIG_BYTES:
        return None

    parsed: dict[str, object] = {}
    parent: str | None = None
    for line in content.splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if "\t" in line:
            return None
        indent = len(line) - len(line.lstrip(" "))
        if indent not in {0, 2}:
            return None
        match = re.fullmatch(r"([a-z][a-zA-Z0-9]*):(.*)", line[indent:])
        if match is None:
            return None
        key, suffix = match.groups()
        if suffix and not suffix.startswith(" "):
            return None
        raw_value = suffix.strip()
        if indent == 0:
            if key in parsed:
                return None
            if not raw_value:
                parsed[key] = {}
                parent = key
                continue
            parent = None
            target = parsed
        else:
            if parent is None or not isinstance(parsed.get(parent), dict):
                return None
            target = parsed[parent]
            if key in target:
                return None
        if not raw_value or raw_value[0] in "\"'[{&*!|>":
            return None
        value: object = int(raw_value) if raw_value.isascii() and raw_value.isdigit() else raw_value
        target[key] = value
    return parsed


def _repo_root(cwd: Path) -> Path:
    current = cwd.resolve()
    for candidate in (current, *current.parents):
        if (candidate / ".git").exists():
            return candidate
    return current


def _jsonc(path: Path) -> dict | None:
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return None
    body = "\n".join(
        line for line in lines if not line.lstrip().startswith("//")
    )
    try:
        value = json.loads(body)
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _repo_is_trusted(repo: Path) -> bool:
    config = Path.home() / ".copilot" / "config.json"
    value = _jsonc(config)
    if value is None:
        return False
    folders = value.get("trustedFolders")
    if not isinstance(folders, list):
        return False
    resolved = repo.resolve()
    for raw in folders:
        if not isinstance(raw, str):
            continue
        try:
            trusted = Path(raw).expanduser().resolve(strict=True)
            if resolved == trusted:
                return True
        except OSError:
            continue
    return False


def _clean_git_env() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment["GIT_CONFIG_NOSYSTEM"] = "1"
    environment["GIT_CONFIG_GLOBAL"] = os.devnull
    return environment


def _git_path(repo: Path, argument: str) -> Path | None:
    git = shutil.which("git")
    if git is None:
        return None
    try:
        result = subprocess.run(
            [
                git,
                "-C",
                str(repo),
                "rev-parse",
                "--path-format=absolute",
                argument,
            ],
            capture_output=True,
            text=True,
            timeout=5,
            check=True,
            env=_clean_git_env(),
            stdin=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        return Path(result.stdout.strip()).resolve(strict=True)
    except (OSError, subprocess.SubprocessError):
        return None


def _trusted_linked_worktree_anchor(repo: Path) -> Path | None:
    common_dir = _git_path(repo, "--git-common-dir")
    if common_dir is None or not common_dir.is_dir():
        return None
    anchor = common_dir.parent
    if anchor == repo.resolve() or not _repo_is_trusted(anchor):
        return None
    if (
        _git_path(anchor, "--show-toplevel") != anchor
        or _git_path(anchor, "--git-common-dir") != common_dir
    ):
        return None
    return anchor


def _windows_command_line_argv(command_line: str) -> tuple[str, ...] | None:
    if os.name != "nt":
        return None
    import ctypes

    argument_count = ctypes.c_int()
    command_line_to_argv = ctypes.windll.shell32.CommandLineToArgvW
    command_line_to_argv.argtypes = [
        ctypes.c_wchar_p,
        ctypes.POINTER(ctypes.c_int),
    ]
    command_line_to_argv.restype = ctypes.POINTER(ctypes.c_wchar_p)
    arguments = command_line_to_argv(
        command_line,
        ctypes.byref(argument_count),
    )
    if not arguments:
        return None
    try:
        return tuple(arguments[index] for index in range(argument_count.value))
    finally:
        ctypes.windll.kernel32.LocalFree(ctypes.cast(arguments, ctypes.c_void_p))


def _process_ancestry_argv() -> list[tuple[str, ...]] | None:
    """Read ancestor argv without evaluating any ancestor command line."""
    if (
        os.environ.get("COPILOT_CONTEXT_INJECTION_TEST_NO_STAGED_PLUGINS") == "1"
        and "PYTEST_CURRENT_TEST" in os.environ
    ):
        return []
    injected = os.environ.get("COPILOT_CONTEXT_INJECTION_TEST_ANCESTRY")
    if injected is not None and "PYTEST_CURRENT_TEST" in os.environ:
        try:
            value = json.loads(injected)
        except json.JSONDecodeError:
            return None
        if not isinstance(value, list) or not all(
            isinstance(command, list)
            and command
            and all(isinstance(argument, str) for argument in command)
            for command in value
        ):
            return None
        return [tuple(command) for command in value]
    if os.name == "nt":
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not powershell:
            return None
        script = r"""
$result = @()
$processes = @(Get-CimInstance Win32_Process -ErrorAction Stop)
$byId = @{}
foreach ($process in $processes) {
    $byId[[int]$process.ProcessId] = $process
}
$current = [int]$env:COPILOT_CONTEXT_INJECTION_ANCESTRY_PID
while ($current -gt 0) {
    if (-not $byId.ContainsKey($current)) {
        throw "process ancestry is incomplete at PID $current"
    }
    $process = $byId[$current]
    if ($process.CommandLine) {
        $result += [pscustomobject]@{
            processId = [int]$process.ProcessId
            parentProcessId = [int]$process.ParentProcessId
            commandLine = [string]$process.CommandLine
        }
        if (
            [string]$process.Name -ieq 'copilot.exe' -or
            [string]$process.Name -ieq 'copilot' -or
            [string]$process.CommandLine -match '(^|\s)--acp(\s|$)'
        ) {
            break
        }
    }
    if ([int]$process.ParentProcessId -eq $current) {
        throw "process ancestry contains a cycle at PID $current"
    }
    $current = [int]$process.ParentProcessId
}
[Console]::Out.Write((ConvertTo-Json -InputObject @($result) -Compress))
"""
        try:
            environment = os.environ.copy()
            environment["COPILOT_CONTEXT_INJECTION_ANCESTRY_PID"] = str(
                os.getppid()
            )
            result = subprocess.run(
                [
                    powershell,
                    "-NoProfile",
                    "-Command",
                    script,
                ],
                capture_output=True,
                text=True,
                timeout=3,
                check=True,
                env=environment,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        try:
            records = json.loads(result.stdout)
        except json.JSONDecodeError:
            return None
        if isinstance(records, dict):
            records = [records]
        if not isinstance(records, list):
            return None
        ancestry: list[tuple[str, ...]] = []
        for record in records:
            if not isinstance(record, dict):
                return None
            command_line = record.get("commandLine")
            if not isinstance(command_line, str):
                return None
            arguments = _windows_command_line_argv(command_line)
            if not arguments:
                return None
            ancestry.append(arguments)
        return ancestry
    pid = os.getppid()
    seen: set[int] = set()
    ancestry = []
    while pid > 1 and pid not in seen:
        seen.add(pid)
        try:
            raw_command = Path(f"/proc/{pid}/cmdline").read_bytes()
            status = Path(f"/proc/{pid}/status").read_text(encoding="utf-8")
        except OSError:
            return None
        arguments = tuple(
            argument.decode("utf-8", errors="surrogateescape")
            for argument in raw_command.split(b"\0")
            if argument
        )
        if not arguments:
            return None
        ancestry.append(arguments)
        parent = re.search(r"^PPid:\s+(\d+)$", status, re.MULTILINE)
        if parent is None:
            return None
        pid = int(parent.group(1))
    return ancestry


def _plugin_dir_arguments(arguments: tuple[str, ...]) -> tuple[str, ...] | None:
    plugin_dirs: list[str] = []
    index = 0
    while index < len(arguments):
        argument = arguments[index]
        if argument == "--plugin-dir":
            index += 1
            if (
                index >= len(arguments)
                or not arguments[index]
                or arguments[index].startswith("--")
            ):
                return None
            plugin_dirs.append(arguments[index])
        elif argument.startswith("--plugin-dir="):
            value = argument.partition("=")[2]
            if not value:
                return None
            plugin_dirs.append(value)
        elif argument.startswith("--plugin-dir"):
            return None
        index += 1
    return tuple(plugin_dirs)


def _staged_launch() -> StagedLaunch | None:
    ancestry = _process_ancestry_argv()
    if ancestry is None:
        _diagnose("process ancestry is unavailable")
        return None
    acp_commands = [
        arguments for arguments in ancestry if "--acp" in arguments
    ]
    if not acp_commands:
        if any(
            "--plugin-dir" in argument
            for arguments in ancestry
            for argument in arguments
        ):
            _diagnose("staged plugin arguments are not available as raw ACP argv")
            return None
        return StagedLaunch(())
    extracted = [_plugin_dir_arguments(arguments) for arguments in acp_commands]
    if any(plugin_dirs is None for plugin_dirs in extracted):
        _diagnose("staged plugin arguments are malformed")
        return None
    assert all(plugin_dirs is not None for plugin_dirs in extracted)
    if any(plugin_dirs != extracted[0] for plugin_dirs in extracted[1:]):
        _diagnose("ACP process ancestry has conflicting staged plugin arguments")
        return None
    canonical: list[Path] = []
    seen: set[str] = set()
    for raw_root in extracted[0] or ():
        configured = Path(raw_root).expanduser()
        if not configured.is_absolute():
            _diagnose("staged plugin root is not absolute")
            return None
        try:
            root = configured.resolve(strict=True)
        except OSError:
            _diagnose("staged plugin root is unavailable")
            return None
        if not root.is_dir():
            _diagnose("staged plugin root is not a directory")
            return None
        identity = os.path.normcase(str(root))
        if identity in seen:
            continue
        seen.add(identity)
        canonical.append(root)
    return StagedLaunch(tuple(canonical))


def _settings(repo: Path) -> tuple[dict[str, bool], dict[str, tuple[dict, Path]]] | None:
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, tuple[dict, Path]] = {}
    layers = [(Path.home() / ".copilot" / "settings.json", Path.home())]
    if _repo_is_trusted(repo):
        anchor = _trusted_linked_worktree_anchor(repo)
        layers.append((repo / ".claude" / "settings.json", repo))
        if anchor is not None:
            layers.append((anchor / ".claude" / "settings.local.json", anchor))
        layers.extend(
            [
                (repo / ".claude" / "settings.local.json", repo),
                (repo / ".github" / "copilot" / "settings.json", repo),
            ]
        )
        if anchor is not None:
            layers.append(
                (anchor / ".github" / "copilot" / "settings.local.json", anchor)
            )
        layers.append(
            (repo / ".github" / "copilot" / "settings.local.json", repo)
        )
    for path, base in layers:
        if not path.exists():
            continue
        value = _load_json(path)
        if value is None:
            _diagnose(f"settings are unreadable or malformed: {path}")
            return None
        raw_enabled = value.get("enabledPlugins")
        if isinstance(raw_enabled, dict):
            for key, active in raw_enabled.items():
                if isinstance(key, str) and isinstance(active, bool):
                    enabled[key] = active
        raw_marketplaces = value.get("extraKnownMarketplaces")
        if isinstance(raw_marketplaces, dict):
            for key, declaration in raw_marketplaces.items():
                if isinstance(key, str) and isinstance(declaration, dict):
                    marketplaces[key] = (declaration, base)
    return enabled, marketplaces


def _repository_adoption(repo: Path) -> Adoption | None:
    if not _repo_is_trusted(repo):
        _diagnose("repository context aggregation is not trusted")
        return None
    try:
        config_path = (repo / ADOPTION_CONFIG).resolve(strict=True)
        config_path.relative_to(repo.resolve())
    except (OSError, ValueError):
        _diagnose("repository context-injection config is missing or escapes the repository")
        return None
    configured = _load_adoption_yaml(config_path)
    if configured is None:
        _diagnose("repository context-injection config is malformed")
        return None
    if (
        set(configured) != {"schema", "version", "authority", "engine"}
        or configured.get("schema") != ADOPTION_SCHEMA
        or configured.get("version") != 1
        or configured.get("authority") != ADOPTED_AUTHORITY_SOURCE
        or not isinstance(configured.get("engine"), dict)
        or set(configured["engine"]) != {"schema", "version"}
        or configured["engine"].get("schema") != ENGINE_SCHEMA
        or configured["engine"].get("version") != ENGINE_VERSION
    ):
        _diagnose("repository context-injection config is incomplete or incompatible")
        return None
    return Adoption(authority_source=configured["authority"])


def _marketplace_manifest(root: Path) -> tuple[dict, Path] | None:
    for relative in (
        Path(".github/plugin/marketplace.json"),
        Path(".claude-plugin/marketplace.json"),
    ):
        path = root / relative
        value = _load_json(path)
        if value is not None:
            return value, path.parent.parent.parent if relative.parts[0] == ".github" else path.parent.parent
    return None


def _directory_plugin(
    repo: Path,
    marketplace: str,
    name: str,
    declaration: dict,
    base: Path,
) -> Path | None:
    source = declaration.get("source")
    if not isinstance(source, dict):
        return None
    if str(source.get("source", "")).strip().lower() != "directory":
        return None
    raw_path = source.get("path")
    if not isinstance(raw_path, str) or not raw_path.strip():
        return None
    configured = Path(raw_path).expanduser()
    root = configured if configured.is_absolute() else base / configured
    try:
        root = root.resolve(strict=True)
    except OSError:
        return None
    loaded = _marketplace_manifest(root)
    if loaded is None:
        return None
    manifest, manifest_root = loaded
    if manifest.get("name") != marketplace:
        return None
    metadata = manifest.get("metadata")
    plugin_root = manifest_root
    if isinstance(metadata, dict):
        raw_plugin_root = metadata.get("pluginRoot")
        if isinstance(raw_plugin_root, str) and raw_plugin_root.strip():
            plugin_root = manifest_root / raw_plugin_root
    try:
        plugin_root = plugin_root.resolve(strict=True)
        plugin_root.relative_to(root)
    except (OSError, ValueError):
        return None
    entries = manifest.get("plugins")
    if not isinstance(entries, list):
        return None
    matches = [entry for entry in entries if isinstance(entry, dict) and entry.get("name") == name]
    if len(matches) != 1 or not isinstance(matches[0].get("source"), str):
        return None
    try:
        candidate = (plugin_root / matches[0]["source"]).resolve(strict=True)
        candidate.relative_to(plugin_root)
    except (OSError, ValueError):
        return None
    plugin_manifest = _load_json(candidate / "plugin.json")
    if plugin_manifest is None:
        plugin_manifest = _load_json(candidate / ".claude-plugin" / "plugin.json")
    return candidate if plugin_manifest and plugin_manifest.get("name") == name else None


def _plugin_identity(source: str) -> tuple[str, str] | None:
    name, separator, marketplace = source.partition("@")
    name = name.strip()
    marketplace = marketplace.strip()
    if (
        not separator
        or not IDENTIFIER.fullmatch(name)
        or not IDENTIFIER.fullmatch(marketplace)
    ):
        return None
    return name, marketplace


def _staged_manifest(root: Path) -> dict | None:
    for relative in (Path("plugin.json"), Path(".claude-plugin/plugin.json")):
        try:
            path = (root / relative).resolve(strict=True)
            path.relative_to(root)
        except OSError:
            continue
        except ValueError:
            _diagnose("staged plugin manifest escapes its payload root")
            return None
        if path.is_file():
            manifest = _load_json(path)
            if manifest is None:
                _diagnose("staged plugin manifest is malformed")
            return manifest
    _diagnose("staged plugin root has no manifest")
    return None


def _staged_active_plugins(
    enabled: dict[str, bool],
    roots: tuple[Path, ...],
) -> list[ActivePlugin] | None:
    identities: dict[str, list[tuple[str, str, bool]]] = {}
    for source, active in enabled.items():
        parsed = _plugin_identity(source)
        if parsed is None:
            _diagnose(f"invalid enabled plugin identity: {source!r}")
            return None
        name, marketplace = parsed
        identities.setdefault(name, []).append((source, marketplace, active))

    staged: list[ActivePlugin] = []
    source_roots: dict[str, Path] = {}
    for root in roots:
        manifest = _staged_manifest(root)
        name = manifest.get("name") if manifest else None
        if not isinstance(name, str) or not IDENTIFIER.fullmatch(name):
            _diagnose("staged plugin manifest has an invalid name")
            return None
        matches = identities.get(name, [])
        active_matches = [match for match in matches if match[2]]
        if len(active_matches) > 1 or (
            not active_matches and len(matches) != 1
        ):
            _diagnose(f"staged plugin source is missing or ambiguous: {name}")
            return None
        if not active_matches:
            continue
        source, marketplace, _ = active_matches[0]
        previous = source_roots.get(source)
        if previous is not None and previous != root:
            _diagnose(f"duplicate staged plugin identity: {source}")
            return None
        source_roots[source] = root
        staged.append(ActivePlugin(source, name, marketplace, root))
    return sorted(staged, key=lambda plugin: plugin.source)


def _active_plugins(
    repo: Path,
    staged_roots: tuple[Path, ...] | None = None,
    *,
    violations: list[conformance.Violation] | None = None,
) -> list[ActivePlugin] | None:
    loaded = _settings(repo)
    if loaded is None:
        return None
    enabled, marketplaces = loaded
    if staged_roots is not None:
        return _staged_active_plugins(enabled, staged_roots)
    active: list[ActivePlugin] = []
    installed = (Path.home() / ".copilot" / "installed-plugins").resolve()
    failed = False
    for source in sorted(key for key, value in enabled.items() if value):
        parsed = _plugin_identity(source)
        if parsed is None:
            message = f"invalid enabled plugin identity: {source!r}"
            if violations is not None:
                violations.append(
                    conformance.Violation(
                        "active-plugin-identity-invalid",
                        message,
                        source=source,
                    )
                )
            else:
                _diagnose(message)
            failed = True
            continue
        name, marketplace = parsed
        declaration, base = marketplaces.get(marketplace, ({}, repo))
        root = _directory_plugin(repo, marketplace, name, declaration, base)
        if root is None:
            root = installed / marketplace / name
            try:
                root = root.resolve(strict=True)
                root.relative_to(installed)
            except OSError:
                message = "active plugin payload is unavailable"
                if violations is not None:
                    violations.append(
                        conformance.Violation(
                            "plugin-payload-missing",
                            message,
                            source=source,
                            path=str(root),
                        )
                    )
                else:
                    _diagnose(f"{message}: {source}")
                failed = True
                continue
            except ValueError:
                message = "active plugin payload escapes installed root"
                if violations is not None:
                    violations.append(
                        conformance.Violation(
                            "plugin-payload-escape",
                            message,
                            source=source,
                            path=str(root),
                        )
                    )
                else:
                    _diagnose(f"{message}: {source}")
                failed = True
                continue
        manifest = _load_json(root / "plugin.json")
        if manifest is None:
            manifest = _load_json(root / ".claude-plugin" / "plugin.json")
        if manifest is None or manifest.get("name") != name:
            message = "active plugin manifest identity is invalid"
            if violations is not None:
                violations.append(
                    conformance.Violation(
                        "plugin-identity-drift",
                        message,
                        source=source,
                        path=str(root),
                    )
                )
            else:
                _diagnose(f"{message}: {source}")
            failed = True
            continue
        active.append(
            ActivePlugin(source, name, marketplace, root)
        )
    return None if failed and violations is None else active


def _session_start_hooks(plugin: ActivePlugin, manifest: dict) -> bool | None:
    configured = manifest.get("hooks")
    candidates: list[Path] = []
    if isinstance(configured, str):
        candidates.append(plugin.root / configured)
    elif isinstance(configured, list):
        candidates.extend(plugin.root / item for item in configured if isinstance(item, str))
    else:
        candidates.extend((plugin.root / "hooks.json", plugin.root / "hooks" / "hooks.json"))
    found = False
    for candidate in candidates:
        try:
            resolved = candidate.resolve()
            resolved.relative_to(plugin.root)
        except (OSError, ValueError):
            return None
        if not resolved.is_file():
            continue
        found = True
        hooks = _load_json(resolved)
        if hooks is None:
            return None
        events = hooks.get("hooks")
        if not isinstance(events, dict):
            return None
        entries = events.get("sessionStart", events.get("SessionStart", []))
        if not isinstance(entries, list):
            return None
        if entries:
            return True
    return False if found or not configured else None


def _manifest(plugin: ActivePlugin) -> dict | None:
    value = _load_json(plugin.root / "plugin.json")
    return value or _load_json(plugin.root / ".claude-plugin" / "plugin.json")


def _declares_aggregate_authority(plugin: ActivePlugin) -> bool:
    manifest = _manifest(plugin)
    configured = manifest.get("sessionContext") if manifest else None
    if not isinstance(configured, str) or not configured.strip():
        return False
    try:
        path = (plugin.root / configured).resolve(strict=True)
        path.relative_to(plugin.root)
    except (OSError, ValueError):
        return False
    declaration = _load_json(path)
    behavior = declaration.get("sessionStart") if declaration else None
    return (
        declaration is not None
        and declaration.get("schema") == SCHEMA
        and declaration.get("version") == 1
        and declaration.get("complete") is True
        and isinstance(behavior, dict)
        and behavior.get("context") == "aggregate-authority"
    )


def _contributors(
    active: list[ActivePlugin],
    adoption: Adoption | None = None,
    *,
    enforce_admission: bool = True,
) -> list[Contributor] | None:
    if adoption is not None:
        authority = next(
            (
                plugin
                for plugin in active
                if plugin.source == adoption.authority_source
            ),
            None,
        )
        report = conformance.scan_plugins(
            [
                conformance.PluginTarget(plugin.source, plugin.root)
                for plugin in active
            ],
            scope="effective-stack",
            authority_source=adoption.authority_source,
            wrapper_root=authority.root if authority else None,
            authority_engine_schema=ENGINE_SCHEMA,
            authority_engine_version=ENGINE_VERSION,
            authority_timeout_seconds=RENDEZVOUS_DEADLINE_SECONDS,
        )
        if not report.ok:
            for violation in report.violations:
                _diagnose(
                    f"{violation.code}: "
                    f"{violation.source or '<stack>'}: {violation.message}"
                )
            return None
    contributors: list[Contributor] = []
    platform_key = "powershell" if os.name == "nt" else "bash"
    for plugin in active:
        plugin_manifest = _manifest(plugin)
        if plugin_manifest is None:
            return None
        has_hooks = _session_start_hooks(plugin, plugin_manifest)
        if has_hooks is None:
            _diagnose(f"cannot inspect session-start hooks for {plugin.source}")
            return None
        declaration_path = plugin_manifest.get("sessionContext")
        if not isinstance(declaration_path, str) or not declaration_path.strip():
            if has_hooks:
                _diagnose(f"session-start plugin has no complete context declaration: {plugin.source}")
                return None
            continue
        try:
            path = (plugin.root / declaration_path).resolve(strict=True)
            path.relative_to(plugin.root)
        except (OSError, ValueError):
            _diagnose(f"context declaration escapes or is missing: {plugin.source}")
            return None
        declaration = _load_json(path)
        if (
            declaration is None
            or declaration.get("schema") != SCHEMA
            or declaration.get("version") != 1
            or declaration.get("complete") is not True
        ):
            _diagnose(f"context declaration is incomplete or incompatible: {plugin.source}")
            return None
        raw_contributors = declaration.get("contributors")
        if not isinstance(raw_contributors, list):
            return None
        if adoption is not None:
            behavior = declaration.get("sessionStart")
            if (
                not isinstance(behavior, dict)
                or set(behavior) != {"sideEffects", "context"}
                or behavior.get("sideEffects")
                not in {"none", "restart-safe-idempotent"}
                or behavior.get("context")
                not in {"none", "authority-aware", "aggregate-authority"}
            ):
                _diagnose(
                    f"session-start behavior is incomplete: {plugin.source}"
                )
                return None
            side_effects = behavior["sideEffects"]
            context_behavior = behavior["context"]
            if plugin.source == adoption.authority_source:
                valid_behavior = (
                    has_hooks
                    and side_effects == "none"
                    and context_behavior == "aggregate-authority"
                    and not raw_contributors
                )
            elif has_hooks and raw_contributors:
                valid_behavior = context_behavior == "authority-aware"
            elif has_hooks:
                valid_behavior = (
                    side_effects == "restart-safe-idempotent"
                    and context_behavior == "none"
                )
            else:
                valid_behavior = not raw_contributors
            if not valid_behavior:
                _diagnose(
                    f"session-start behavior is incompatible with adoption: "
                    f"{plugin.source}"
                )
                return None
        seen: set[str] = set()
        for raw in raw_contributors:
            if not isinstance(raw, dict):
                return None
            contributor_id = raw.get("id")
            command = raw.get(platform_key)
            if (
                not isinstance(contributor_id, str)
                or not IDENTIFIER.fullmatch(contributor_id)
                or contributor_id in seen
                or raw.get("pure") is not True
                or not isinstance(command, list)
                or not command
                or not all(isinstance(part, str) and part for part in command)
            ):
                return None
            seen.add(contributor_id)
            order = raw.get("order", 500)
            timeout = raw.get("timeoutSeconds", 5)
            max_bytes = raw.get("maxBytes", 8192)
            if (
                not isinstance(order, int)
                or not isinstance(timeout, int)
                or not 1 <= timeout <= MAX_TIMEOUT_SECONDS
                or not isinstance(max_bytes, int)
                or not 1 <= max_bytes <= MAX_AGGREGATE_BYTES
            ):
                return None
            contributors.append(
                Contributor(
                    plugin.source,
                    plugin.root,
                    contributor_id,
                    order,
                    timeout,
                    max_bytes,
                    tuple(command),
                )
            )
            if len(contributors) > MAX_CONTRIBUTORS:
                return None
    ordered = sorted(
        contributors, key=lambda item: (item.order, item.source, item.contributor_id)
    )
    if not enforce_admission:
        return ordered
    catalog_count = sum(
        item.contributor_id == "command-catalog" for item in ordered
    )
    declared_bytes = sum(
        item.max_bytes
        + len(
            (
                f"[context-contributor: "
                f"{item.source}/{item.contributor_id}]\n\n"
            ).encode("utf-8")
        )
        for item in ordered
        if item.contributor_id != "command-catalog"
    )
    if catalog_count:
        declared_bytes += COMMAND_CATALOG_BUDGET_BYTES
    schedule: list[int] = [0] * min(MAX_WORKERS, max(1, len(ordered)))
    heapq.heapify(schedule)
    for item in sorted(ordered, key=lambda value: value.timeout_seconds, reverse=True):
        elapsed = heapq.heappop(schedule)
        heapq.heappush(schedule, elapsed + item.timeout_seconds)
    declared_time = max(schedule)
    if declared_bytes > MAX_AGGREGATE_BYTES - AGGREGATE_HEADROOM_BYTES:
        _diagnose("declared contributor bytes exceed aggregate admission budget")
        return None
    if declared_time > MAX_TOTAL_TIMEOUT_SECONDS - 2:
        _diagnose("declared contributor time exceeds aggregate admission budget")
        return None
    return ordered


def _command(contributor: Contributor) -> list[str] | None:
    first, *rest = contributor.command
    try:
        script = (contributor.plugin_root / first).resolve(strict=True)
        script.relative_to(contributor.plugin_root)
    except (OSError, ValueError):
        return None
    suffix = script.suffix.lower()
    if os.name == "nt":
        if suffix != ".ps1":
            return None
        powershell = shutil.which("pwsh") or shutil.which("powershell.exe")
        if not powershell:
            return None
        return [powershell, "-NoProfile", "-File", str(script), *rest]
    if suffix != ".sh":
        return None
    return ["bash", str(script), *rest]


def _run(
    contributor: Contributor,
    hook_input: bytes | BinaryIO,
    session_cwd: Path,
) -> ContributorResult:
    command = _command(contributor)
    if command is None:
        _diagnose(f"invalid contributor command: {contributor.source}/{contributor.contributor_id}")
        return ContributorResult(False)
    environment = os.environ.copy()
    for variable in (
        "COPILOT_PLUGIN_DATA",
        "CLAUDE_PLUGIN_DATA",
        "PLUGIN_DATA",
    ):
        environment.pop(variable, None)
    root = str(contributor.plugin_root)
    environment.update(
        {
            "COPILOT_PLUGIN_ROOT": root,
            "PLUGIN_ROOT": root,
            "CLAUDE_PLUGIN_ROOT": root,
        }
    )
    with tempfile.TemporaryFile() as stdout_file, tempfile.TemporaryFile() as stderr_file:
        input_stream = None
        input_bytes = hook_input
        if not isinstance(hook_input, bytes):
            hook_input.seek(0)
            input_stream = hook_input
            input_bytes = None
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.PIPE if input_stream is None else input_stream,
                stdout=stdout_file,
                stderr=stderr_file,
                env=environment,
                cwd=session_cwd,
                start_new_session=os.name != "nt",
            )
            process.communicate(
                input=input_bytes,
                timeout=(
                    contributor.timeout_seconds
                    + PROCESS_START_GRACE_SECONDS
                ),
            )
        except (OSError, subprocess.TimeoutExpired):
            if "process" in locals() and process.poll() is None:
                try:
                    if os.name == "nt":
                        process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                except OSError:
                    pass
                process.wait()
            _diagnose(
                f"contributor failed: "
                f"{contributor.source}/{contributor.contributor_id}"
            )
            return ContributorResult(False)
        stdout_size = stdout_file.tell()
        stderr_size = stderr_file.tell()
        if (
            process.returncode != 0
            or stdout_size > contributor.max_bytes
            or stderr_size > contributor.max_bytes
        ):
            _diagnose(
                f"contributor rejected: "
                f"{contributor.source}/{contributor.contributor_id}"
            )
            return ContributorResult(False)
        stdout_file.seek(0)
        stdout = stdout_file.read()
    try:
        payload = json.loads(stdout.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        _diagnose(f"contributor emitted invalid JSON: {contributor.source}/{contributor.contributor_id}")
        return ContributorResult(False)
    if payload == {}:
        return ContributorResult(True)
    if not isinstance(payload, dict) or set(payload) != {"additionalContext"}:
        return ContributorResult(False)
    context = payload["additionalContext"]
    if not isinstance(context, str) or not context.strip():
        return ContributorResult(False)
    if "[context-contributor:" in context:
        _diagnose(
            f"contributor forged a reserved marker: "
            f"{contributor.source}/{contributor.contributor_id}"
        )
        return ContributorResult(False)
    return ContributorResult(True, context.strip())


def _command_catalog(
    contributor: Contributor,
    context: str,
) -> dict | None:
    marker = "```json\n"
    if marker not in context:
        return None
    raw = context.split(marker, 1)[1].split("\n```", 1)[0]
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema") != "copilot-extensions.session-command-catalog"
        or payload.get("version") != 1
        or not isinstance(payload.get("plugin"), str)
        or payload["plugin"] != contributor.source.partition("@")[0]
        or not isinstance(payload.get("commands"), list)
        or not isinstance(payload.get("payload"), dict)
    ):
        return None
    return {
        "source": contributor.source,
        "plugin": payload["plugin"],
        "payload": payload["payload"],
        "commands": payload["commands"],
    }


def _catalog_fragment(catalogs: list[dict]) -> str:
    payload = {
        "schema": "copilot-extensions.session-command-catalog.aggregate",
        "version": 1,
        "catalogs": catalogs,
    }
    return (
        "## session command catalog\n\n"
        "Invoke each exact `argv`; do not search `PATH` or substitute a "
        "same-named command.\n\n"
        "```json\n"
        f"{json.dumps(payload, ensure_ascii=False, separators=(',', ':'))}\n"
        "```"
    )


def _prove_authority(
    active: list[ActivePlugin],
    adoption: Adoption,
    engine_root: Path,
    caller_root: Path,
    producer_source: str | None,
) -> bool:
    declared_authorities = [
        plugin for plugin in active if _declares_aggregate_authority(plugin)
    ]
    if (
        len(declared_authorities) != 1
        or declared_authorities[0].source != adoption.authority_source
    ):
        _diagnose("configured context authority is missing or ambiguous")
        return False
    authority = [
        plugin for plugin in active if plugin.source == adoption.authority_source
    ]
    if len(authority) != 1:
        _diagnose("configured context authority is not exactly active")
        return False
    if authority[0].root != engine_root:
        _diagnose(
            "running context engine does not match the configured authority payload"
        )
        return False
    if (
        _contributors(
            [authority[0]],
            adoption,
            enforce_admission=False,
        )
        is None
    ):
        _diagnose("configured context authority behavior is incomplete")
        return False
    override = os.environ.get("COPILOT_CONTEXT_INJECTION_AUTHORITY")
    if override is not None and override != adoption.authority_source:
        _diagnose("authority override does not match repository adoption")
        return False
    if producer_source is None:
        if caller_root != authority[0].root:
            _diagnose("aggregate emission did not originate from the authority")
            return False
    else:
        producer = [plugin for plugin in active if plugin.source == producer_source]
        if len(producer) != 1 or producer[0].root != caller_root:
            _diagnose("producer caller does not match its active source identity")
            return False
    return True


def _stack_fingerprint(
    active: list[ActivePlugin],
    adoption: Adoption,
    contributors: list[Contributor],
) -> str | None:
    """Hash the validated active stack and its context-producing contracts."""

    by_source: dict[str, list[Contributor]] = {}
    for contributor in contributors:
        by_source.setdefault(contributor.source, []).append(contributor)
    relevant_sources = {adoption.authority_source, *by_source}
    relevant_plugins = [
        plugin for plugin in active if plugin.source in relevant_sources
    ]
    digest = hashlib.sha256()
    header = {
        "schema": "copilot-extensions.context-injection-cache-generation",
        "version": 1,
        "authority": adoption.authority_source,
        "engineVersion": ENGINE_VERSION,
        "plugins": [
            {
                "source": plugin.source,
                "root": os.path.normcase(str(plugin.root)),
            }
            for plugin in sorted(relevant_plugins, key=lambda item: item.source)
        ],
    }
    digest.update(
        json.dumps(
            header,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
    )
    for plugin in sorted(relevant_plugins, key=lambda item: item.source):
        manifest = _manifest(plugin)
        if manifest is None:
            return None
        relative_paths: set[str] = set()
        for relative in ("plugin.json", ".claude-plugin/plugin.json"):
            if (plugin.root / relative).is_file():
                relative_paths.add(relative)
        configured_hooks = manifest.get("hooks")
        if isinstance(configured_hooks, str):
            relative_paths.add(configured_hooks)
        elif isinstance(configured_hooks, list):
            relative_paths.update(
                item for item in configured_hooks if isinstance(item, str)
            )
        else:
            relative_paths.update(
                relative
                for relative in ("hooks.json", "hooks/hooks.json")
                if (plugin.root / relative).is_file()
            )
        for key in ("sessionContext", "sessionContextEngine"):
            configured = manifest.get(key)
            if isinstance(configured, str) and configured:
                relative_paths.add(configured)
        if (plugin.root / "payload-invocation.json").is_file():
            relative_paths.add("payload-invocation.json")
        for relative in (
            "scripts/invoke-context-contributor.sh",
            "scripts/invoke-context-contributor.ps1",
            "scripts/resolve_context_authority.py",
            "scripts/emit-command-catalog.sh",
            "scripts/emit-command-catalog.ps1",
        ):
            if (plugin.root / relative).is_file():
                relative_paths.add(relative)
        for contributor in by_source.get(plugin.source, []):
            relative_paths.add(contributor.command[0])

        for relative in sorted(relative_paths):
            try:
                path = (plugin.root / relative).resolve(strict=True)
                path.relative_to(plugin.root)
                if not path.is_file():
                    return None
                content = path.read_bytes()
            except (OSError, ValueError):
                return None
            if len(content) > MAX_STACK_FINGERPRINT_FILE_BYTES:
                return None
            digest.update(
                json.dumps(
                    [plugin.source, relative, len(content)],
                    ensure_ascii=False,
                    separators=(",", ":"),
                ).encode("utf-8")
            )
            digest.update(content)
    return digest.hexdigest()


def _cache_path(
    session_id: str,
    canonical_cwd: str,
    stack_fingerprint: str,
) -> Path:
    root = _cache_root()
    identity = json.dumps(
        [session_id, canonical_cwd, stack_fingerprint],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return root / f"{hashlib.sha256(identity).hexdigest()}.json"


def _cache_root() -> Path:
    configured = os.environ.get("COPILOT_CONTEXT_INJECTION_CACHE_DIR")
    if configured:
        return Path(configured)
    elif os.name == "nt":
        return (
            Path(os.environ.get("LOCALAPPDATA", tempfile.gettempdir()))
            / "copilot-context-injection"
            / "v1"
        )
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    base = Path(runtime) if runtime else Path.home() / ".cache"
    return base / "copilot-context-injection" / "v1"


def _fast_replay_path(
    session_id: str,
    canonical_cwd: str,
    hook_timestamp: int,
) -> Path:
    identity = json.dumps(
        [session_id, canonical_cwd, hook_timestamp],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return _cache_root() / f"{hashlib.sha256(identity).hexdigest()}.replay.json"


def _private_cache_root(root: Path) -> bool:
    try:
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        root_stat = root.lstat()
    except OSError:
        return False
    if root.is_symlink() or not stat.S_ISDIR(root_stat.st_mode):
        return False
    if os.name != "nt" and (
        root_stat.st_uid != os.getuid()
        or stat.S_IMODE(root_stat.st_mode) != 0o700
    ):
        return False
    return True


def _private_cache_file(path: Path) -> bool:
    try:
        file_stat = path.lstat()
    except OSError:
        return False
    if path.is_symlink() or not stat.S_ISREG(file_stat.st_mode):
        return False
    return os.name == "nt" or (
        file_stat.st_uid == os.getuid()
        and stat.S_IMODE(file_stat.st_mode) == 0o600
    )


def _load_cached(path: Path) -> bytes | None:
    if not _private_cache_file(path):
        return None
    try:
        value = path.read_bytes()
        payload = json.loads(value.decode("utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    if payload == {}:
        return value
    if (
        not isinstance(payload, dict)
        or set(payload) != {"additionalContext"}
        or not isinstance(payload["additionalContext"], str)
        or not payload["additionalContext"]
    ):
        return None
    return value


def _fast_replay_callers(
    active: list[ActivePlugin],
    adoption: Adoption,
    contributors: list[Contributor],
) -> dict[str, str] | None:
    roots = {plugin.source: plugin.root for plugin in active}
    authority_root = roots.get(adoption.authority_source)
    if authority_root is None:
        return None
    callers = {
        "@authority": os.path.normcase(str(authority_root)),
    }
    for contributor in contributors:
        root = roots.get(contributor.source)
        if root is None:
            return None
        callers[
            f"{contributor.source}/{contributor.contributor_id}"
        ] = os.path.normcase(str(root))
    return callers


def _load_fast_replay(
    path: Path,
    caller: str,
    caller_root: Path,
) -> bytes | None:
    if not _private_cache_file(path):
        return None
    try:
        value = path.read_bytes()
        if len(value) > MAX_AGGREGATE_BYTES + 64 * 1024:
            return None
        payload = json.loads(value.decode("utf-8"))
        created_at = payload.get("createdAt")
        callers = payload.get("callers")
        output = payload.get("output")
    except (OSError, UnicodeDecodeError, json.JSONDecodeError, AttributeError):
        return None
    if (
        not isinstance(payload, dict)
        or payload.get("schema")
        != "copilot-extensions.context-injection-fast-replay"
        or payload.get("version") != 1
        or not isinstance(created_at, (int, float))
        or created_at > time.time() + 5
        or time.time() - created_at > FAST_REPLAY_TTL_SECONDS
        or not isinstance(callers, dict)
        or callers.get(caller) != os.path.normcase(str(caller_root))
        or not isinstance(output, str)
    ):
        return None
    encoded = output.encode("utf-8")
    return encoded if _valid_cached_output(encoded) else None


def _valid_cached_output(value: bytes) -> bool:
    try:
        payload = json.loads(value.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return False
    return payload == {} or (
        isinstance(payload, dict)
        and set(payload) == {"additionalContext"}
        and isinstance(payload["additionalContext"], str)
        and bool(payload["additionalContext"])
    )


def _store_fast_replay(
    path: Path,
    callers: dict[str, str],
    output: bytes,
) -> bool:
    if not _valid_cached_output(output):
        return False
    value = json.dumps(
        {
            "schema": "copilot-extensions.context-injection-fast-replay",
            "version": 1,
            "createdAt": time.time(),
            "callers": callers,
            "output": output.decode("utf-8"),
        },
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        if not _private_cache_root(path.parent):
            raise OSError("cache root is not private")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
        os.replace(temporary, path)
        return _private_cache_file(path)
    except OSError:
        _diagnose("aggregate fast replay write failed")
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


def _store_cached(path: Path, value: bytes) -> bool:
    temporary = path.with_name(
        f"{path.name}.{os.getpid()}.{time.time_ns()}.tmp"
    )
    try:
        if not _private_cache_root(path.parent):
            raise OSError("cache root is not private")
        flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL
        if hasattr(os, "O_NOFOLLOW"):
            flags |= os.O_NOFOLLOW
        descriptor = os.open(temporary, flags, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(value)
            handle.flush()
        os.replace(temporary, path)
        if not _private_cache_file(path):
            raise OSError("cache file is not private")
        return True
    except OSError:
        _diagnose("aggregate cache write failed")
        try:
            temporary.unlink(missing_ok=True)
        except OSError:
            pass
        return False


@contextmanager
def _cache_lock(path: Path):
    lock_path = path.with_suffix(".lock")
    if not _private_cache_root(lock_path.parent):
        raise OSError("cache root is not private")
    deadline = time.monotonic() + RENDEZVOUS_DEADLINE_SECONDS
    flags = os.O_RDWR | os.O_CREAT
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    descriptor = os.open(lock_path, flags, 0o600)
    if not _private_cache_file(lock_path):
        os.close(descriptor)
        raise OSError("cache lock is not private")
    with os.fdopen(descriptor, "a+b") as handle:
        if os.name == "nt":
            import msvcrt

            handle.seek(0)
            if not handle.read(1):
                handle.seek(0)
                handle.write(b"\0")
                handle.flush()
            while True:
                try:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                    break
                except OSError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    time.sleep(0.05)
            try:
                yield
            finally:
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
        else:
            import fcntl

            while True:
                try:
                    fcntl.flock(
                        handle.fileno(),
                        fcntl.LOCK_EX | fcntl.LOCK_NB,
                    )
                    break
                except BlockingIOError:
                    if time.monotonic() >= deadline:
                        raise TimeoutError
                    time.sleep(0.05)
            try:
                yield
            finally:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def _producer_reference(value: str | None) -> tuple[str, str] | None:
    if value is None or "/" not in value:
        return None
    source, contributor_id = value.rsplit("/", 1)
    name, separator, marketplace = source.partition("@")
    if (
        not separator
        or not IDENTIFIER.fullmatch(name)
        or not IDENTIFIER.fullmatch(marketplace)
        or not IDENTIFIER.fullmatch(contributor_id)
    ):
        return None
    return source, contributor_id


def _direct_fallback(
    producer: tuple[str, str],
    caller_root: Path,
    hook_input: bytes | BinaryIO,
    launch_cwd: Path,
) -> bytes:
    source, contributor_id = producer
    name, _, marketplace = source.partition("@")
    plugin = ActivePlugin(source, name, marketplace, caller_root)
    manifest = _manifest(plugin)
    if manifest is None or manifest.get("name") != name:
        _diagnose("direct producer fallback identity is invalid")
        return b"{}"
    declared = _contributors(
        [plugin],
        enforce_admission=False,
    )
    if declared is None:
        return b"{}"
    matches = [
        contributor
        for contributor in declared
        if contributor.contributor_id == contributor_id
    ]
    if len(matches) != 1:
        _diagnose("direct producer fallback contributor is unavailable")
        return b"{}"
    result = _run(matches[0], hook_input, launch_cwd)
    if not result.ok or result.context is None:
        return b"{}"
    return json.dumps(
        {"additionalContext": result.context},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _validated_payload_cwd(hook_input: BinaryIO) -> Path | None:
    try:
        hook_input.seek(0)
        payload = json.load(hook_input)
        raw_cwd = payload.get("cwd") if isinstance(payload, dict) else None
        if (
            not isinstance(raw_cwd, str)
            or not raw_cwd
            or not os.path.isabs(raw_cwd)
        ):
            return None
        launch_cwd = Path(raw_cwd).resolve(strict=True)
        return launch_cwd if launch_cwd.is_dir() else None
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return None
    finally:
        hook_input.seek(0)


def _validation_main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="aggregate_context.py --validate",
        description=(
            "Scan a directory marketplace or an effective repository plugin "
            "stack for sessionStart conformance."
        ),
    )
    target = parser.add_mutually_exclusive_group(required=True)
    target.add_argument("--marketplace-root", type=Path)
    target.add_argument("--repository", type=Path)
    parser.add_argument(
        "--authority-root",
        type=Path,
        help=(
            "Exact external context-injection@copilot-extensions payload "
            "for a cross-marketplace roster"
        ),
    )
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)

    authority_source: str | None = None
    wrapper_root: Path | None = None
    initial: list[conformance.Violation] = []
    if args.marketplace_root is not None:
        targets, discovery = conformance.marketplace_targets(
            args.marketplace_root
        )
        initial.extend(discovery.violations)
        if args.authority_root is not None:
            try:
                external_authority = args.authority_root.resolve(strict=True)
            except OSError:
                initial.append(
                    conformance.Violation(
                        "aggregate-authority-missing",
                        "external authority root is unavailable",
                        source=ADOPTED_AUTHORITY_SOURCE,
                        path=str(args.authority_root),
                    )
                )
            else:
                targets = [
                    item
                    for item in targets
                    if item.source != ADOPTED_AUTHORITY_SOURCE
                ]
                targets.append(
                    conformance.PluginTarget(
                        ADOPTED_AUTHORITY_SOURCE,
                        external_authority,
                    )
                )
        authority = [
            item
            for item in targets
            if item.source.partition("@")[0] == "context-injection"
        ]
        if len(authority) == 1:
            authority_source = authority[0].source
            wrapper_root = authority[0].root
        else:
            initial.append(
                conformance.Violation(
                    "aggregate-authority-missing",
                    "marketplace must contain exactly one context-injection authority",
                )
            )
        scope = discovery.scope
    else:
        if args.authority_root is not None:
            parser.error("--authority-root applies only to --marketplace-root")
        repository = _repo_root(args.repository)
        active = _active_plugins(repository, violations=initial)
        targets = [
            conformance.PluginTarget(plugin.source, plugin.root)
            for plugin in (active or [])
        ]
        adoption = _repository_adoption(repository)
        if adoption is None:
            initial.append(
                conformance.Violation(
                    "repository-adoption-invalid",
                    "repository has no trusted compatible context-injection adoption",
                    path=str(repository / ADOPTION_CONFIG),
                )
            )
        else:
            authority_source = adoption.authority_source
            authority = [
                plugin
                for plugin in (active or [])
                if plugin.source == authority_source
            ]
            if len(authority) == 1:
                wrapper_root = authority[0].root
            else:
                initial.append(
                    conformance.Violation(
                        "aggregate-authority-missing",
                        "configured context authority is not exactly active",
                        source=authority_source,
                    )
                )
        scope = f"repository:{repository}"

    report = conformance.scan_plugins(
        targets,
        scope=scope,
        authority_source=authority_source,
        wrapper_root=wrapper_root,
        authority_engine_schema=ENGINE_SCHEMA,
        authority_engine_version=ENGINE_VERSION,
        authority_timeout_seconds=RENDEZVOUS_DEADLINE_SECONDS,
        initial_violations=initial,
    )
    if args.json:
        print(
            json.dumps(
                report.as_dict(),
                ensure_ascii=False,
                separators=(",", ":"),
            )
        )
    else:
        print(conformance.render_text(report))
    return 0 if report.ok else 1


def main() -> int:
    if len(sys.argv) >= 2 and sys.argv[1] == "--validate":
        return _validation_main(sys.argv[2:])
    producer_value: str | None = None
    if len(sys.argv) == 3 and sys.argv[1] == "--producer":
        producer_value = sys.argv[2]
    elif len(sys.argv) != 1:
        return _emit_empty("unsupported context engine arguments")
    producer = _producer_reference(producer_value) if producer_value else None
    if producer_value is not None and producer is None:
        return _emit_empty("producer identity is invalid")

    hook_input = sys.stdin.buffer.read(MAX_INPUT_BYTES + 1)
    try:
        caller_root = Path(os.environ["COPILOT_PLUGIN_ROOT"]).resolve(strict=True)
    except (KeyError, OSError):
        caller_root = None
    try:
        fallback_cwd = Path.cwd().resolve(strict=True)
    except OSError:
        fallback_cwd = Path.cwd()
    validated_launch_cwd: Path | None = None

    def fail(
        message: str | None = None,
        launch_cwd: Path | None = None,
        *,
        direct: bool = True,
        direct_input: bytes | BinaryIO | None = None,
    ) -> int:
        if message:
            _diagnose(message)
        producer_input = hook_input if direct_input is None else direct_input
        contributor_cwd = launch_cwd or validated_launch_cwd or fallback_cwd
        output = (
            _direct_fallback(
                producer,
                caller_root,
                producer_input,
                contributor_cwd,
            )
            if direct and producer is not None and caller_root is not None
            else b"{}"
        )
        sys.stdout.buffer.write(output)
        return 0

    if len(hook_input) > MAX_INPUT_BYTES:
        if producer is not None:
            with tempfile.TemporaryFile() as complete_input:
                complete_input.write(hook_input)
                shutil.copyfileobj(sys.stdin.buffer, complete_input)
                complete_input.seek(0)
                oversized_cwd = _validated_payload_cwd(complete_input)
                return fail(
                    "hook input exceeds the configured limit",
                    launch_cwd=oversized_cwd,
                    direct=oversized_cwd is not None,
                    direct_input=complete_input,
                )
        return fail("hook input exceeds the configured limit", direct=False)
    try:
        payload = json.loads(hook_input.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return fail("hook input is not valid JSON", direct=False)
    cwd = payload.get("cwd") if isinstance(payload, dict) else None
    session_id = payload.get("sessionId") if isinstance(payload, dict) else None
    hook_timestamp = payload.get("timestamp") if isinstance(payload, dict) else None
    if not isinstance(cwd, str) or not cwd:
        return fail("hook input has no cwd", direct=False)
    try:
        launch_cwd = Path(cwd).resolve(strict=True)
        if not launch_cwd.is_dir():
            raise OSError
        validated_launch_cwd = launch_cwd
        repo = _repo_root(launch_cwd)
        engine_root = Path(__file__).resolve().parents[1]
        if caller_root is None:
            raise OSError
    except (OSError, ValueError):
        return fail("aggregator payload root is unavailable", direct=False)

    if not isinstance(session_id, str) or not session_id:
        return fail("hook input has no sessionId")
    canonical_cwd = os.path.normcase(str(launch_cwd))
    fast_replay_path = (
        _fast_replay_path(session_id, canonical_cwd, hook_timestamp)
        if isinstance(hook_timestamp, int) and hook_timestamp >= 0
        else None
    )
    if fast_replay_path is not None:
        fast_replay = _load_fast_replay(
            fast_replay_path,
            producer_value or "@authority",
            caller_root,
        )
        if fast_replay is not None:
            sys.stdout.buffer.write(fast_replay)
            return 0
    staged_launch = _staged_launch()
    if staged_launch is None:
        return fail("host-loaded plugin inventory is not authoritative")
    active = _active_plugins(
        repo,
        staged_launch.plugin_roots if staged_launch.plugin_roots else None,
    )
    if not active:
        return fail("active plugin stack is unavailable")
    adoption = _repository_adoption(repo)
    if adoption is None:
        return fail("repository context aggregation is not adopted")
    if not _prove_authority(
        active,
        adoption,
        engine_root,
        caller_root,
        producer[0] if producer else None,
    ):
        return fail("exact repository context authority is not proven")
    contributors = _contributors(active, adoption)
    if contributors is None:
        return fail(
            "active session-start declarations are not adoption-safe",
            direct=False,
        )
    stack_fingerprint = _stack_fingerprint(
        active,
        adoption,
        contributors,
    )
    if stack_fingerprint is None:
        return fail(
            "active session-start stack fingerprint is unavailable",
            direct=False,
        )
    cache_path = _cache_path(
        session_id,
        canonical_cwd,
        stack_fingerprint,
    )
    fast_replay_callers = _fast_replay_callers(
        active,
        adoption,
        contributors,
    )
    if fast_replay_callers is None:
        return fail("aggregate fast replay callers are unavailable", direct=False)
    try:
        with _cache_lock(cache_path):
            def emit_shared(output: bytes) -> int:
                if fast_replay_path is not None:
                    _store_fast_replay(
                        fast_replay_path,
                        fast_replay_callers,
                        output,
                    )
                sys.stdout.buffer.write(output)
                return 0

            cached = _load_cached(cache_path)
            if cached is not None:
                return emit_shared(cached)

            def publish_empty(message: str) -> int:
                _diagnose(message)
                output = b"{}"
                _store_cached(cache_path, output)
                return emit_shared(output)

            executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)
            started = time.monotonic()
            futures = [
                executor.submit(_run, contributor, hook_input, launch_cwd)
                for contributor in contributors
            ]
            outputs: list[ContributorResult] = []
            try:
                for future in futures:
                    remaining = MAX_TOTAL_TIMEOUT_SECONDS - (
                        time.monotonic() - started
                    )
                    if remaining <= 0:
                        raise TimeoutError
                    outputs.append(future.result(timeout=remaining))
            except TimeoutError:
                for future in futures:
                    future.cancel()
                return publish_empty("aggregate contributor deadline exceeded")
            except Exception as exc:
                _diagnose(f"aggregate contributor collection failed: {exc}")
                return publish_empty("aggregate contributor collection failed")
            finally:
                executor.shutdown(wait=False, cancel_futures=True)
            if not all(result.ok for result in outputs):
                return publish_empty("one or more contributors failed")
            fragments: list[tuple[int, str, str]] = []
            catalogs: list[dict] = []
            catalog_order = 500
            for contributor, result in zip(contributors, outputs, strict=True):
                context = result.context
                if context is None:
                    continue
                if contributor.contributor_id == "command-catalog":
                    catalog = _command_catalog(contributor, context)
                    if catalog is None:
                        return publish_empty(
                            f"command catalog is malformed: {contributor.source}"
                        )
                    catalogs.append(catalog)
                    catalog_order = min(catalog_order, contributor.order)
                    continue
                escaped_context = context.replace("[owner:", "[producer-owner:")
                fragment = (
                    f"[context-contributor: "
                    f"{contributor.source}/{contributor.contributor_id}]\n"
                    f"{escaped_context}"
                )
                fragments.append(
                    (contributor.order, contributor.source, fragment)
                )
            if catalogs:
                catalog_fragment = _catalog_fragment(catalogs)
                if (
                    len(catalog_fragment.encode("utf-8"))
                    > COMMAND_CATALOG_BUDGET_BYTES
                ):
                    return publish_empty(
                        "aggregate command catalog exceeds its budget"
                    )
                fragments.append(
                    (catalog_order, "command-catalogs", catalog_fragment)
                )
            if not fragments:
                return publish_empty("aggregate contributors emitted no context")
            ordered_fragments = sorted(fragments)
            context = "\n\n".join(
                fragment for _, _, fragment in ordered_fragments
            )
            if len(context.encode("utf-8")) > MAX_AGGREGATE_BYTES:
                return publish_empty(
                    "aggregate context exceeds the configured limit"
                )
            delivered_context = context
            if len(context.encode("utf-8")) > MAX_INLINE_CONTEXT_BYTES:
                spilled = _spill_context(
                    session_id,
                    canonical_cwd,
                    context,
                    ordered_fragments,
                )
                if spilled is not None:
                    delivered_context = spilled
            output = json.dumps(
                {"additionalContext": delivered_context},
                ensure_ascii=False,
                separators=(",", ":"),
            ).encode("utf-8")
            if not _store_cached(cache_path, output):
                return fail(
                    "aggregate rendezvous result could not be published",
                    direct=False,
                )
            return emit_shared(output)
    except TimeoutError:
        return fail("aggregate rendezvous lock deadline exceeded", direct=False)
    except OSError:
        return fail("aggregate rendezvous lock is unavailable", direct=False)


if __name__ == "__main__":
    raise SystemExit(main())
