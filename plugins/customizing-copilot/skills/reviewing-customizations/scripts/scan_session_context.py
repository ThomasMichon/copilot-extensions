from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

from scan_plugin_sources import (
    COPILOT_EXTENSIONS_SOURCE,
    PluginSource,
    _load_json,
    _plugin_hook_files,
)

BLOCKING = "blocking"
WARNING = "warning"

MCP_BRIDGE_SUFFIXES = {".json", ".yaml", ".yml"}
SESSION_CONTEXT_SCHEMA = "copilot-extensions.session-context-contributors"
SESSION_CONTEXT_VERSION = 1
SESSION_CONTEXT_MAX_TIMEOUT_SECONDS = 10
SESSION_CONTEXT_MAX_BYTES = 65536
SESSION_CONTEXT_IDENTIFIER = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
)
SESSION_CONTEXT_ROLES = {
    "output": "complete-declared-output-capable",
    "side_effect": "proven-output-free",
    "legacy": "legacy-direct-or-unknown",
}
OUTPUT_FREE_SESSION_START_MARKERS = (
    "write-session-guidance.",
    "bootstrap-check.",
    "hook_client.py",
    "register-",
)
SESSION_START_SCRIPT_PATH = re.compile(
    r"scripts[\\/][A-Za-z0-9_.-]+\.(?:sh|ps1|py)",
    re.IGNORECASE,
)
SHELL_STDOUT_WRITE = re.compile(r"^\s*(echo|printf)\b")
POWERSHELL_STDOUT_WRITE = re.compile(
    r"^\s*(Write-Host|Write-Output|\[Console\]::Out\.Write(?:Line)?)\b",
    re.IGNORECASE,
)
STRING_LITERAL = re.compile(r"""(['"])(?P<body>(?:\\.|(?!\1).)*)\1""")


def _mcp_bridge_name(path: Path) -> str:
    name = path.name
    lowered = name.casefold()
    for suffix in (".yaml", ".yml", ".json"):
        if lowered.endswith(suffix):
            name = name[: -len(suffix)]
            break
    if name.casefold().endswith(".mcp"):
        name = name[:-4]
    return name.casefold() if os.name == "nt" else name


def scan_installed_mcp_bridge_collisions(
    installed_root: Path,
    enabled_plugins: dict[str, bool],
    report,
) -> None:
    """Report bridge names whose installed providers make runtime lookup ambiguous."""
    candidates: dict[str, list[tuple[str, Path]]] = {}
    if not installed_root.is_dir():
        return
    for marketplace in sorted(installed_root.iterdir()):
        if not marketplace.is_dir():
            continue
        for plugin in sorted(marketplace.iterdir()):
            if not plugin.is_dir():
                continue
            identity = f"{plugin.name}@{marketplace.name}"
            for subdir in ("agents", "mcp"):
                root = plugin / subdir
                if not root.is_dir():
                    continue
                for path in sorted(root.iterdir()):
                    if path.is_file() and path.suffix.casefold() in MCP_BRIDGE_SUFFIXES:
                        candidates.setdefault(_mcp_bridge_name(path), []).append(
                            (identity, path)
                        )
    for name, providers in sorted(candidates.items()):
        if len(providers) < 2:
            continue
        states = ", ".join(
            f"{identity} ({'enabled' if enabled_plugins.get(identity) else 'disabled'})"
            for identity, _path in providers
        )
        disabled = [
            identity
            for identity, _path in providers
            if not enabled_plugins.get(identity)
        ]
        remediation = (
            " Remove stale disabled payloads with "
            + ", ".join(
                f"`copilot plugin uninstall {identity}`" for identity in disabled
            )
            + "."
            if disabled
            else " Disable or rename one provider; all installed providers are enabled."
        )
        report.add(
            WARNING,
            "mcp-bridge-collision",
            providers[0][1],
            f"bridge `{name}` has multiple installed providers: {states}."
            f"{remediation} Do not delete installed-plugin directories manually.",
        )


@dataclass(frozen=True)
class SessionContextEntry:
    """Identity-and-role-only inventory for one active plugin."""

    identity: str
    plugin_name: str
    role: str
    session_start: str
    declaration: str
    possible_non_empty: str
    side_effects: str
    context_behavior: str


def _editable_plugin_footprint(root: Path, source: PluginSource) -> Path:
    """Prefer editable suite source over an installed copy of the same plugin."""
    if source.marketplace != "copilot-extensions":
        return source.payload_root
    candidate = root / "plugins" / source.plugin_name
    manifest = _load_json(candidate / "plugin.json")
    if candidate.is_dir() and manifest.get("name") == source.plugin_name:
        return candidate
    return source.payload_root


def _session_start_entries(footprint: Path) -> list[dict[str, object]] | None:
    """Return command session-start entries, or None when shape is unknown."""
    if not footprint.is_dir():
        return None
    manifest_data: dict = {}
    for manifest in (
        footprint / "plugin.json",
        footprint / ".claude-plugin" / "plugin.json",
    ):
        manifest_data = _load_json(manifest)
        if manifest_data:
            break
    if not manifest_data:
        return None
    hook_files = _plugin_hook_files(footprint)
    if not hook_files:
        return None if manifest_data.get("hooks") else []
    command_entries: list[dict[str, object]] = []
    for path in sorted(hook_files):
        data = _load_json(path)
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return None
        entries = hooks.get("sessionStart", hooks.get("SessionStart", []))
        if not isinstance(entries, list):
            return None
        for entry in entries:
            if not isinstance(entry, dict):
                return None
            hook_type = re.sub(r"[^a-z]", "", str(entry.get("type", "command")).lower())
            if hook_type != "prompt":
                command_entries.append(entry)
    return command_entries


def _session_start_state(footprint: Path) -> str:
    """Return yes/no/unknown without executing hook commands."""
    entries = _session_start_entries(footprint)
    if entries is None:
        return "unknown"
    return "yes" if entries else "no"


def _session_start_has_fast_fail_marker(text: str) -> bool:
    lowered = text.lower()
    return (
        "additionalcontext" in lowered
        or "invoke-context-contributor" in lowered
        or bool(re.search(r"scripts[\\/]+emit-[a-z0-9-]+\.", lowered))
    )


def _strip_inline_comment(line: str, *, marker: str) -> str:
    in_single = False
    in_double = False
    escaped = False
    for index, char in enumerate(line):
        if escaped:
            escaped = False
            continue
        if char == "\\":
            escaped = True
            continue
        if char == "'" and not in_double:
            in_single = not in_single
            continue
        if char == '"' and not in_single:
            in_double = not in_double
            continue
        if char == marker and not in_single and not in_double:
            return line[:index]
    return line


def _unquoted_shell_value(token: str) -> str:
    return token.replace("\\'", "'").replace('\\"', '"').strip()


def _shell_logical_lines(text: str) -> list[str]:
    lines: list[str] = []
    current = ""
    for raw in text.splitlines():
        stripped = raw.rstrip()
        if current:
            current += " " + stripped.lstrip()
        else:
            current = stripped
        if current.endswith("\\"):
            current = current[:-1].rstrip()
            continue
        lines.append(current)
        current = ""
    if current:
        lines.append(current)
    return lines


def _shell_literal_writes_text(line: str) -> bool:
    if not SHELL_STDOUT_WRITE.match(line):
        return False
    if ">&2" in line or "1>&2" in line:
        return False
    stripped = _strip_inline_comment(line, marker="#").strip()
    if not stripped:
        return False
    if re.search(r">\s*[^&]", stripped):
        return False
    if stripped.startswith("printf '{}'") or stripped.startswith('printf "{}"'):
        return False
    if stripped.startswith("echo '{}'") or stripped.startswith('echo "{}"'):
        return False
    for _quote, body in STRING_LITERAL.findall(stripped):
        value = _unquoted_shell_value(body)
        if not value:
            continue
        if value.startswith("$"):
            continue
        if value.startswith("{"):
            return True
        if re.fullmatch(r"%[-+0-9.#]*(?:\[[0-9]+\])?[A-Za-z](?:\\n)?", value):
            continue
        if re.search(r"[A-Za-z\[]", value):
            return True
    return False


def _powershell_literal_writes_text(line: str) -> bool:
    if not POWERSHELL_STDOUT_WRITE.match(line):
        return False
    stripped = line.strip()
    for _quote, body in STRING_LITERAL.findall(stripped):
        value = body.strip()
        if not value:
            continue
        return not value.startswith("{")
    return "[Console]::Out.Write('{}')" not in stripped


def _resolve_session_start_script(
    footprint: Path,
    command: str,
) -> Path | None:
    match = SESSION_START_SCRIPT_PATH.search(command)
    relative_text = match.group(0) if match else ""
    if not relative_text:
        nested = re.search(
            r"""['"]scripts['"]\)\s+['"](?P<leaf>[A-Za-z0-9_.-]+\.(?:sh|ps1|py))['"]""",
            command,
            re.IGNORECASE,
        )
        if nested:
            relative_text = f"scripts/{nested.group('leaf')}"
    if not relative_text:
        return None
    try:
        script = (footprint / Path(relative_text.replace("\\", "/"))).resolve(
            strict=True
        )
        script.relative_to(footprint.resolve())
    except (OSError, ValueError):
        return None
    return script


def _script_has_output_free_contract(script: Path) -> bool:
    try:
        text = script.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return False
    suffix = script.suffix.lower()
    if suffix == ".sh":
        if any(
            _shell_literal_writes_text(line)
            for line in _shell_logical_lines(text)
            if line.lstrip() and not line.lstrip().startswith("#")
        ):
            return False
        if "trap 'emit_session_start_json' EXIT" in text and "printf '{}'" in text:
            return True
        if (
            "write_session_guidance.py" in text
            and "printf '{}'" in text
            and "|| printf '{}'" in text
        ):
            nested = script.with_name("write_session_guidance.py")
            return nested.is_file() and _script_has_output_free_contract(nested)
        return False
    if suffix == ".ps1":
        if any(
            _powershell_literal_writes_text(line)
            for line in text.splitlines()
            if line.lstrip() and not line.lstrip().startswith("#")
        ):
            return False
        if (
            (
                "finally { Write-SessionStartJson }" in text
                or "function Exit-SessionStart" in text
            )
            and "[Console]::Out.Write('{}')" in text
        ):
            return True
        if "write_session_guidance.py" in text and "[Console]::Out.Write('{}')" in text:
            nested = script.with_name("write_session_guidance.py")
            return nested.is_file() and _script_has_output_free_contract(nested)
        return False
    if suffix == ".py":
        code_lines = [
            line for line in text.splitlines() if line.lstrip() and not line.lstrip().startswith("#")
        ]
        if any("print(" in line for line in code_lines):
            return False
        stdout_lines = [line for line in code_lines if "sys.stdout.write(" in line]
        if not stdout_lines:
            return True
        if script.name == "hook_client.py":
            return (
                'if kind == "sessionStart":' in text
                and 'sys.stdout.write("{}")' in text
            )
        return [line.strip() for line in stdout_lines] == ['sys.stdout.write("{}")']
    return False


def _session_start_is_structurally_output_free(footprint: Path) -> bool:
    """Recognize suite-standard side effects that emit no model context."""
    entries = _session_start_entries(footprint)
    if not entries:
        return False
    for entry in entries:
        commands = [str(entry.get(platform, "")) for platform in ("bash", "powershell")]
        if not all(commands):
            return False
        for command in commands:
            if _session_start_has_fast_fail_marker(command):
                return False
            if not any(
                marker in command.lower() for marker in OUTPUT_FREE_SESSION_START_MARKERS
            ):
                return False
            script = _resolve_session_start_script(footprint, command)
            if script is None or not _script_has_output_free_contract(script):
                return False
    return True


def _uses_suite_output_free_conventions(
    root: Path,
    source: PluginSource,
    footprint: Path,
) -> bool:
    """Limit content proofs to suite-owned source or published payloads."""
    try:
        editable_plugins = (root / "plugins").resolve()
        footprint.resolve().relative_to(editable_plugins)
    except (OSError, ValueError):
        pass
    else:
        return True
    return source.source.removesuffix(".git") == COPILOT_EXTENSIONS_SOURCE


def _session_context_declaration(
    footprint: Path,
) -> tuple[str, int, str, str]:
    """Return declaration state and contributor count without exposing commands."""
    manifest = _load_json(footprint / "plugin.json")
    if not manifest:
        manifest = _load_json(footprint / ".claude-plugin" / "plugin.json")
    configured = manifest.get("sessionContext")
    if not isinstance(configured, str) or not configured.strip():
        return "missing", 0, "undeclared", "undeclared"
    try:
        payload_root = footprint.resolve()
        path = (footprint / configured).resolve(strict=True)
        path.relative_to(payload_root)
    except (OSError, ValueError):
        return "incomplete", 0, "undeclared", "undeclared"
    declaration = _load_json(path)
    if (
        declaration.get("schema") != SESSION_CONTEXT_SCHEMA
        or declaration.get("version") != SESSION_CONTEXT_VERSION
        or declaration.get("complete") is not True
    ):
        return "incomplete", 0, "undeclared", "undeclared"
    contributors = declaration.get("contributors")
    if not isinstance(contributors, list):
        return "incomplete", 0, "undeclared", "undeclared"
    session_start = declaration.get("sessionStart")
    side_effects = "undeclared"
    context_behavior = "undeclared"
    if session_start is not None:
        if (
            not isinstance(session_start, dict)
            or set(session_start) != {"sideEffects", "context"}
            or session_start.get("sideEffects") not in {"none", "restart-safe-idempotent"}
            or session_start.get("context") not in {"none", "direct"}
        ):
            return "incomplete", 0, "undeclared", "undeclared"
        side_effects = session_start["sideEffects"]
        context_behavior = session_start["context"]
    seen: set[str] = set()
    for contributor in contributors:
        if not isinstance(contributor, dict):
            return "incomplete", 0, side_effects, context_behavior
        contributor_id = contributor.get("id")
        order = contributor.get("order", 500)
        timeout = contributor.get("timeoutSeconds", 5)
        max_bytes = contributor.get("maxBytes", 8192)
        if (
            not isinstance(contributor_id, str)
            or not SESSION_CONTEXT_IDENTIFIER.fullmatch(contributor_id)
            or contributor_id in seen
            or contributor.get("pure") is not True
            or not isinstance(order, int)
            or not isinstance(timeout, int)
            or not 1 <= timeout <= SESSION_CONTEXT_MAX_TIMEOUT_SECONDS
            or not isinstance(max_bytes, int)
            or not 1 <= max_bytes <= SESSION_CONTEXT_MAX_BYTES
        ):
            return "incomplete", 0, side_effects, context_behavior
        seen.add(contributor_id)
        for platform, suffix in (("bash", ".sh"), ("powershell", ".ps1")):
            argv = contributor.get(platform)
            if (
                not isinstance(argv, list)
                or not argv
                or not all(isinstance(part, str) and part for part in argv)
            ):
                return "incomplete", 0, side_effects, context_behavior
            relative = Path(argv[0])
            if relative.is_absolute():
                return "incomplete", 0, side_effects, context_behavior
            try:
                command = (payload_root / relative).resolve(strict=True)
                command.relative_to(payload_root)
            except (OSError, ValueError):
                return "incomplete", 0, side_effects, context_behavior
            if command.suffix.lower() != suffix or not command.is_file():
                return "incomplete", 0, side_effects, context_behavior
    if contributors and context_behavior == "none":
        return "incomplete", 0, side_effects, context_behavior
    return "complete", len(contributors), side_effects, context_behavior


def scan_session_context(
    root: Path,
    plugin_sources: list[PluginSource],
    report,
) -> dict:
    """Inventory and statically enforce session-start context composition."""
    entries: list[SessionContextEntry] = []
    unknown_entries: list[SessionContextEntry] = []

    for source in plugin_sources:
        footprint = _editable_plugin_footprint(root, source)
        session_start = _session_start_state(footprint)
        declaration, _, side_effects, context_behavior = _session_context_declaration(
            footprint
        )

        structurally_output_free = (
            _uses_suite_output_free_conventions(root, source, footprint)
            and _session_start_is_structurally_output_free(footprint)
        )
        declared_output_free = (
            declaration == "complete"
            and context_behavior == "none"
            and (
                session_start == "no"
                or (session_start == "yes" and structurally_output_free)
            )
        )
        if declared_output_free or (
            structurally_output_free and context_behavior != "direct"
        ):
            role = SESSION_CONTEXT_ROLES["side_effect"]
            possible_non_empty = "no"
        elif declaration == "complete" and context_behavior == "direct":
            role = SESSION_CONTEXT_ROLES["output"]
            possible_non_empty = (
                "yes" if session_start == "yes" else "unknown" if session_start == "unknown" else "no"
            )
        else:
            role = SESSION_CONTEXT_ROLES["legacy"]
            possible_non_empty = (
                "yes" if session_start == "yes" else "unknown" if session_start == "unknown" else "no"
            )

        entry = SessionContextEntry(
            identity=source.origin,
            plugin_name=source.plugin_name,
            role=role,
            session_start=session_start,
            declaration=declaration,
            possible_non_empty=possible_non_empty,
            side_effects=side_effects,
            context_behavior=context_behavior,
        )
        if session_start != "no":
            entries.append(entry)
        if session_start == "unknown":
            unknown_entries.append(entry)
            severity = WARNING if not source.controlled else BLOCKING
            report.add(
                severity,
                "session-context-unknown",
                f"<plugin:{entry.identity}>",
                f"`{entry.identity}` is `{entry.role}`; the scanner cannot "
                "establish whether it emits session-start context",
            )

    possible_outputs = [entry for entry in entries if entry.possible_non_empty != "no"]
    if len(possible_outputs) > 1:
        identities = ", ".join(
            sorted(f"{entry.identity} ({entry.role})" for entry in possible_outputs)
        )
        report.add(
            BLOCKING,
            "session-context-collision",
            "<plugin-stack>",
            "multiple possible non-empty session-start outputs are not "
            "covered by proven runtime-defined merge semantics: "
            f"{identities}",
        )

    if len(possible_outputs) > 1:
        disposition = "unsafe-multiple-output"
    elif unknown_entries:
        disposition = "indeterminate-stand-down"
    elif possible_outputs:
        disposition = "single-possible-output"
    else:
        disposition = "output-free-stack"

    return {
        "disposition": disposition,
        "plugins": [
            {
                "identity": entry.identity,
                "role": entry.role,
                "session_start": entry.session_start,
                "declaration": entry.declaration,
                "possible_non_empty": entry.possible_non_empty,
            }
            for entry in sorted(entries, key=lambda item: item.identity)
        ],
    }
