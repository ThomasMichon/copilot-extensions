#!/usr/bin/env python3
"""Mechanical scan of a harness's Copilot CLI customization surfaces.

Part of the `reviewing-customizations` skill. This helper runs the *repeatable,
machine-checkable* half of a customization review so audits are consistent
rather than hand-rolled. It complements -- it does not replace -- the design
critique (a rubber-duck / review sub-agent pass over the same files).

Checks (all stdlib, no dependencies):

  1. skill frontmatter   -- SKILL.md has YAML frontmatter with `name` +
                            `description`, and the description advertises
                            structured trigger phrases.
  2. name/folder match   -- a skill's `name` equals its parent folder name.
  3. trigger collision   -- the same trigger phrase is claimed by two+ skills.
                            Both structured (`Trigger phrases include:`) and
                            inline *prose* quoted phrases count, and (with
                            `--include-plugins`) collisions are detected across
                            LOCAL skills and installed-plugin skills too.
  4. agent safety        -- every Task-capable agent carries an agent-specific
                            anti-self-delegation line; MCP-owning agents also
                            carry an MCP-readiness section, and agent-mcp-backed
                            agents name an equivalent materialized fallback.
  5. MCP plugin recovery -- a plugin that packages an MCP-owning agent also
                            packages a discoverable MCP troubleshooting skill
                            and documents dependencies/prerequisites in README.
  6. secrets             -- a secret-looking key is assigned a literal value
                            (not an env-var / placeholder) in a scanned file.
  7. raw IPs             -- an ssh/scp/rsync command targets a raw IPv4 literal
                            instead of a configured alias.
  8. session context     -- with `--from-settings`, active session-start plugins
                            are classified by declared aggregation role; unsafe
                            multi-output stacks and order-dependent aggregate
                            authorities are rejected statically.
  9. static projections  -- validate deterministic provenance-marked fallback
                            instructions and their lock offline; with
                            `--from-settings`, compare enabled plugin declarations
                            and report source updates or migration work.

Usage:
    scan-customizations.py [REPO_ROOT] [--json] [--strict]
                           [--context-budget]
                           [--from-settings]
                           [--owned-agent-root RELATIVE_DIR]
                           [--include-plugins DIR ...] [--include-installed]

`REPO_ROOT` defaults to the current directory. `--from-settings` assembles the
plugin set **actually loaded for this repo** -- from its
`.github/copilot/settings.json` (+ user settings) `enabledPlugins` /
`extraKnownMarketplaces` -- and brings each into scope: an in-repo `directory`
marketplace plugin (e.g. `./.ai`) is **owned** (fully checked), while an
external marketplace plugin is **advisory**: its skills join the collision map
and its agents receive origin/version-aware safety findings without making the
consumer repo fail strict mode. `--include-plugins` / `--include-installed` add raw
installed-plugin trees (layout `<root>/<marketplace>/<plugin>/...`) the
same advisory way. Exit code is 0 unless `--strict` is given and at least
one BLOCKING finding was reported. `--context-budget` inventories always-loaded
and conditional repository instructions, standard personal instructions,
configured instruction directories, enabled skill/agent frontmatter, and
additionalContext, prompt, and other hook registrations without executing hooks
or printing file contents. Estimated tokens use the fixed, intentionally coarse
heuristic `ceil(Unicode characters / 4)`.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
import instruction_projections

BLOCKING = "blocking"
WARNING = "warning"

# Keys that look like credentials when assigned a literal value.
SECRET_KEY = re.compile(
    r"""(?ix)
    \b(password|passwd|secret|token|api[_-]?key|access[_-]?key|
       client[_-]?secret|private[_-]?key)\b
    \s*[:=]\s*
    (?P<val>.+)$
    """
)
# A value that is NOT a literal secret: env/command substitution, a code-span or
# reference, a placeholder, or empty. Checked against the value's leading run.
SAFE_VALUE = re.compile(
    r"""(?ix)
    ^\s*["'`]?(
      \$ |                                 # $VAR / ${VAR} / $(command)
      ` |                                  # markdown / shell code-span
      < |                                  # <placeholder>
      \{ |                                 # {{ template }} or { json object
      \[ | \( |                            # [ ... ] / ( ... )
      null|none|true|false|changeme|example|your[_-]|xxx+|\.\.\.|
      placeholder|redacted|required|optional|vault|env: |
      ["']["']                             # empty string
    )
    """
)
# A value credential-shaped enough to be a real inline secret: one unbroken
# 12+ char run of secret-ish characters, nothing else on the value side.
CREDENTIAL_SHAPE = re.compile(r"""^["']?[A-Za-z0-9+/=_.\-]{12,}["']?[,\s]*$""")

# Raw IPv4 following an ssh/scp/rsync token (optionally through user@).
SSH_RAW_IP = re.compile(
    r"""(?ix)
    \b(ssh|scp|rsync)\b
    [^\n]*?
    (?<![\w.])
    (?:[\w.-]+@)?
    (?P<ip>(?:\d{1,3}\.){3}\d{1,3})
    """
)
# A line that is teaching what *not* to do -- suppress raw-IP noise on it.
NEGATIVE_EXAMPLE = re.compile(
    r"(?i)\b(wrong|never|don'?t|do not|avoid|bad|incorrect|counter-?example)\b|\u274c"
)
MCP_FALLBACK_ACTION = re.compile(
    r"(?i)\b(use|invoke|run|switch|continue|fall\s+back)\b.{0,120}"
    r"\b(materialized|fleets?|stubs?|agent-mcp\s+materialize)\b",
)
MCP_FALLBACK_DISABLED = re.compile(
    r"(?i)materialized\s+(cli\s+)?fallback\s*:\s*disabled\b.{0,120}"
    r"\b(gate|conditional|authorization)\b",
)
MCP_RECOVERY_PURPOSE = re.compile(
    r"(?i)\b(troubleshoot\w*|diagnos\w*|debug\w*|repair\w*|recover\w*)\b",
)
MCP_SETUP_PURPOSE = re.compile(r"(?i)\b(setup|set(?:ting)?[- ]?up)\b")
EXPLICIT_MCP_REFERENCE = re.compile(r"(?i)\bMCP\b|mcp-servers|agent-mcp")
BRIDGE_REFERENCE = re.compile(r"(?i)\bbridge\b")
README_DEPENDENCY_HEADING = re.compile(
    r"(?im)^(?P<marks>#{1,6})\s+[^\n]*\b("
    r"dependenc(?:y|ies)|prerequisit(?:e|es)|requirements?|requires?|"
    r"companion(?:\s+plugins?)?"
    r")\b[^\n]*$",
)


def has_mcp_fallback(text: str) -> bool:
    """Whether readiness text affirmatively permits or explicitly disables it."""
    if MCP_FALLBACK_DISABLED.search(text):
        return True
    for match in MCP_FALLBACK_ACTION.finditer(text):
        clause_start = max(
            text.rfind(".", 0, match.start()),
            text.rfind("\n", 0, match.start()),
        )
        prefix = text[clause_start + 1:match.start()]
        if re.search(
            r"(?i)\b(do\s+not|don't|never|must\s+not|should\s+not|"
            r"cannot|can't|may\s+not)\b",
            prefix,
        ):
            continue
        if re.search(r"(?i)\b(no|neither)\b", match.group(0)):
            continue
        return True
    return False


def has_mcp_troubleshooting_skill(plugin_root: Path) -> bool:
    """Whether a plugin ships a discoverable skill for its MCP failure path."""
    for skill_file in sorted(plugin_root.glob("skills/*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8", errors="replace")
        fm = split_frontmatter(text)
        if fm is None:
            continue
        frontmatter, _ = fm
        identity = " ".join(filter(None, (
            get_field(frontmatter, "name"),
            get_field_block(frontmatter, "description"),
        )))
        explicit_mcp = EXPLICIT_MCP_REFERENCE.search(identity)
        recovery = MCP_RECOVERY_PURPOSE.search(identity)
        setup = MCP_SETUP_PURPOSE.search(identity)
        if (
            (recovery and (explicit_mcp or BRIDGE_REFERENCE.search(identity)))
            or (setup and explicit_mcp)
        ):
            return True
    return False


def strip_markdown_fences(text: str) -> str:
    """Remove balanced or trailing fenced blocks before heading inspection."""
    kept: list[str] = []
    in_fence = False
    fence_char = ""
    fence_length = 0
    fence_indent = ""
    for line in text.splitlines(keepends=True):
        marker = re.match(r"^(\s*)(`{3,}|~{3,})", line)
        if not in_fence:
            if marker:
                in_fence = True
                fence_indent = marker.group(1)
                fence_char = marker.group(2)[0]
                fence_length = len(marker.group(2))
                continue
            kept.append(line)
            continue
        closing = re.match(
            rf"^{re.escape(fence_indent)}{re.escape(fence_char)}"
            rf"{{{fence_length},}}\s*$",
            line,
        )
        if closing:
            in_fence = False
    return "".join(kept)


def readme_documents_dependencies(plugin_root: Path) -> bool:
    """Whether the plugin README has an explicit dependency/prerequisite section."""
    readme = plugin_root / "README.md"
    if not readme.is_file():
        return False
    text = strip_markdown_fences(
        readme.read_text(encoding="utf-8", errors="replace")
    )
    for heading in README_DEPENDENCY_HEADING.finditer(text):
        section_start = heading.end()
        level = len(heading.group("marks"))
        next_heading = re.search(
            rf"(?m)^#{{1,{level}}}\s+",
            text[section_start:],
        )
        section_end = (
            section_start + next_heading.start()
            if next_heading
            else len(text)
        )
        if text[section_start:section_end].strip():
            return True
    return False


def plugin_root_for_agent(
    root: Path,
    agent_file: Path,
    source: PluginSource | None,
) -> Path | None:
    """Return the package root for a plugin-owned agent, else None."""
    if source is not None:
        return source.payload_root
    candidate = agent_file.parent.parent
    if candidate.parent.resolve() == (root.resolve() / "plugins"):
        return candidate
    return None


def frontmatter_tool_names(frontmatter: str) -> set[str] | None:
    """Return normalized tool names, or None when tools are unrestricted."""
    if not re.search(r"(?im)^tools\s*:", frontmatter):
        return None
    raw = get_field_block(frontmatter, "tools")
    without_comments = "\n".join(line.split("#", 1)[0] for line in raw.splitlines())
    return {
        token.lower()
        for token in re.findall(
            r"[A-Za-z*][A-Za-z0-9_.*:/-]*", without_comments
        )
    }


def agent_can_invoke_task(frontmatter: str) -> bool:
    """Whether an agent's declared tool surface includes the Task/agent tool."""
    tools = frontmatter_tool_names(frontmatter)
    return tools is None or bool({"*", "agent", "task"} & tools)


def has_anti_self_delegation(text: str, agent_name: str) -> bool:
    """Require an explicit do-not-spawn/delegate line naming this agent type."""
    flat = re.sub(r"\s+", " ", text)
    name = re.escape(agent_name.strip().strip("'\""))
    if not name:
        return False
    return bool(re.search(
        rf"(?i)do\s+not\b.{{0,160}}"
        rf"(?:task\s+tool|spawn|delegate)\b.{{0,160}}"
        rf"(?:another\s+)?[`'\"]?{name}[`'\"]?\s+agent\b",
        flat,
    ))

CONFIG_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".psd1", ".env", ".ini", ".conf"}
MCP_BRIDGE_SUFFIXES = {".json", ".yaml", ".yml"}
# Heavy / irrelevant trees to skip when walking a large monorepo.
PRUNE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__",
    "logs", ".mypy_cache", ".pytest_cache", "target", ".idea", "site-packages",
}
TOKEN_HEURISTIC_CHARS = 4
ADDITIONAL_CONTEXT_EVENTS = {
    "sessionstart",
    "posttooluse",
    "posttoolusefailure",
    "notification",
    "subagentstart",
}
SESSION_CONTEXT_SCHEMA = "copilot-extensions.session-context-contributors"
SESSION_CONTEXT_VERSION = 1
SESSION_CONTEXT_MAX_TIMEOUT_SECONDS = 10
SESSION_CONTEXT_MAX_BYTES = 65536
AGGREGATE_AUTHORITY_NAME = "context-injection"
SESSION_CONTEXT_ADOPTION_SCHEMA = "copilot-extensions.context-injection"
SESSION_CONTEXT_ADOPTION_CONFIG = Path(".context-injection/config.yaml")
SESSION_CONTEXT_ADOPTION_MAX_BYTES = 4096
SESSION_CONTEXT_ENGINE_SCHEMA = (
    "copilot-extensions.context-injection-engine"
)
SESSION_CONTEXT_ENGINE_VERSION = 5
ADOPTED_AUTHORITY_SOURCE = "context-injection@copilot-extensions"
SESSION_CONTEXT_IDENTIFIER = re.compile(
    r"^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$"
)
SESSION_CONTEXT_ROLES = {
    "authority": "aggregate-authority",
    "contributor": "complete-declared-contributor",
    "side_effect": "complete-declared-side-effect-only",
    "legacy": "legacy-direct-or-unknown",
}


@dataclass
class Finding:
    severity: str
    check: str
    path: str
    message: str


@dataclass
class PluginSource:
    """A loaded plugin with ownership, source, and release identity.

    ``controlled`` is True when the repo under review OWNS the plugin (its own
    in-repo ``.ai`` directory-marketplace plugins, or its ``plugins/*`` suite):
    those get full checks and their findings are actionable in-repo. When False
    the plugin is **external** (installed from another marketplace) -- its skills
    are reference-only for collision detection, while agent safety findings are
    advisory and carry an upstream remediation pointer.
    """

    skills_root: Path
    origin: str                # "<marketplace>/<plugin>" label
    controlled: bool = False
    source: str = ""           # upstream repo URL for an external plugin ("" if in-repo/unknown)
    version: str = ""          # installed plugin version ("" when unavailable)

    @property
    def payload_root(self) -> Path:
        """Return the plugin directory containing skills, agents, and hooks."""
        return self.skills_root.parent

    @property
    def plugin_name(self) -> str:
        """Return the unqualified plugin name from the loaded identity."""
        return self.origin.rsplit("/", 1)[-1]

    @property
    def marketplace(self) -> str:
        """Return the marketplace qualifier from the loaded identity."""
        return self.origin.rsplit("/", 1)[0] if "/" in self.origin else ""


def _load_json_optional(path: Path) -> dict | None:
    """Load a JSON object, distinguishing an empty object from failure."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def _load_json(path: Path) -> dict:
    """Best-effort JSON load (settings/manifests); returns {} on any problem."""
    data = _load_json_optional(path)
    if data is None:
        return {}
    return data


def _load_jsonc(path: Path) -> dict:
    """Best-effort load for Copilot's leading-comment config.json."""
    try:
        text = path.read_text(encoding="utf-8", errors="replace")
        body = "\n".join(
            line for line in text.splitlines()
            if not line.lstrip().startswith("//")
        )
        data = json.loads(body)
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _repo_is_trusted(repo_root: Path, home: Path) -> bool:
    """Match Copilot's exact persisted-folder trust boundary."""
    data = _load_jsonc(home / ".copilot" / "config.json")
    folders = data.get("trustedFolders")
    if not isinstance(folders, list):
        return False
    resolved = repo_root.resolve()
    for value in folders:
        if not isinstance(value, str):
            continue
        try:
            if Path(value).expanduser().resolve(strict=True) == resolved:
                return True
        except OSError:
            continue
    return False


def _merged_settings(
    repo_root: Path,
    home: Path | None = None,
    *,
    require_trust: bool = False,
    include_user: bool = True,
    include_local: bool = True,
) -> tuple[dict[str, bool], dict[str, tuple[dict, Path]]]:
    """Merge the settings that decide a repo's *loaded* plugin set.

    Reads the repo's committed ``.github/copilot/settings.json`` (and the
    ``.claude/settings.json`` fallback) plus the user ``~/.copilot/settings.json``,
    and returns ``(enabled_plugins, marketplaces)``. Repo settings take
    precedence over user settings for a marketplace of the same name. Plugin
    booleans use last-layer-wins semantics, including explicit ``false``.
    """
    selected_home = home or Path.home()
    layers = (
        [(selected_home / ".copilot" / "settings.json", selected_home)]
        if include_user
        else []
    )
    if not require_trust or _repo_is_trusted(repo_root, selected_home):
        layers.append((repo_root / ".claude" / "settings.json", repo_root))
        if include_local:
            layers.append(
                (repo_root / ".claude" / "settings.local.json", repo_root)
            )
        layers.append(
            (repo_root / ".github" / "copilot" / "settings.json", repo_root)
        )
        if include_local:
            layers.append(
                (
                    repo_root / ".github" / "copilot" / "settings.local.json",
                    repo_root,
                )
            )
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, tuple[dict, Path]] = {}
    for p, base in layers:                 # later layers win (repo over user)
        data = _load_json(p)
        ep = data.get("enabledPlugins")
        if isinstance(ep, dict):
            for k, v in ep.items():
                if isinstance(v, bool):
                    enabled[str(k)] = v
        mk = data.get("extraKnownMarketplaces")
        if isinstance(mk, dict):
            for k, v in mk.items():
                if isinstance(v, dict):
                    marketplaces[str(k)] = (v, base)
    return enabled, marketplaces


def _plugin_repo_url(footprint: Path) -> str:
    """A plugin's upstream repo URL from its manifest (``repository`` /
    ``homepage``), read from either manifest spelling. Empty when unknown."""
    for manifest in (footprint / "plugin.json",
                     footprint / ".claude-plugin" / "plugin.json"):
        data = _load_json(manifest)
        for key in ("repository", "homepage"):
            val = data.get(key)
            if isinstance(val, dict):
                val = val.get("url", "")
            if isinstance(val, str) and val.strip():
                return val.strip()
    return ""


def _plugin_version(footprint: Path) -> str:
    """Return the plugin's declared version from either manifest spelling."""
    for manifest in (
        footprint / "plugin.json",
        footprint / ".claude-plugin" / "plugin.json",
    ):
        data = _load_json(manifest)
        value = data.get("version")
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _plugin_hook_files(footprint: Path) -> set[Path]:
    """Return conventional and manifest-declared hook files for a plugin."""
    manifest_data: dict = {}
    for manifest in (
        footprint / "plugin.json",
        footprint / ".claude-plugin" / "plugin.json",
    ):
        data = _load_json(manifest)
        if data:
            manifest_data = data
            break
    configured = manifest_data.get("hooks")
    values = [configured] if isinstance(configured, str) else configured
    if isinstance(values, list):
        payload_root = footprint.resolve()
        paths = set()
        for value in values:
            if not isinstance(value, str) or not value.strip():
                continue
            candidate = (footprint / value).resolve()
            if candidate.is_relative_to(payload_root):
                paths.add(candidate)
    else:
        paths = {footprint / "hooks.json", footprint / "hooks" / "hooks.json"}
    return {path for path in paths if path.is_file()}


def _has_reviewable_payload(footprint: Path) -> bool:
    """Return whether a plugin contains skills, agents, or hook declarations."""
    skills_root = footprint / "skills"
    agents_root = footprint / "agents"
    return (
        (skills_root.is_dir() and any(skills_root.glob("*/SKILL.md")))
        or (agents_root.is_dir() and any(agents_root.glob("*.agent.md")))
        or bool(_plugin_hook_files(footprint))
    )


def _marketplace_manifest(root: Path) -> tuple[dict, Path] | None:
    """Load a supported marketplace manifest and its plugin-root base."""
    for relative in (
        Path(".github/plugin/marketplace.json"),
        Path(".claude-plugin/marketplace.json"),
    ):
        path = root / relative
        data = _load_json_optional(path)
        if data is not None:
            manifest_root = (
                path.parent.parent.parent
                if relative.parts[0] == ".github"
                else path.parent.parent
            )
            return data, manifest_root
    return None


def _directory_marketplace_plugin(
    marketplace: str,
    name: str,
    declaration: dict,
    base: Path,
) -> Path | None:
    """Resolve one directory-marketplace entry with runtime-equivalent checks."""
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
    plugin_root = manifest_root
    metadata = manifest.get("metadata")
    if isinstance(metadata, dict):
        configured_root = metadata.get("pluginRoot")
        if isinstance(configured_root, str) and configured_root.strip():
            plugin_root = manifest_root / configured_root
    try:
        plugin_root = plugin_root.resolve(strict=True)
        plugin_root.relative_to(root)
    except (OSError, ValueError):
        return None
    entries = manifest.get("plugins")
    if not isinstance(entries, list):
        return None
    matches = [
        entry for entry in entries
        if isinstance(entry, dict) and entry.get("name") == name
    ]
    if len(matches) != 1:
        return None
    plugin_source = matches[0].get("source")
    if not isinstance(plugin_source, str) or not plugin_source.strip():
        return None
    try:
        footprint = (plugin_root / plugin_source).resolve(strict=True)
        footprint.relative_to(plugin_root)
    except (OSError, ValueError):
        return None
    manifest_data = _load_json_optional(footprint / "plugin.json")
    if manifest_data is None:
        manifest_data = _load_json_optional(
            footprint / ".claude-plugin" / "plugin.json"
        )
    return (
        footprint
        if manifest_data is not None and manifest_data.get("name") == name
        else None
    )


def assemble_enabled_plugins(
    repo_root: Path,
    installed_root: Path | None = None,
    *,
    home: Path | None = None,
    require_trust: bool = False,
    include_user: bool = True,
    include_local: bool = True,
) -> list[PluginSource]:
    """Assemble the plugin set *actually loaded for this repo* into review scope.

    Resolves the repo's + user's ``settings.json`` ``enabledPlugins`` /
    ``extraKnownMarketplaces`` into concrete :class:`PluginSource` entries: each
    enabled ``<name>@<marketplace>`` mapped to its skills footprint (an in-repo
    ``directory`` marketplace path, else the installed-plugins tree) and
    classified ``controlled`` (in-repo) vs external (with an upstream ``source``).
    Every enabled plugin is returned. Missing or unreadable external payloads
    remain in the result so the session-context inventory can report that their
    output is unknown instead of silently treating them as absent. Never raises.
    """
    selected_home = home or Path.home()
    if installed_root is None:
        installed_root = selected_home / ".copilot" / "installed-plugins"
    enabled, marketplaces = _merged_settings(
        repo_root,
        home,
        require_trust=require_trust,
        include_user=include_user,
        include_local=include_local,
    )
    out: list[PluginSource] = []
    for key in sorted(enabled):
        if not enabled[key]:
            continue
        name, _, mkt = key.partition("@")
        name = name.strip()
        mkt = mkt.strip()
        if not name:
            continue
        origin = f"{mkt}/{name}" if mkt else name
        declaration, base = marketplaces.get(mkt, ({}, repo_root))
        src = declaration.get("source") or {}
        src_kind = str(src.get("source", "")).strip().lower() if isinstance(src, dict) else ""

        footprint = _directory_marketplace_plugin(
            mkt, name, declaration, base
        )
        if footprint is not None:
            try:
                controlled = repo_root.resolve() in footprint.parents or footprint == repo_root.resolve()
            except Exception:
                controlled = False
            skills_root = footprint / "skills"
            source_url = "" if controlled else _plugin_repo_url(footprint)
        else:
            # github / other marketplace -> the vendored installed payload.
            footprint = installed_root / mkt / name if mkt else installed_root / name
            skills_root = footprint / "skills"
            controlled = False
            source_url = ""
            if isinstance(src, dict) and src_kind == "github" and src.get("repo"):
                source_url = f"https://github.com/{str(src['repo']).strip()}"
            if not source_url:
                source_url = _plugin_repo_url(footprint)

        out.append(PluginSource(
            skills_root=skills_root, origin=origin,
            controlled=controlled, source=source_url,
            version=_plugin_version(footprint),
        ))
    return out



@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    instruction_projections: dict | None = None

    def add(self, severity: str, check: str, path: Path | str, message: str) -> None:
        self.findings.append(Finding(severity, check, str(path), message))

    @property
    def blocking(self) -> int:
        return sum(1 for f in self.findings if f.severity == BLOCKING)


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
    report: Report,
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
            identity for identity, _path in providers
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


def _session_start_state(footprint: Path) -> str:
    """Return yes/no/unknown without inspecting or executing hook commands."""
    if not footprint.is_dir():
        return "unknown"
    manifest_data: dict = {}
    for manifest in (
        footprint / "plugin.json",
        footprint / ".claude-plugin" / "plugin.json",
    ):
        manifest_data = _load_json(manifest)
        if manifest_data:
            break
    if not manifest_data:
        return "unknown"
    hook_files = _plugin_hook_files(footprint)
    if not hook_files:
        return "unknown" if manifest_data.get("hooks") else "no"
    has_command_hook = False
    for path in sorted(hook_files):
        data = _load_json(path)
        hooks = data.get("hooks")
        if not isinstance(hooks, dict):
            return "unknown"
        entries = hooks.get("sessionStart", hooks.get("SessionStart", []))
        if not isinstance(entries, list):
            return "unknown"
        for entry in entries:
            if not isinstance(entry, dict):
                return "unknown"
            hook_type = re.sub(
                r"[^a-z]", "", str(entry.get("type", "command")).lower()
            )
            if hook_type != "prompt":
                has_command_hook = True
    return "yes" if has_command_hook else "no"


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
            or session_start.get("sideEffects")
            not in {"none", "restart-safe-idempotent"}
            or session_start.get("context")
            not in {"none", "authority-aware", "aggregate-authority"}
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
    if (
        contributors
        and context_behavior not in {"undeclared", "authority-aware"}
    ):
        return "incomplete", 0, side_effects, context_behavior
    if not contributors and context_behavior == "aggregate-authority":
        return "complete", 0, side_effects, context_behavior
    if not contributors and context_behavior == "authority-aware":
        return "complete", 0, side_effects, context_behavior
    if not contributors and context_behavior not in {"undeclared", "none"}:
        return "incomplete", 0, side_effects, context_behavior
    return "complete", len(contributors), side_effects, context_behavior


def _context_injection_origin(value: object) -> str | None:
    if not isinstance(value, str):
        return None
    plugin, separator, marketplace = value.partition("@")
    if (
        not separator
        or plugin != AGGREGATE_AUTHORITY_NAME
        or not SESSION_CONTEXT_IDENTIFIER.fullmatch(marketplace)
    ):
        return None
    return f"{marketplace}/{plugin}"


def _load_session_context_adoption(path: Path) -> dict | None:
    try:
        content = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    if len(content.encode("utf-8")) > SESSION_CONTEXT_ADOPTION_MAX_BYTES:
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


def _session_context_adoption(root: Path) -> tuple[str, str]:
    path = root / SESSION_CONTEXT_ADOPTION_CONFIG
    if not path.is_file():
        return "missing", ""
    try:
        resolved = path.resolve(strict=True)
        resolved.relative_to(root.resolve())
    except (OSError, ValueError):
        return "invalid", ""
    configured = _load_session_context_adoption(resolved)
    if configured is None:
        return "invalid", ""
    if (
        set(configured) != {"schema", "version", "authority", "engine"}
        or configured.get("schema") != SESSION_CONTEXT_ADOPTION_SCHEMA
        or configured.get("version") != 1
        or configured.get("authority") != ADOPTED_AUTHORITY_SOURCE
        or not isinstance(configured.get("engine"), dict)
        or set(configured["engine"]) != {"schema", "version"}
        or configured["engine"].get("schema") != SESSION_CONTEXT_ENGINE_SCHEMA
        or configured["engine"].get("version") != SESSION_CONTEXT_ENGINE_VERSION
    ):
        return "invalid", ""
    authority = _context_injection_origin(configured.get("authority"))
    if authority is None:
        return "invalid", ""
    return "complete", authority


def _compatible_context_engine(footprint: Path) -> bool:
    manifest = _load_json(footprint / "plugin.json")
    if not manifest:
        manifest = _load_json(footprint / ".claude-plugin" / "plugin.json")
    configured = manifest.get("sessionContextEngine")
    if not isinstance(configured, str) or not configured.strip():
        return False
    try:
        payload_root = footprint.resolve()
        contract_path = (footprint / configured).resolve(strict=True)
        contract_path.relative_to(payload_root)
    except (OSError, ValueError):
        return False
    return _load_json(contract_path) == {
        "schema": SESSION_CONTEXT_ENGINE_SCHEMA,
        "version": SESSION_CONTEXT_ENGINE_VERSION,
    }


def scan_session_context(
    root: Path,
    plugin_sources: list[PluginSource],
    report: Report,
) -> dict:
    """Inventory and statically enforce session-start context composition.

    This reads manifests and hook registrations only. It never executes hooks
    and never includes hook commands or emitted context in its result.
    """
    adoption_state, adopted_authority = _session_context_adoption(root)
    entries: list[SessionContextEntry] = []
    authority_entries: list[SessionContextEntry] = []
    known_session_start: list[SessionContextEntry] = []
    unknown_entries: list[SessionContextEntry] = []
    footprints: dict[str, Path] = {}

    for source in plugin_sources:
        footprint = _editable_plugin_footprint(root, source)
        footprints[source.origin] = footprint
        session_start = _session_start_state(footprint)
        (
            declaration,
            contributor_count,
            side_effects,
            context_behavior,
        ) = _session_context_declaration(footprint)
        is_adopted_authority = (
            adoption_state == "complete"
            and source.origin == adopted_authority
        )
        is_legacy_authority = (
            adoption_state != "complete"
            and source.plugin_name == AGGREGATE_AUTHORITY_NAME
        )
        is_declared_authority = context_behavior == "aggregate-authority"

        if (
            is_adopted_authority
            or is_legacy_authority
            or is_declared_authority
        ):
            role = SESSION_CONTEXT_ROLES["authority"]
            possible_non_empty = "yes" if session_start == "yes" else "unknown"
        elif declaration == "complete" and contributor_count:
            role = SESSION_CONTEXT_ROLES["contributor"]
            possible_non_empty = "yes"
        elif (
            declaration == "complete"
            and side_effects == "restart-safe-idempotent"
            and context_behavior == "none"
        ) or (
            adoption_state != "complete"
            and declaration == "complete"
            and not contributor_count
        ):
            role = SESSION_CONTEXT_ROLES["side_effect"]
            possible_non_empty = "no"
        else:
            role = SESSION_CONTEXT_ROLES["legacy"]
            possible_non_empty = "unknown"

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
        if (
            session_start != "no"
            or contributor_count
            or is_adopted_authority
            or is_legacy_authority
            or is_declared_authority
        ):
            entries.append(entry)
        if (
            is_adopted_authority
            or is_legacy_authority
            or is_declared_authority
        ):
            authority_entries.append(entry)
        if session_start == "yes":
            known_session_start.append(entry)
        elif session_start == "unknown":
            unknown_entries.append(entry)
            severity = WARNING if not source.controlled else BLOCKING
            report.add(
                severity,
                "session-context-unknown",
                f"<plugin:{entry.identity}>",
                f"`{entry.identity}` is `{entry.role}`; the scanner cannot "
                "establish whether it emits session-start context",
            )

    authority_proven = False
    if adoption_state == "invalid":
        report.add(
            BLOCKING,
            "session-context-authority",
            ".github/copilot/settings.json",
            "repository session-context aggregation adoption is malformed or "
            "incomplete",
        )
    if len(authority_entries) > 1:
        identities = ", ".join(
            sorted(entry.identity for entry in authority_entries)
        )
        report.add(
            BLOCKING,
            "session-context-authority",
            "<plugin-stack>",
            "multiple aggregate authorities are active: "
            f"{identities}",
        )
    if adoption_state == "complete":
        authority = next(
            (
                entry
                for entry in authority_entries
                if entry.identity == adopted_authority
            ),
            None,
        )
        invalid_reasons: list[str] = []
        if len(authority_entries) != 1:
            invalid_reasons.append(
                "the aggregate authority is missing or ambiguous"
            )
        if authority is None:
            invalid_reasons.append("the exact aggregate authority is not active")
        elif (
            authority.session_start != "yes"
            or authority.declaration != "complete"
            or authority.context_behavior != "aggregate-authority"
        ):
            invalid_reasons.append(
                "the aggregate authority lacks a complete declaration"
            )
        authority_footprint = footprints.get(adopted_authority)
        if authority_footprint is None:
            invalid_reasons.append(
                "the aggregate authority payload is unavailable"
            )
        elif not _compatible_context_engine(authority_footprint):
            invalid_reasons.append(
                "the aggregate authority engine is incompatible"
            )
        unsafe = [
            entry
            for entry in entries
            if entry.identity != adopted_authority
            and (
                entry.declaration != "complete"
                or (
                    entry.role == SESSION_CONTEXT_ROLES["contributor"]
                    and entry.session_start != "yes"
                )
                or (
                    entry.role == SESSION_CONTEXT_ROLES["contributor"]
                    and entry.context_behavior != "authority-aware"
                )
                or entry.role == SESSION_CONTEXT_ROLES["legacy"]
            )
        ]
        if unsafe:
            invalid_reasons.append(
                "enabled session-start plugins are unclassified, incomplete, "
                "or not authority-aware: "
                + ", ".join(sorted(entry.identity for entry in unsafe))
            )
        if invalid_reasons:
            report.add(
                BLOCKING,
                "session-context-authority",
                "<plugin-stack>",
                "; ".join(invalid_reasons),
            )
        else:
            authority_proven = True
    else:
        for authority in authority_entries:
            authority_complete = (
                authority.session_start == "yes"
                and authority.declaration == "complete"
            )
            if not authority_complete:
                report.add(
                    BLOCKING,
                    "session-context-authority",
                    f"<plugin:{authority.identity}>",
                    f"`{authority.identity}` is `{authority.role}` but does not "
                    "have a complete session-start declaration",
                )
            report.add(
                BLOCKING,
                "session-context-authority",
                f"<plugin:{authority.identity}>",
                f"`{authority.identity}` is `{authority.role}`, but no complete "
                "repository adoption selects it as the exact authority",
            )

    if authority_entries and not authority_proven:
        incomplete = [
            entry for entry in known_session_start
            if entry.declaration != "complete"
        ]
        if incomplete:
            identities = ", ".join(
                sorted(
                    f"{entry.identity} ({entry.role})"
                    for entry in incomplete
                )
            )
            report.add(
                BLOCKING,
                "session-context-declaration",
                "<plugin-stack>",
                "aggregate activation is unsafe because known session-start "
                f"plugins are not complete-declared: {identities}",
            )

    possible_outputs = [
        entry
        for entry in known_session_start
        if (
            entry.role == SESSION_CONTEXT_ROLES["authority"]
            if authority_proven
            else entry.role != SESSION_CONTEXT_ROLES["side_effect"]
        )
    ]
    if len(possible_outputs) > 1 and not authority_proven:
        identities = ", ".join(
            sorted(
                f"{entry.identity} ({entry.role})"
                for entry in possible_outputs
            )
        )
        report.add(
            BLOCKING,
            "session-context-collision",
            "<plugin-stack>",
            "multiple possible non-empty session-start outputs are not "
            "composed by runtime-defined merge semantics or one attributable "
            f"owner that does not rely on a last-writer race: {identities}",
        )

    if authority_proven:
        disposition = "repository-authority-proven"
    elif authority_entries:
        disposition = "unproven-aggregate-authority"
    elif unknown_entries:
        disposition = "indeterminate-stand-down"
    elif len(possible_outputs) > 1:
        disposition = "unsafe-multiple-output"
    else:
        disposition = "direct-or-side-effect-only"

    return {
        "disposition": disposition,
        "authority_proven": authority_proven,
        "plugins": [
            {
                "identity": entry.identity,
                "role": entry.role,
                "session_start": entry.session_start,
                "declaration": entry.declaration,
                "possible_non_empty": (
                    "no"
                    if authority_proven
                    and entry.role == SESSION_CONTEXT_ROLES["contributor"]
                    else entry.possible_non_empty
                ),
            }
            for entry in sorted(entries, key=lambda item: item.identity)
        ],
    }


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return (frontmatter, body) if the file opens with a --- YAML block."""
    if not text.startswith("---"):
        return None
    m = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not m:
        return None
    return m.group(1), m.group(2)


def _dedup(items: list[str]) -> list[str]:
    """Case-insensitive dedup preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for t in items:
        k = t.strip().lower()
        if k and k not in seen:
            seen.add(k)
            out.append(t.strip())
    return out


def extract_triggers(frontmatter: str) -> list[str]:
    """Pull *structured* trigger phrases from a `Trigger phrases include:` block.

    Handles both the inline `Trigger phrases include: - 'a' - 'b'` form and the
    multiline dash-list form.
    """
    idx = frontmatter.lower().find("trigger phrases")
    if idx == -1:
        return []
    tail = frontmatter[idx:]
    triggers: list[str] = []
    # Inline: "- 'phrase'" segments anywhere in the tail.
    for m in re.finditer(r"-\s*['\"]([^'\"]+)['\"]", tail):
        triggers.append(m.group(1).strip())
    # Also catch bare "- phrase" list lines with no quotes.
    for line in tail.splitlines()[1:]:
        m = re.match(r"\s*-\s+(?!['\"])(.+?)\s*$", line)
        if m:
            triggers.append(m.group(1).strip())
    return _dedup(triggers)


def get_field_block(frontmatter: str, key: str) -> str:
    """Return a field's value including a multi-line YAML block/folded scalar.

    Covers the three shapes these skills use: a single-line `key: "..."`, a
    block scalar (`key: >` / `key: |` with optional chomp), and a plain value
    continued on indented lines.
    """
    lines = frontmatter.splitlines()
    for i, line in enumerate(lines):
        m = re.match(rf"(?i)^{re.escape(key)}\s*:\s*(.*)$", line)
        if not m:
            continue
        first = m.group(1).strip()
        if first in ("|", ">", "|-", ">-", "|+", ">+", ""):
            block: list[str] = []
            for cont in lines[i + 1:]:
                if cont.strip() == "" or re.match(r"^\s", cont):
                    block.append(cont)
                else:
                    break
            return "\n".join(block)
        return first
    return ""


def extract_prose_triggers(frontmatter: str) -> list[str]:
    """Quoted multi-word trigger phrases embedded in a prose `description`.

    Many skills advertise triggers inline (`Use when asked to "create a
    codespace", ...`) instead of the structured list. Those still drive
    auto-invocation, so they must participate in collision detection. Only the
    `description` value is searched, and only multi-word quoted phrases are
    taken -- single tokens are usually tool/command names, not triggers.
    """
    desc = get_field_block(frontmatter, "description")
    if not desc:
        return []
    out: list[str] = []
    for m in re.finditer(r"['\"\u201c]([A-Za-z][^'\"\u201c\u201d]{3,60})['\"\u201d]", desc):
        phrase = m.group(1).strip()
        if " " in phrase:  # multi-word only
            out.append(phrase)
    return _dedup(out)


def _plugin_origin(sf: Path) -> str:
    """`<marketplace>/<plugin>` (or `<plugin>`) inferred from a skill path."""
    parts = sf.parts
    try:
        si = len(parts) - 1 - parts[::-1].index("skills")
    except ValueError:
        return ""
    plugin = parts[si - 1] if si - 1 >= 0 else ""
    mkt = parts[si - 2] if si - 2 >= 0 else ""
    return f"{mkt}/{plugin}" if mkt and plugin else plugin


def get_field(frontmatter: str, key: str) -> str | None:
    m = re.search(rf"(?im)^{re.escape(key)}\s*:\s*(.*)$", frontmatter)
    return m.group(1).strip() if m else None


def _check_owned_skill(sf: Path, report: Report, frontmatter: str) -> str:
    """Run the owned-skill frontmatter/name checks; return the skill's name."""
    name = get_field(frontmatter, "name")
    desc = "description" in frontmatter.lower()
    if not name:
        report.add(BLOCKING, "skill-frontmatter", sf, "frontmatter missing `name`")
    if not desc:
        report.add(BLOCKING, "skill-frontmatter", sf,
                   "frontmatter missing `description`")
    folder = sf.parent.name
    if name and name != folder:
        report.add(BLOCKING, "name-folder-match", sf,
                   f"skill `name: {name}` != folder `{folder}`")
    structured = extract_triggers(frontmatter)
    if not structured:
        report.add(WARNING, "skill-triggers", sf,
                   "description advertises no structured trigger phrases "
                   "(`Trigger phrases include:` list)")
    return name or folder


def scan_skills(root: Path, report: Report,
                plugin_sources: list[PluginSource] | None = None) -> None:
    trigger_owner: dict[str, set[str]] = {}
    # owner label -> (controlled, source_url). Absent => a plain owned local skill.
    owner_meta: dict[str, tuple[bool, str]] = {}

    # Owned skills: local `.github/skills` + this repo's own `plugins/*`. Full
    # checks apply, and both structured + prose triggers feed the collision map.
    owned = sorted(root.glob(".github/skills/*/SKILL.md"))
    suite_owned = sorted(root.glob("plugins/*/skills/*/SKILL.md"))
    owned += suite_owned
    owned_plugin_skills = {
        (sf.parent.parent.parent.name, sf.parent.name) for sf in suite_owned
    }
    for sf in owned:
        text = sf.read_text(encoding="utf-8", errors="replace")
        fm = split_frontmatter(text)
        if fm is None:
            report.add(BLOCKING, "skill-frontmatter", sf,
                       "SKILL.md has no YAML frontmatter (--- block)")
            continue
        frontmatter, _ = fm
        name = _check_owned_skill(sf, report, frontmatter)
        for t in _dedup(extract_triggers(frontmatter)
                        + extract_prose_triggers(frontmatter)):
            trigger_owner.setdefault(t.lower(), set()).add(name)

    # Plugin sources: the loaded set (from --from-settings) or raw trees (from
    # --include-plugins). A *controlled* (in-repo) plugin gets full checks and is
    # an owned collision participant; an *external* plugin is reference-only, its
    # skills joining the collision map so a LOCAL<->PLUGIN clash is visible and
    # annotated with a fix pointer.
    for ps in sorted(
        plugin_sources or [], key=lambda item: not item.controlled
    ):
        for sf in sorted(ps.skills_root.glob("*/SKILL.md")):
            plugin_name = ps.origin.rsplit("/", 1)[-1]
            logical_key = (plugin_name, sf.parent.name)
            if not ps.controlled and logical_key in owned_plugin_skills:
                continue
            fm = split_frontmatter(sf.read_text(encoding="utf-8", errors="replace"))
            if fm is None:
                continue
            frontmatter, _ = fm
            if ps.controlled:
                name = _check_owned_skill(sf, report, frontmatter)
                label = name
            else:
                nm = get_field(frontmatter, "name") or sf.parent.name
                label = f"{nm} [{ps.origin}]"
                owner_meta[label] = (False, ps.source)
            for t in _dedup(extract_triggers(frontmatter)
                            + extract_prose_triggers(frontmatter)):
                trigger_owner.setdefault(t.lower(), set()).add(label)
            if ps.controlled:
                owned_plugin_skills.add(logical_key)

    for phrase, owners in sorted(trigger_owner.items()):
        uniq = sorted(owners)
        if len(uniq) <= 1:
            continue
        msg = f"trigger '{phrase}' claimed by: {', '.join(uniq)}"
        externals = [o for o in uniq if o in owner_meta and owner_meta[o][0] is False]
        if externals:
            srcs = sorted({owner_meta[o][1] for o in externals if owner_meta[o][1]})
            where = f" (source: {', '.join(srcs)})" if srcs else ""
            msg += (f"  --  involves plugin(s) OUTSIDE this repo's control"
                    f"{where}. Fix in-repo (reclaim the phrase with a local "
                    f"authority-override skill, or disable the plugin), OR file "
                    f"an issue/PR upstream -- if a `<repo>-harness` plugin is "
                    f"enabled for that source, use its contributing-to-<repo> "
                    f"skill (e.g. copilot-extensions-harness -> "
                    f"contributing-to-copilot-extensions).")
        report.add(WARNING, "trigger-collision", ".github/skills", msg)



def resolve_owned_agent_roots(
    root: Path,
    values: list[str] | None,
) -> tuple[Path, ...]:
    """Validate explicit repo-owned agent directories."""
    repo = root.resolve(strict=True)
    resolved_roots: set[Path] = set()
    for raw in values or []:
        relative = Path(raw)
        if (
            not raw.strip()
            or relative.is_absolute()
            or ".." in relative.parts
        ):
            raise ValueError(
                f"--owned-agent-root {raw!r} must be a non-empty "
                "repository-relative directory without '..'"
            )
        candidate = repo / relative
        if not candidate.exists():
            raise ValueError(
                f"--owned-agent-root {raw!r} does not exist"
            )
        if candidate.is_symlink():
            raise ValueError(
                f"--owned-agent-root {raw!r} must not be a symlink"
            )
        try:
            resolved = candidate.resolve(strict=True)
            resolved.relative_to(repo)
        except (OSError, ValueError) as exc:
            raise ValueError(
                f"--owned-agent-root {raw!r} must resolve inside the repository"
            ) from exc
        if resolved == repo or not resolved.is_dir():
            raise ValueError(
                f"--owned-agent-root {raw!r} must name a directory below "
                "the repository root"
            )
        for agent_file in resolved.glob("*.agent.md"):
            if agent_file.is_symlink():
                raise ValueError(
                    f"--owned-agent-root {raw!r} contains symlinked agent "
                    f"{agent_file.name!r}"
                )
            try:
                agent_file.resolve(strict=True).relative_to(resolved)
            except (OSError, ValueError) as exc:
                raise ValueError(
                    f"--owned-agent-root {raw!r} contains an agent outside "
                    "the declared directory"
                ) from exc
        resolved_roots.add(resolved)
    return tuple(sorted(resolved_roots, key=str))


def repo_owned_agent_files(
    root: Path,
    owned_agent_roots: tuple[Path, ...] = (),
) -> set[Path]:
    """Return standard and explicitly declared repository-owned agents."""
    files = (
        set(root.glob(".github/agents/*.agent.md"))
        | set(root.glob(".claude/agents/*.agent.md"))
    )
    for agent_root in owned_agent_roots:
        files.update(agent_root.glob("*.agent.md"))
    return {path.resolve() for path in files if path.is_file()}


def scan_agents(
    root: Path,
    report: Report,
    plugin_sources: list[PluginSource] | None = None,
    owned_agent_roots: tuple[Path, ...] = (),
) -> None:
    # Path -> source. None means editable project/suite source. Owned sources
    # take precedence if the same payload is also discovered as installed.
    agent_files: dict[Path, PluginSource | None] = {}
    owned_plugin_agents: set[tuple[str, str]] = set()
    checked_mcp_plugins: set[Path] = set()
    for af in repo_owned_agent_files(root, owned_agent_roots):
        agent_files[af.resolve()] = None
    for af in root.glob("plugins/*/agents/*.agent.md"):
        agent_files[af.resolve()] = None
        owned_plugin_agents.add((af.parent.parent.name, af.name))
    for source in sorted(
        plugin_sources or [], key=lambda item: not item.controlled
    ):
        for af in source.payload_root.glob("agents/*.agent.md"):
            plugin_name = source.origin.rsplit("/", 1)[-1]
            logical_key = (plugin_name, af.name)
            if not source.controlled and logical_key in owned_plugin_agents:
                continue
            resolved = af.resolve()
            if resolved not in agent_files:
                agent_files[resolved] = source
            elif source.controlled and agent_files[resolved] is not None:
                agent_files[resolved] = source
            if source.controlled:
                owned_plugin_agents.add(logical_key)

    for af, source in sorted(agent_files.items(), key=lambda item: str(item[0])):
        external = source is not None and not source.controlled
        severity = WARNING if external else BLOCKING
        if external:
            identity = source.origin
            if source.version:
                identity += f"@{source.version}"
            else:
                identity += "@<unknown-version>"
            path: Path | str = f"<plugin:{identity}>/agents/{af.name}"
            upstream = source.source or f"the `{source.origin}` marketplace source"
            suffix = (
                f" External enabled plugin `{identity}` is advisory because this "
                f"repo cannot edit its installed payload. Disable or configure "
                f"the plugin here, or fix it upstream at {upstream} using that "
                f"repo's contribution workflow (prefer its `<repo>-harness` "
                f"contributing skill when enabled)."
            )
        else:
            path = af
            suffix = ""

        def add(check: str, message: str) -> None:
            report.add(severity, check, path, message + suffix)

        text = af.read_text(encoding="utf-8", errors="replace")
        fm = split_frontmatter(text)
        if fm is None:
            add("agent-frontmatter",
                ".agent.md has no YAML frontmatter (--- block)")
            continue
        frontmatter, body = fm
        if "description" not in frontmatter.lower():
            add("agent-frontmatter", "frontmatter missing `description`")

        declared_name = get_field(frontmatter, "name")
        agent_name = (
            declared_name.strip().strip("'\"")
            if declared_name
            else af.name.removesuffix(".agent.md")
        )
        has_mcp = bool(re.search(
            r"(?im)^\s*mcp-servers\s*:", frontmatter
        ))
        plugin_root = plugin_root_for_agent(root, af, source)
        if has_mcp and plugin_root is not None:
            plugin_key = plugin_root.resolve()
            if plugin_key not in checked_mcp_plugins:
                checked_mcp_plugins.add(plugin_key)
                if not has_mcp_troubleshooting_skill(plugin_root):
                    add(
                        "mcp-troubleshooting-skill",
                        "plugin packages an MCP-owning agent but has no "
                        "discoverable troubleshooting skill whose name or "
                        "description identifies setup/diagnosis/repair and "
                        "names the MCP or bridge failure path",
                    )
                if not readme_documents_dependencies(plugin_root):
                    add(
                        "plugin-readme-dependencies",
                        "plugin packages an MCP-owning agent but its README.md "
                        "has no explicit Dependencies, Prerequisites, or "
                        "Requirements section",
                    )
        readiness_match = re.search(
            r"(?ims)^##\s+MCP\s+Readiness\b(.*?)(?=^##\s|\Z)",
            body,
        )
        readiness = re.sub(
            r"\s+", " ", readiness_match.group(1) if readiness_match else "",
        )

        if has_mcp and readiness_match is None:
            add(
                "mcp-readiness",
                "declares mcp-servers but has no `## MCP Readiness` section "
                "(probe one tool on startup and preserve the exact error)",
            )

        if agent_can_invoke_task(frontmatter):
            anti_scope = readiness if has_mcp else body
            if not has_anti_self_delegation(anti_scope, agent_name):
                location = " in `## MCP Readiness`" if has_mcp else ""
                add(
                    "anti-recursion",
                    "Task-capable agent has no agent-specific anti-self-"
                    f"delegation line{location} (use: \"Do NOT use the task "
                    f"tool to spawn another `{agent_name}` agent.\")",
                )

        uses_agent_mcp = bool(re.search(
            r"(?im)^\s*command\s*:\s*['\"]?agent-mcp['\"]?"
            r"\s*(?:#.*)?$",
            frontmatter,
        ))
        if uses_agent_mcp and not has_mcp_fallback(readiness):
            add(
                "mcp-fallback",
                "uses agent-mcp but has no equivalent materialized CLI "
                "fallback over the same bridge config",
            )


def _walk_customization_files(root: Path):
    """Yield customization-surface files, pruning heavy/irrelevant trees."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in PRUNE_DIRS and not (d.startswith(".") and d != ".github")
        ]
        for fn in filenames:
            yield Path(dirpath) / fn


def scan_text_files(
    root: Path,
    report: Report,
    plugin_sources: list[PluginSource] | None = None,
) -> None:
    scan_roots = [
        source.payload_root
        for source in (plugin_sources or [])
        if source.controlled
    ]
    scan_roots.append(root)
    seen: set[Path] = set()
    for scan_root in scan_roots:
        owned_plugin_payload = scan_root != root
        for p in _walk_customization_files(scan_root):
            resolved = p.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            name = p.name
            suffix = p.suffix.lower()
            parts = set(p.parts)
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
            # Secrets: only config-shaped customization files.
            config_target = suffix in CONFIG_SUFFIXES and (
                under_github
                or is_mcp
                or "plugins" in parts
                or owned_plugin_payload
            )
            # Raw IPs: surface markdown + those same config files.
            if not (config_target or is_surface_md):
                continue
            try:
                lines = p.read_text(
                    encoding="utf-8", errors="replace"
                ).splitlines()
            except OSError:
                continue
            for n, line in enumerate(lines, 1):
                if config_target:
                    sm = SECRET_KEY.search(line)
                    if sm:
                        val = sm.group("val").strip().strip(",")
                        token = val.split()[0] if val.split() else val
                        if (
                            not SAFE_VALUE.match(val)
                            and CREDENTIAL_SHAPE.match(token)
                        ):
                            report.add(
                                BLOCKING,
                                "secret",
                                f"{p}:{n}",
                                "possible hardcoded secret assigned to "
                                f"{sm.group(1)}",
                            )
                im = SSH_RAW_IP.search(line)
                if im:
                    ip = im.group("ip")
                    window = "\n".join(lines[max(0, n - 4):n])
                    if (
                        not ip.startswith(("0.", "127.", "255."))
                        and not NEGATIVE_EXAMPLE.search(window)
                    ):
                        report.add(
                            WARNING,
                            "raw-ip",
                            f"{p}:{n}",
                            f"ssh/scp/rsync targets raw IP {ip} (use an alias)",
                        )


def _sources_from_raw_dir(root: Path) -> list[PluginSource]:
    """Convert a raw installed-plugins tree (``<root>/<mkt>/<plugin>``)
    into external :class:`PluginSource` entries (reference-only, source read from
    each plugin manifest)."""
    out: list[PluginSource] = []
    for plugin_dir in sorted(root.glob("*/*")):
        if not plugin_dir.is_dir() or not _has_reviewable_payload(plugin_dir):
            continue
        origin = f"{plugin_dir.parent.name}/{plugin_dir.name}"
        out.append(PluginSource(
            skills_root=plugin_dir / "skills", origin=origin,
            controlled=False, source=_plugin_repo_url(plugin_dir),
            version=_plugin_version(plugin_dir),
        ))
    return out


def run(
    root: Path,
    plugin_sources: list[PluginSource] | None = None,
    owned_agent_roots: tuple[Path, ...] = (),
    *,
    projection_sources: list[PluginSource] | None | object = ...,
    projection_root: Path | None = None,
    projection_settings_error: str | None = None,
) -> Report:
    report = Report()
    scan_skills(root, report, plugin_sources)
    scan_agents(root, report, plugin_sources, owned_agent_roots)
    scan_text_files(root, report, plugin_sources)
    selected_projection_sources = (
        plugin_sources if projection_sources is ... else projection_sources
    )
    projection_result = instruction_projections.scan_repository(
        projection_root or root, selected_projection_sources
    )
    if projection_settings_error is not None:
        projection_result.add(
            BLOCKING,
            "projection-settings",
            projection_root or root,
            projection_settings_error,
        )
    for finding in projection_result.findings:
        report.add(
            finding.severity,
            finding.check,
            finding.path,
            finding.message,
        )
    report.instruction_projections = projection_result.to_dict()
    return report


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


def _measure_files(paths: set[Path], root: Path, *,
                   frontmatter_only: bool = False,
                   aliases: tuple[tuple[Path, str], ...] = ()) -> list[dict]:
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
        entries.append({
            "path": _display_path(path, root, aliases),
            **_text_metrics(text, byte_count=byte_count),
        })
    return entries


def _repo_instruction_files(root: Path) -> tuple[set[Path], set[Path]]:
    """Return root-loaded and cwd-conditional repository instructions."""
    conditional: set[Path] = set()
    for name in ("AGENTS.md", "CLAUDE.md", "GEMINI.md"):
        conditional.update(
            path for path in _walk_named_files(root, name)
            if path != root / name
        )
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
        files.update(
            p for p in nested.rglob("*.instructions.md") if p.is_file()
        )
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
        files.update(
            p for p in instruction_dir.rglob("*.instructions.md") if p.is_file()
        )
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
    return {p for p in files if p.is_file()}


def _settings_paths(root: Path, home: Path) -> list[tuple[Path, str, str]]:
    return [
        (home / ".copilot" / "settings.json",
         "user-settings", "personal-copilot"),
        (root / ".claude" / "settings.json",
         "repository-settings", "repository"),
        (root / ".claude" / "settings.local.json",
         "repository-local-settings", "repository"),
        (root / ".github" / "copilot" / "settings.json",
         "repository-settings", "repository"),
        (root / ".github" / "copilot" / "settings.local.json",
         "repository-local-settings", "repository"),
    ]


def _hook_documents(
    root: Path,
    plugin_sources: list[PluginSource],
    home: Path,
) -> list[tuple[Path, str, dict, tuple[tuple[Path, str], ...]]]:
    """Collect file and inline hook declarations without running them."""
    documents: list[
        tuple[Path, str, dict, tuple[tuple[Path, str], ...]]
    ] = []
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
            aliases = (
                ((home / ".copilot", alias),)
                if source == "user-settings"
                else ()
            )
            documents.append((path, source, {"hooks": hooks}, aliases))
    for source in plugin_sources:
        footprint = source.payload_root
        for path in sorted(_plugin_hook_files(footprint)):
            documents.append((
                path,
                f"plugin:{source.origin}",
                _load_json(path),
                ((footprint, f"plugin:{source.origin}"),),
            ))
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
    for path, source, data, aliases in _hook_documents(
        root, plugin_sources, home
    ):
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
                hook_type = re.sub(
                    r"[^a-z]", "", str(entry.get("type", "command")).lower()
                )
                if hook_type == "prompt":
                    registration["payload_size"] = "unknown"
                    prompt_hooks.append(registration)
                elif normalized in ADDITIONAL_CONTEXT_EVENTS:
                    registration["emitted_payload_size"] = "unknown"
                    context_capable.append(registration)
                else:
                    not_additional_context_capable.append(registration)
    return context_capable, prompt_hooks, not_additional_context_capable


def build_context_budget(
    root: Path,
    plugin_sources: list[PluginSource] | None = None,
    *,
    home: Path | None = None,
    owned_agent_roots: tuple[Path, ...] = (),
) -> dict:
    """Build a counts-only context inventory; hook commands are never run."""
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
    custom_entries = _measure_files(
        custom_files, root, aliases=custom_aliases
    )
    plugin_aliases = tuple(
        (source.skills_root.parent, f"plugin:{source.origin}")
        for source in sources
    )
    metadata_entries = _measure_files(
        _metadata_files(root, sources, owned_agent_roots),
        root,
        frontmatter_only=True,
        aliases=plugin_aliases,
    )
    context_hooks, prompt_hooks, other_hooks = _hook_registrations(
        root, sources, home
    )
    static_entries = (
        repo_always_entries
        + repo_conditional_entries
        + personal_entries
        + custom_entries
    )
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
        "known_totals": _sum_metrics(static_entries + metadata_entries),
    }


def _print_context_budget(budget: dict) -> None:
    static = budget["static_instruction_payloads"]
    metadata = budget["metadata_upper_bounds"]
    hooks = budget["hook_registrations"]
    print("\nContext budget (token estimate: ceil(Unicode characters / 4))")
    print("Category                       Files  Chars   Bytes   Words  Est tokens")
    print("-----------------------------  -----  ------  ------  ------  ----------")
    for label, count, totals in (
        ("Repo always-loaded", len(
            static["repository_always_loaded_files"]
        ), _sum_metrics(static["repository_always_loaded_files"])),
        ("Nested repo instructions", len(
            static["repository_conditional_instruction_files"]
        ), _sum_metrics(static["repository_conditional_instruction_files"])),
        ("Personal Copilot", len(
            static["personal_copilot_files"]
        ), _sum_metrics(static["personal_copilot_files"])),
        ("Custom instruction dirs", len(
            static["custom_instruction_dir_files"]
        ), _sum_metrics(static["custom_instruction_dir_files"])),
        ("Metadata upper bound", len(metadata["files"]), metadata["totals"]),
    ):
        print(
            f"{label:<29}  {count:>5}  {totals['characters']:>6}  "
            f"{totals['bytes']:>6}  {totals['words']:>6}  "
            f"{totals['estimated_tokens']:>10}"
        )
    context_hooks = hooks["additional_context_capable"]
    prompt_hooks = hooks["prompt_hooks"]
    other_hooks = hooks["not_additional_context_capable"]
    print(f"additionalContext hooks        {context_hooks['count']:>5}  "
          "payload size unknown (not executed)")
    print(f"Prompt hooks                   {prompt_hooks['count']:>5}  "
          "payload size unknown (not additionalContext)")
    print(f"Other hook events              {other_hooks['count']:>5}  "
          "not additionalContext-capable")


def _print_session_context(inventory: dict) -> None:
    """Print the identity-and-role-only session-context inventory."""
    print(f"\nSession context: {inventory['disposition']}")
    for entry in inventory["plugins"]:
        print(f"  {entry['identity']}: {entry['role']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".", help="repo root (default: .)")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if any BLOCKING finding is reported")
    ap.add_argument("--context-budget", action="store_true",
                    help="report counts-only context usage without executing hooks")
    ap.add_argument("--from-settings", action="store_true",
                    help="assemble the plugin set actually LOADED for this repo "
                         "(from .github/copilot/settings.json + user settings) and "
                         "bring each into scope -- in-repo plugins fully checked, "
                         "external skill collisions and agent findings advisory "
                         "+ source/version-classified")
    ap.add_argument("--include-plugins", action="append", default=[], metavar="DIR",
                    help="installed-plugin tree (<root>/<marketplace>/<plugin>/...) "
                         "whose payloads join the inventory; repeatable")
    ap.add_argument("--include-installed", action="store_true",
                    help="shortcut for --include-plugins ~/.copilot/installed-plugins")
    ap.add_argument(
        "--owned-agent-root",
        action="append",
        default=[],
        metavar="RELATIVE_DIR",
        help="repository-relative directory whose immediate *.agent.md files "
             "are fully owned and checked; repeatable",
    )
    args = ap.parse_args(argv)

    input_root = Path(args.root).expanduser()
    if not input_root.is_dir():
        print(f"error: {input_root} is not a directory", file=sys.stderr)
        return 2
    root = input_root.resolve()
    try:
        owned_agent_roots = resolve_owned_agent_roots(
            root, args.owned_agent_root,
        )
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sources: list[PluginSource] = []
    enabled_settings: dict[str, bool] = {}
    projection_settings_error = None
    if args.from_settings:
        enabled_settings, _marketplaces = _merged_settings(
            root, require_trust=True,
        )
        sources += assemble_enabled_plugins(root, require_trust=True)
        try:
            instruction_projections.validate_committed_settings(root)
            repository_projection_sources = assemble_enabled_plugins(
                root,
                require_trust=False,
                include_user=False,
                include_local=False,
            )
        except ValueError as exc:
            repository_projection_sources = []
            projection_settings_error = str(exc)
    else:
        repository_projection_sources = None

    plugin_dirs = list(args.include_plugins)
    if args.include_installed:
        plugin_dirs.append(str(Path.home() / ".copilot" / "installed-plugins"))
    for d in plugin_dirs:
        p = Path(d).expanduser().resolve()
        if p.is_dir():
            sources += _sources_from_raw_dir(p)
        else:
            print(f"warning: --include-plugins {p} is not a directory (skipped)",
                  file=sys.stderr)

    report = run(
        root,
        sources,
        owned_agent_roots,
        projection_sources=repository_projection_sources,
        projection_root=input_root,
        projection_settings_error=projection_settings_error,
    )
    if args.from_settings:
        scan_installed_mcp_bridge_collisions(
            Path.home() / ".copilot" / "installed-plugins",
            enabled_settings,
            report,
        )
    session_context = (
        scan_session_context(root, sources, report)
        if args.from_settings
        else None
    )
    budget = (
        build_context_budget(
            root,
            sources,
            owned_agent_roots=owned_agent_roots,
        )
        if args.context_budget
        else None
    )

    if args.json:
        payload = {
            "root": str(root),
            "blocking": report.blocking,
            "total": len(report.findings),
            "findings": [asdict(f) for f in report.findings],
        }
        if session_context is not None:
            payload["session_context"] = session_context
        if report.instruction_projections is not None:
            payload["instruction_projections"] = report.instruction_projections
        if budget is not None:
            payload["context_budget"] = budget
        print(json.dumps(payload, indent=2))
    else:
        if not report.findings:
            print("[OK] no mechanical findings")
        else:
            order = {BLOCKING: 0, WARNING: 1}
            for f in sorted(report.findings, key=lambda x: (order.get(x.severity, 9), x.check)):
                tag = "BLOCK" if f.severity == BLOCKING else "WARN "
                print(f"[{tag}] {f.check}: {f.path}\n        {f.message}")
            print(f"\n{report.blocking} blocking, "
                  f"{len(report.findings) - report.blocking} warning(s)")
        if session_context is not None:
            _print_session_context(session_context)
        if budget is not None:
            _print_context_budget(budget)

    if args.strict and report.blocking:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
