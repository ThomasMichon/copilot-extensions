from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path

from scan_agents import repo_owned_agent_files
from scan_plugin_sources import PluginSource, _load_json, _plugin_hook_files
from scan_skills import split_frontmatter
from scan_text_files import PRUNE_DIRS

TOKEN_HEURISTIC_CHARS = 4
ADDITIONAL_CONTEXT_EVENTS = {
    "sessionstart",
    "posttooluse",
    "posttoolusefailure",
    "notification",
    "subagentstart",
}
_DYNAMIC_CAPTURE_SESSION_ID = "scan-customizations-dynamic-capture"
_DYNAMIC_CAPTURE_TIMEOUT_MARGIN_S = 5.0


def _walk_named_files(root: Path, filename: str):
    """Yield named files without descending into excluded directory trees."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in PRUNE_DIRS]
        if filename in filenames:
            yield Path(dirpath) / filename


def _text_metrics(text: str, *, byte_count: int | None = None) -> dict[str, int]:
    """Return reproducible size metrics for a Unicode text payload."""
    characters = len(text)
    return {
        "characters": characters,
        "bytes": len(text.encode("utf-8")) if byte_count is None else byte_count,
        "words": len(re.findall(r"\S+", text)),
        "estimated_tokens": (
            characters + TOKEN_HEURISTIC_CHARS - 1
        ) // TOKEN_HEURISTIC_CHARS,
    }


def _sum_metrics(entries: list[dict]) -> dict[str, int]:
    keys = ("characters", "bytes", "words", "estimated_tokens")
    return {key: sum(int(entry[key]) for entry in entries) for key in keys}


def _display_path(
    path: Path,
    root: Path,
    aliases: tuple[tuple[Path, str], ...] = (),
) -> str:
    """Render a shareable path, redacting locations outside the reviewed repo."""
    resolved = path.resolve()
    for base, label in aliases:
        try:
            relative = resolved.relative_to(base.resolve())
        except ValueError:
            continue
        suffix = relative.as_posix()
        return f"<{label}>/{suffix}" if suffix != "." else f"<{label}>"
    try:
        return resolved.relative_to(root.resolve()).as_posix()
    except ValueError:
        pass
    return "<external-path>"


def _measure_files(
    paths: set[Path],
    root: Path,
    *,
    frontmatter_only: bool = False,
    aliases: tuple[tuple[Path, str], ...] = (),
) -> list[dict]:
    entries: list[dict] = []
    for path in sorted(paths, key=lambda p: str(p)):
        try:
            raw = path.read_bytes()
        except OSError:
            continue
        text = raw.decode("utf-8", errors="replace")
        byte_count = len(text.encode("utf-8"))
        if frontmatter_only:
            split = split_frontmatter(text)
            if split is None:
                continue
            text = split[0]
            byte_count = len(text.encode("utf-8"))
        entries.append(
            {
                "path": _display_path(path, root, aliases),
                **_text_metrics(text, byte_count=byte_count),
            }
        )
    return entries


def _repo_instruction_files(root: Path) -> tuple[set[Path], set[Path]]:
    """Return root-loaded and cwd-conditional repository instructions."""
    conditional: set[Path] = set()
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        conditional.update(path for path in _walk_named_files(root, name) if path != root / name)
    always_loaded: set[Path] = set()
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        candidate = root / name
        if candidate.is_file():
            always_loaded.add(candidate)
    direct = root / ".github" / "copilot-instructions.md"
    if direct.is_file():
        always_loaded.add(direct)
    instruction_dir = root / ".github" / "instructions"
    if instruction_dir.is_dir():
        always_loaded.update(
            p for p in instruction_dir.rglob("*.instructions.md") if p.is_file()
        )
    return always_loaded, conditional


def _custom_instruction_dirs(home: Path) -> list[Path]:
    raw = os.environ.get("COPILOT_CUSTOM_INSTRUCTIONS_DIRS", "")
    if not raw:
        return []
    separators = "," if os.pathsep == "," else f",{re.escape(os.pathsep)}"
    values = re.split(f"[{separators}]", raw)
    directories: list[Path] = []
    seen: set[str] = set()
    for value in values:
        if not value.strip():
            continue
        configured = value.strip()
        if configured == "~":
            directory = home
        elif configured.startswith(("~/", "~\\")):
            directory = home / configured[2:]
        else:
            directory = Path(configured)
        key = str(directory.resolve())
        if key not in seen:
            seen.add(key)
            directories.append(directory)
    return directories


def _instruction_files_in(directory: Path) -> set[Path]:
    files: set[Path] = set()
    if not directory.is_dir():
        return files
    for name in ("AGENTS.md", "copilot-instructions.md"):
        path = directory / name
        if path.is_file():
            files.add(path)
    files.update(p for p in directory.glob("*.instructions.md") if p.is_file())
    nested = directory / ".github" / "instructions"
    if nested.is_dir():
        files.update(p for p in nested.rglob("*.instructions.md") if p.is_file())
    github_instructions = directory / ".github" / "copilot-instructions.md"
    if github_instructions.is_file():
        files.add(github_instructions)
    return files


def _custom_instruction_files(
    home: Path,
) -> tuple[set[Path], tuple[tuple[Path, str], ...]]:
    files: set[Path] = set()
    aliases: list[tuple[Path, str]] = []
    for index, directory in enumerate(_custom_instruction_dirs(home), 1):
        files.update(_instruction_files_in(directory))
        aliases.append((directory, f"custom-instructions-{index}"))
    return files, tuple(aliases)


def _personal_instruction_files(home: Path) -> set[Path]:
    base = home / ".copilot"
    files: set[Path] = set()
    direct = base / "copilot-instructions.md"
    if direct.is_file():
        files.add(direct)
    instruction_dir = base / "instructions"
    if instruction_dir.is_dir():
        files.update(p for p in instruction_dir.rglob("*.instructions.md") if p.is_file())
    return files


def _metadata_files(
    root: Path,
    plugin_sources: list[PluginSource],
    owned_agent_roots: tuple[Path, ...] = (),
) -> set[Path]:
    files = set(root.glob(".github/skills/*/SKILL.md"))
    files.update(root.glob(".claude/skills/*/SKILL.md"))
    files.update(root.glob(".agents/skills/*/SKILL.md"))
    files.update(repo_owned_agent_files(root, owned_agent_roots))
    for source in plugin_sources:
        files.update(source.skills_root.glob("*/SKILL.md"))
        files.update(source.payload_root.glob("agents/*.agent.md"))
    return {path for path in files if path.is_file()}


def _settings_paths(root: Path, home: Path) -> list[tuple[Path, str, str]]:
    return [
        (home / ".copilot" / "settings.json", "user-settings", "personal-copilot"),
        (root / ".claude" / "settings.json", "repository-settings", "repository"),
        (root / ".claude" / "settings.local.json", "repository-local-settings", "repository"),
        (root / ".github" / "copilot" / "settings.json", "repository-settings", "repository"),
        (root / ".github" / "copilot" / "settings.local.json", "repository-local-settings", "repository"),
    ]


def _hook_documents(
    root: Path,
    plugin_sources: list[PluginSource],
    home: Path,
) -> list[tuple[Path, str, dict, tuple[tuple[Path, str], ...]]]:
    """Collect file and inline hook declarations without running them."""
    documents: list[tuple[Path, str, dict, tuple[tuple[Path, str], ...]]] = []
    repo_hook = root / "hooks.json"
    if repo_hook.is_file():
        documents.append((repo_hook, "repository", _load_json(repo_hook), ()))
    repo_hooks = root / ".github" / "hooks"
    if repo_hooks.is_dir():
        documents.extend(
            (path, "repository", _load_json(path), ())
            for path in sorted(repo_hooks.glob("*.json"))
        )
    user_hooks = home / ".copilot" / "hooks"
    if user_hooks.is_dir():
        documents.extend(
            (
                path,
                "user",
                _load_json(path),
                ((home / ".copilot", "personal-copilot"),),
            )
            for path in sorted(user_hooks.glob("*.json"))
        )
    for path, source, alias in _settings_paths(root, home):
        data = _load_json(path)
        hooks = data.get("hooks")
        if isinstance(hooks, dict):
            aliases = ((home / ".copilot", alias),) if source == "user-settings" else ()
            documents.append((path, source, {"hooks": hooks}, aliases))
    for source in plugin_sources:
        footprint = source.payload_root
        for path in sorted(_plugin_hook_files(footprint)):
            documents.append(
                (
                    path,
                    f"plugin:{source.origin}",
                    _load_json(path),
                    ((footprint, f"plugin:{source.origin}"),),
                )
            )
    return documents


def _hook_registrations(
    root: Path,
    plugin_sources: list[PluginSource],
    home: Path,
) -> tuple[list[dict], list[dict], list[dict]]:
    """Enumerate hook declarations without invoking or exposing commands."""
    context_capable: list[dict] = []
    prompt_hooks: list[dict] = []
    not_additional_context_capable: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for path, source, data, aliases in _hook_documents(root, plugin_sources, home):
        key = (str(path.resolve()), source)
        if key in seen:
            continue
        seen.add(key)
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            continue
        for event in sorted(hooks):
            entries = hooks[event]
            if not isinstance(entries, list):
                continue
            for index, entry in enumerate(entries):
                if not isinstance(entry, dict):
                    continue
                registration = {
                    "path": _display_path(path, root, aliases),
                    "source": source,
                    "event": str(event),
                    "index": index,
                    "type": str(entry.get("type", "command")),
                }
                normalized = re.sub(r"[^a-z]", "", str(event).lower())
                hook_type = re.sub(r"[^a-z]", "", str(entry.get("type", "command")).lower())
                if hook_type == "prompt":
                    registration["payload_size"] = "unknown"
                    prompt_hooks.append(registration)
                elif normalized in ADDITIONAL_CONTEXT_EVENTS:
                    registration["emitted_payload_size"] = "unknown"
                    context_capable.append(registration)
                else:
                    not_additional_context_capable.append(registration)
    return context_capable, prompt_hooks, not_additional_context_capable


def _synthetic_session_payload(root: Path, session_id: str) -> bytes:
    """Build a minimal, synthetic sessionStart stdin payload."""
    payload = {
        "sessionId": session_id,
        "cwd": str(root),
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "source": "startup",
        "initialPrompt": "",
    }
    return json.dumps(payload).encode("utf-8")


def _run_sandboxed_session_start_hook(
    script: str,
    *,
    plugin_root: Path,
    project_dir: Path,
    sandbox_home: Path,
    payload: bytes,
    timeout: float,
) -> tuple[bool, str]:
    """Run one sessionStart command hook with HOME/USERPROFILE sandboxed."""
    env = {
        **os.environ,
        "HOME": str(sandbox_home),
        "USERPROFILE": str(sandbox_home),
        "COPILOT_PLUGIN_ROOT": str(plugin_root),
        "PLUGIN_ROOT": str(plugin_root),
        "CLAUDE_PLUGIN_ROOT": str(plugin_root),
        "COPILOT_EXTENSIONS_CONTEXT": "1",
        "COPILOT_PROJECT_DIR": str(project_dir),
    }
    if os.name == "nt":
        shell = (
            shutil.which("pwsh")
            or shutil.which("powershell.exe")
            or shutil.which("powershell")
        )
        if not shell:
            return False, "no PowerShell interpreter found"
        argv = [shell, "-NoLogo", "-NoProfile", "-Command", script]
    else:
        shell = shutil.which("bash")
        if not shell:
            return False, "bash not found"
        argv = [shell, "-c", script]
    try:
        completed = subprocess.run(
            argv,
            input=payload,
            capture_output=True,
            timeout=timeout,
            env=env,
            cwd=str(sandbox_home),
            check=False,
        )
    except subprocess.TimeoutExpired:
        return False, f"timed out after {timeout:.0f}s"
    except OSError as exc:
        return False, str(exc)
    if completed.returncode != 0:
        return False, f"exit code {completed.returncode}"
    return True, ""


def capture_dynamic_session_files(
    root: Path,
    plugin_sources: list[PluginSource],
    home: Path,
) -> tuple[list[dict], list[dict]]:
    """Invoke each plugin-owned sessionStart command hook once, sandboxed."""
    errors: list[dict] = []
    with tempfile.TemporaryDirectory(
        prefix="scan-customizations-dynamic-home-",
        ignore_cleanup_errors=True,
    ) as sandbox:
        sandbox_home = Path(sandbox)
        session_id = _DYNAMIC_CAPTURE_SESSION_ID
        payload = _synthetic_session_payload(root, session_id)
        for path, source, data, aliases in _hook_documents(root, plugin_sources, home):
            if not source.startswith("plugin:"):
                continue
            hooks = data.get("hooks")
            if not isinstance(hooks, dict):
                continue
            entries = hooks.get("sessionStart")
            if not isinstance(entries, list):
                continue
            plugin_root = aliases[0][0] if aliases else path.parent
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                if str(entry.get("type", "command")) != "command":
                    continue
                script = entry.get("powershell" if os.name == "nt" else "bash")
                if not isinstance(script, str) or not script.strip():
                    continue
                try:
                    timeout = float(entry.get("timeoutSec", 15))
                except (TypeError, ValueError):
                    timeout = 15.0
                timeout += _DYNAMIC_CAPTURE_TIMEOUT_MARGIN_S
                ok, detail = _run_sandboxed_session_start_hook(
                    script,
                    plugin_root=plugin_root,
                    project_dir=root,
                    sandbox_home=sandbox_home,
                    payload=payload,
                    timeout=timeout,
                )
                if not ok:
                    errors.append(
                        {
                            "plugin": source,
                            "path": _display_path(path, root, aliases),
                            "detail": detail,
                        }
                    )
        instructions_root = sandbox_home / ".copilot" / "session-state" / session_id / "instructions"
        dynamic_files: set[Path] = set()
        if instructions_root.is_dir():
            dynamic_files = {
                path for path in instructions_root.rglob("*.instructions.md") if path.is_file()
            }
        file_entries = _measure_files(
            dynamic_files,
            root,
            aliases=((instructions_root, "session-instructions"),),
        )
    return file_entries, errors


def build_context_budget(
    root: Path,
    plugin_sources: list[PluginSource] | None = None,
    *,
    home: Path | None = None,
    owned_agent_roots: tuple[Path, ...] = (),
    capture_dynamic: bool = False,
) -> dict:
    """Build a counts-only context inventory."""
    sources = plugin_sources or []
    home = home or Path.home()
    repo_always, repo_conditional = _repo_instruction_files(root)
    repo_always_entries = _measure_files(repo_always, root)
    repo_conditional_entries = _measure_files(repo_conditional, root)
    personal_entries = _measure_files(
        _personal_instruction_files(home),
        root,
        aliases=((home / ".copilot", "personal-copilot"),),
    )
    custom_files, custom_aliases = _custom_instruction_files(home)
    custom_entries = _measure_files(custom_files, root, aliases=custom_aliases)
    plugin_aliases = tuple((source.skills_root.parent, f"plugin:{source.origin}") for source in sources)
    metadata_entries = _measure_files(
        _metadata_files(root, sources, owned_agent_roots),
        root,
        frontmatter_only=True,
        aliases=plugin_aliases,
    )
    context_hooks, prompt_hooks, other_hooks = _hook_registrations(root, sources, home)
    static_entries = repo_always_entries + repo_conditional_entries + personal_entries + custom_entries
    dynamic_entries: list[dict] = []
    dynamic_errors: list[dict] = []
    if capture_dynamic:
        dynamic_entries, dynamic_errors = capture_dynamic_session_files(root, sources, home)
    known_totals_entries = static_entries + metadata_entries + dynamic_entries
    return {
        "token_estimate": {
            "heuristic": "ceil(unicode_characters / 4)",
            "characters_per_token": TOKEN_HEURISTIC_CHARS,
        },
        "static_instruction_payloads": {
            "totals": _sum_metrics(static_entries),
            "repository_always_loaded_files": repo_always_entries,
            "repository_conditional_instruction_files": repo_conditional_entries,
            "personal_copilot_files": personal_entries,
            "custom_instruction_dir_files": custom_entries,
        },
        "metadata_upper_bounds": {
            "totals": _sum_metrics(metadata_entries),
            "files": metadata_entries,
        },
        "dynamic_session_files": {
            "captured": capture_dynamic,
            "totals": _sum_metrics(dynamic_entries),
            "files": dynamic_entries,
            "errors": dynamic_errors,
        },
        "hook_registrations": {
            "additional_context_capable": {
                "count": len(context_hooks),
                "emitted_payload_size": "unknown",
                "registrations": context_hooks,
            },
            "prompt_hooks": {
                "count": len(prompt_hooks),
                "payload_size": "unknown",
                "additional_context": False,
                "registrations": prompt_hooks,
            },
            "not_additional_context_capable": {
                "count": len(other_hooks),
                "registrations": other_hooks,
            },
        },
        "known_totals": _sum_metrics(known_totals_entries),
    }


def _print_context_budget(budget: dict) -> None:
    static = budget["static_instruction_payloads"]
    metadata = budget["metadata_upper_bounds"]
    dynamic = budget.get("dynamic_session_files")
    hooks = budget["hook_registrations"]
    print("\nContext budget (token estimate: ceil(Unicode characters / 4))")
    print("Category                       Files  Chars   Bytes   Words  Est tokens")
    print("-----------------------------  -----  ------  ------  ------  ----------")
    for label, count, totals in (
        ("Repo always-loaded", len(static["repository_always_loaded_files"]), _sum_metrics(static["repository_always_loaded_files"])),
        ("Nested repo instructions", len(static["repository_conditional_instruction_files"]), _sum_metrics(static["repository_conditional_instruction_files"])),
        ("Personal Copilot", len(static["personal_copilot_files"]), _sum_metrics(static["personal_copilot_files"])),
        ("Custom instruction dirs", len(static["custom_instruction_dir_files"]), _sum_metrics(static["custom_instruction_dir_files"])),
        ("Metadata upper bound", len(metadata["files"]), metadata["totals"]),
    ):
        print(
            f"{label:<29}  {count:>5}  {totals['characters']:>6}  "
            f"{totals['bytes']:>6}  {totals['words']:>6}  "
            f"{totals['estimated_tokens']:>10}",
        )
    if dynamic is not None and dynamic["captured"]:
        dyn_totals = dynamic["totals"]
        print(
            f"{'Dynamic session-start files':<29}  "
            f"{len(dynamic['files']):>5}  {dyn_totals['characters']:>6}  "
            f"{dyn_totals['bytes']:>6}  {dyn_totals['words']:>6}  "
            f"{dyn_totals['estimated_tokens']:>10}",
        )
        if dynamic["errors"]:
            print(
                f"  ({len(dynamic['errors'])} sessionStart hook(s) failed to "
                "run -- see JSON output for detail; not counted above)",
            )
    context_hooks = hooks["additional_context_capable"]
    prompt_hooks = hooks["prompt_hooks"]
    other_hooks = hooks["not_additional_context_capable"]
    if dynamic is not None and dynamic["captured"]:
        print(
            f"additionalContext hooks        {context_hooks['count']:>5}  "
            "payload size measured via --capture-dynamic above "
            "(dynamic session files), not this row",
        )
    else:
        print(
            f"additionalContext hooks        {context_hooks['count']:>5}  "
            "payload size unknown (not executed)",
        )
    print(
        f"Prompt hooks                   {prompt_hooks['count']:>5}  "
        "payload size unknown (not additionalContext)",
    )
    print(
        f"Other hook events              {other_hooks['count']:>5}  "
        "not additionalContext-capable",
    )
