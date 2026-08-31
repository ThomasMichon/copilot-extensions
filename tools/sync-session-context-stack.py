#!/usr/bin/env python3
"""Synchronize authority-aware session-context producer hooks."""

from __future__ import annotations

import argparse
import json
import shlex
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PLUGINS = ROOT / "plugins"
MARKETPLACE = ROOT / ".github" / "plugin" / "marketplace.json"
SCHEMA = "copilot-extensions.session-context-contributors"
AUTHORITY = "context-injection"
CONTEXT_ONLY = {
    "ai-attribution",
    "context-handoff",
    "copilot-extensions-harness",
    "delegation-guidance",
}
SUPPRESSED_WORKTREE_CONTEXT = ("session-conduct", "session-machine")
WORKTREE_SIDE_EFFECTS = (
    "register-nudge",
    "register-session",
    "marketplace-overrides",
)
WRAPPERS = {
    "bash": "invoke-context-contributor.sh",
    "powershell": "invoke-context-contributor.ps1",
}


def _load(path: Path) -> dict:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"{path} must contain an object")
    return value


def _dump(value: dict) -> bytes:
    return (json.dumps(value, indent=2) + "\n").encode("utf-8")


def _hook_paths(plugin: Path, manifest: dict) -> list[Path]:
    configured = manifest.get("hooks", "hooks.json")
    values = [configured] if isinstance(configured, str) else configured
    if not isinstance(values, list):
        return []
    return [plugin / item for item in values if isinstance(item, str)]


def _bash_hook(source: str, contributor: dict) -> str:
    command = contributor["bash"]
    arguments = " ".join(shlex.quote(str(part)) for part in command)
    return (
        'r="${COPILOT_PLUGIN_ROOT:-${PLUGIN_ROOT:-'
        '${CLAUDE_PLUGIN_ROOT:-$PWD}}}"; '
        'w="$r/scripts/invoke-context-contributor.sh"; '
        f'if [ -f "$w" ]; then bash "$w" {shlex.quote(source)} '
        f'{shlex.quote(contributor["id"])} {arguments}; '
        "else printf '{}'; fi"
    )


def _ps_quote(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


def _powershell_hook(source: str, contributor: dict) -> str:
    arguments = " ".join(_ps_quote(str(part)) for part in contributor["powershell"])
    return (
        "$r = $env:COPILOT_PLUGIN_ROOT; if (-not $r) { $r = $env:PLUGIN_ROOT }; "
        "if (-not $r) { $r = $env:CLAUDE_PLUGIN_ROOT }; "
        "if (-not $r) { $r = (Get-Location).Path }; "
        "$w = Join-Path (Join-Path $r 'scripts') "
        "'invoke-context-contributor.ps1'; "
        f"if (Test-Path -LiteralPath $w -PathType Leaf) {{ & $w "
        f"{_ps_quote(source)} {_ps_quote(contributor['id'])} {arguments} }} "
        "else { [Console]::Out.Write('{}') }"
    )


def _entry_mentions(entry: dict, relative: str) -> bool:
    candidates = {
        relative.replace("/", "\\"),
        relative.replace("\\", "/"),
        Path(relative).name,
    }
    return any(
        candidate in str(entry.get(platform, ""))
        for candidate in candidates
        for platform in ("bash", "powershell")
    )


def _producer_entry(source: str, contributor: dict) -> dict:
    return {
        "type": "command",
        "powershell": _powershell_hook(source, contributor),
        "bash": _bash_hook(source, contributor),
        "timeoutSec": 30,
    }


def _with_side_effect_only(entry: dict) -> dict:
    updated = dict(entry)
    for platform in ("bash", "powershell"):
        command = str(updated.get(platform, ""))
        for stem in WORKTREE_SIDE_EFFECTS:
            if stem in command and "--side-effect-only" not in command:
                if platform == "bash":
                    command = command.replace(
                        'bash "$s"',
                        'bash "$s" --side-effect-only',
                    )
                else:
                    command = command.replace("& $s", "& $s --side-effect-only")
        updated[platform] = command
    return updated


def _desired_plugin(plugin: Path, marketplace: str) -> dict[Path, bytes]:
    manifest_path = plugin / "plugin.json"
    manifest = _load(manifest_path)
    declaration_name = manifest.get("sessionContext")
    if not isinstance(declaration_name, str):
        raise ValueError(f"{plugin.name} has sessionStart hooks but no sessionContext")
    declaration_path = plugin / declaration_name
    declaration = _load(declaration_path)
    if declaration.get("schema") != SCHEMA or declaration.get("complete") is not True:
        raise ValueError(f"{plugin.name} has an incomplete context declaration")
    contributors = declaration.get("contributors")
    if not isinstance(contributors, list):
        raise ValueError(f"{plugin.name} contributors must be a list")

    if plugin.name != AUTHORITY:
        declaration["sessionStart"] = {
            "sideEffects": (
                "none"
                if plugin.name in CONTEXT_ONLY
                else "restart-safe-idempotent"
            ),
            "context": "authority-aware",
        }

    desired: dict[Path, bytes] = {declaration_path: _dump(declaration)}
    if not contributors:
        return desired

    for platform, filename in WRAPPERS.items():
        del platform
        source = PLUGINS / AUTHORITY / "scripts" / filename
        desired[plugin / "scripts" / filename] = source.read_bytes()

    source_id = f"{plugin.name}@{marketplace}"
    hook_paths = _hook_paths(plugin, manifest)
    session_hooks: list[tuple[Path, dict, list[dict]]] = []
    for path in hook_paths:
        hooks = _load(path)
        events = hooks.get("hooks")
        if not isinstance(events, dict):
            continue
        entries = events.get("sessionStart", events.get("SessionStart"))
        if isinstance(entries, list):
            session_hooks.append((path, hooks, entries))
    if not session_hooks:
        raise ValueError(f"{plugin.name} has contributors but no sessionStart hooks")

    pending = list(contributors)
    for path, hooks, entries in session_hooks:
        rewritten: list[dict] = []
        for entry in entries:
            if not isinstance(entry, dict):
                rewritten.append(entry)
                continue
            if (
                plugin.name == "agent-worktrees"
                and any(_entry_mentions(entry, stem) for stem in SUPPRESSED_WORKTREE_CONTEXT)
            ):
                continue
            match = next(
                (
                    contributor
                    for contributor in pending
                    if _entry_mentions(entry, contributor["bash"][0])
                    or _entry_mentions(entry, contributor["powershell"][0])
                ),
                None,
            )
            if match is not None:
                rewritten.append(_producer_entry(source_id, match))
                pending.remove(match)
            else:
                rewritten.append(
                    _with_side_effect_only(entry)
                    if plugin.name == "agent-worktrees"
                    else entry
                )
        events = hooks["hooks"]
        key = "sessionStart" if "sessionStart" in events else "SessionStart"
        events[key] = rewritten
        desired[path] = _dump(hooks)

    if pending:
        if plugin.name != "agent-worktrees" or {
            item["id"] for item in pending
        } != {"aggregate-context"}:
            missing = ", ".join(item["id"] for item in pending)
            raise ValueError(f"{plugin.name} has no direct hooks for: {missing}")
        path, hooks, entries = session_hooks[-1]
        events = hooks["hooks"]
        key = "sessionStart" if "sessionStart" in events else "SessionStart"
        events[key].append(_producer_entry(source_id, pending[0]))
        desired[path] = _dump(hooks)
    return desired


def _desired() -> dict[Path, bytes]:
    marketplace = _load(MARKETPLACE)
    marketplace_name = marketplace.get("name")
    if not isinstance(marketplace_name, str):
        raise ValueError("marketplace name is missing")
    entries = marketplace.get("plugins")
    if not isinstance(entries, list):
        raise ValueError("marketplace plugins must be a list")
    desired: dict[Path, bytes] = {}
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("source"), str):
            continue
        plugin = ROOT / entry["source"]
        manifest = _load(plugin / "plugin.json")
        if not any(path.is_file() for path in _hook_paths(plugin, manifest)):
            continue
        has_session_start = False
        for path in _hook_paths(plugin, manifest):
            if not path.is_file():
                continue
            events = _load(path).get("hooks")
            if isinstance(events, dict) and (
                events.get("sessionStart") or events.get("SessionStart")
            ):
                has_session_start = True
        if has_session_start:
            desired.update(_desired_plugin(plugin, marketplace_name))
    return desired


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    mismatches: list[str] = []
    for path, expected in _desired().items():
        actual = path.read_bytes() if path.is_file() else None
        if actual == expected:
            continue
        mismatches.append(str(path.relative_to(ROOT)))
        if not args.check:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(expected)
    if mismatches:
        action = "out of date" if args.check else "updated"
        print(f"session-context stack {action}:")
        for path in mismatches:
            print(f"  {path}")
        return 1 if args.check else 0
    print("session-context stack is synchronized")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
