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
                            are classified by whether they are proven
                            output-free; ambiguous multi-output stacks are
                            rejected statically.
  9. static projections  -- validate deterministic provenance-marked fallback
                            instructions and their lock offline; with
                            `--from-settings`, compare enabled plugin declarations
                            and report source updates or migration work.

Usage:
    scan-customizations.py [REPO_ROOT] [--json] [--strict]
                           [--context-budget] [--capture-dynamic]
                           [--from-settings]
                           [--owned-agent-root RELATIVE_DIR]
                           [--include-plugins DIR ...] [--include-installed]

`REPO_ROOT` defaults to the current directory. `--from-settings` assembles the
plugin set **actually loaded for this repo** -- from its
`.github/copilot/settings.json` (+ user settings) `enabledPlugins` /
`extraKnownMarketplaces` -- and brings each into scope: an in-repo `directory`
marketplace plugin (e.g. `./.ai`) is **owned** (fully checked); an
`agent-worktrees-repo` marketplace (``{"source": "agent-worktrees-repo",
"repo": "<registered-repo-name>"}``, resolved via the trusted
`agent-worktrees repos find` registry rather than a hardcoded path -- the
portable way to consume *another* repo's directory marketplace from a
committed, shared settings.json) is treated identically to a `directory`
marketplace once resolved; while an external marketplace plugin is
**advisory**: its skills join the collision map
and its agents receive origin/version-aware safety findings without making the
consumer repo fail strict mode. `--include-plugins` / `--include-installed` add raw
installed-plugin trees (layout `<root>/<marketplace>/<plugin>/...`) the
same advisory way. Exit code is 0 unless `--strict` is given and at least
one BLOCKING finding was reported. `--context-budget` inventories always-loaded
and conditional repository instructions, standard personal instructions,
configured instruction directories, enabled skill/agent frontmatter, and
additionalContext, prompt, and other hook registrations without executing hooks
or printing file contents. Estimated tokens use the fixed, intentionally coarse
heuristic `ceil(Unicode characters / 4)`. `--capture-dynamic` (only meaningful
with `--context-budget`) additionally invokes each already-enabled plugin's
`sessionStart` **command** hook once, inside a disposable sandbox
`HOME`/`USERPROFILE` (never the real `~/.copilot/session-state` tree), with a
synthetic session payload, then measures the session-scoped
`instructions/**/*.instructions.md` files the hook writes as its documented
side effect (see `docs/patterns/session-scoped-dynamic-guidance.md`) -- turning
the previously "unknown (not executed)" additionalContext-hook row into real
byte/token counts. Per-plugin invocation failures are reported, never silently
dropped; the sandbox directory is always removed afterward.
"""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

_SCRIPT_DIR = Path(__file__).resolve().parent
if str(_SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPT_DIR))
import instruction_projections
from context_budget import (
    ADDITIONAL_CONTEXT_EVENTS,
    TOKEN_HEURISTIC_CHARS,
    _custom_instruction_dirs,
    _custom_instruction_files,
    _display_path,
    _hook_documents,
    _hook_registrations,
    _instruction_files_in,
    _measure_files,
    _metadata_files,
    _personal_instruction_files,
    _print_context_budget,
    _repo_instruction_files,
    _run_sandboxed_session_start_hook,
    _settings_paths,
    _sum_metrics,
    _synthetic_session_payload,
    _text_metrics,
    _walk_named_files,
    build_context_budget,
    capture_dynamic_session_files,
)
from scan_agents import (
    agent_can_invoke_task,
    frontmatter_tool_names,
    has_anti_self_delegation,
    plugin_root_for_agent,
    repo_owned_agent_files,
    resolve_owned_agent_roots,
    scan_agents,
)
from scan_plugin_sources import (
    AGENT_WORKTREES_REPO_NAME,
    COPILOT_EXTENSIONS_SOURCE,
    PluginSource,
    _agent_worktrees_repo_root,
    _directory_marketplace_plugin,
    _git_head,
    _has_reviewable_payload,
    _load_json,
    _load_json_optional,
    _load_jsonc,
    _marketplace_manifest,
    _merged_settings,
    _payload_is_clean,
    _plugin_commit,
    _plugin_hook_files,
    _plugin_repo_url,
    _plugin_version,
    _repo_is_trusted,
    _sources_from_raw_dir,
    assemble_enabled_plugins,
    resolve_pinned_commits,
)
from scan_session_context import (
    MCP_BRIDGE_SUFFIXES,
    OUTPUT_FREE_SESSION_START_MARKERS,
    POWERSHELL_STDOUT_WRITE,
    SESSION_CONTEXT_IDENTIFIER,
    SESSION_CONTEXT_MAX_BYTES,
    SESSION_CONTEXT_MAX_TIMEOUT_SECONDS,
    SESSION_CONTEXT_ROLES,
    SESSION_CONTEXT_SCHEMA,
    SESSION_CONTEXT_VERSION,
    SESSION_START_SCRIPT_PATH,
    SHELL_STDOUT_WRITE,
    STRING_LITERAL,
    SessionContextEntry,
    _editable_plugin_footprint,
    _mcp_bridge_name,
    _powershell_literal_writes_text,
    _resolve_session_start_script,
    _script_has_output_free_contract,
    _session_context_declaration,
    _session_start_entries,
    _session_start_has_fast_fail_marker,
    _session_start_is_structurally_output_free,
    _session_start_state,
    _shell_literal_writes_text,
    _shell_logical_lines,
    _strip_inline_comment,
    _unquoted_shell_value,
    _uses_suite_output_free_conventions,
    scan_installed_mcp_bridge_collisions,
    scan_session_context,
)
from scan_skills import (
    BLOCKING,
    WARNING,
    BRIDGE_REFERENCE,
    EXPLICIT_MCP_REFERENCE,
    MCP_FALLBACK_ACTION,
    MCP_FALLBACK_DISABLED,
    MCP_RECOVERY_PURPOSE,
    MCP_SETUP_PURPOSE,
    README_DEPENDENCY_HEADING,
    _check_owned_skill,
    _plugin_origin,
    extract_prose_triggers,
    extract_triggers,
    get_field,
    get_field_block,
    has_mcp_fallback,
    has_mcp_troubleshooting_skill,
    readme_documents_dependencies,
    scan_skills,
    split_frontmatter,
    strip_markdown_fences,
)
from scan_text_files import (
    CONFIG_SUFFIXES,
    CREDENTIAL_SHAPE,
    NEGATIVE_EXAMPLE,
    PRUNE_DIRS,
    SAFE_VALUE,
    SECRET_KEY,
    SSH_RAW_IP,
    _walk_customization_files,
    scan_text_files,
)

_COMPAT_TEST_EXPORTS = (Path, shutil, subprocess)


@dataclass
class Finding:
    severity: str
    check: str
    path: str
    message: str


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    instruction_projections: dict | None = None

    def add(self, severity: str, check: str, path: Path | str, message: str) -> None:
        self.findings.append(Finding(severity, check, str(path), message))

    @property
    def blocking(self) -> int:
        return sum(1 for finding in self.findings if finding.severity == BLOCKING)


__all__ = [
    "ADDITIONAL_CONTEXT_EVENTS",
    "AGENT_WORKTREES_REPO_NAME",
    "BLOCKING",
    "BRIDGE_REFERENCE",
    "CONFIG_SUFFIXES",
    "COPILOT_EXTENSIONS_SOURCE",
    "CREDENTIAL_SHAPE",
    "EXPLICIT_MCP_REFERENCE",
    "Finding",
    "MCP_BRIDGE_SUFFIXES",
    "MCP_FALLBACK_ACTION",
    "MCP_FALLBACK_DISABLED",
    "MCP_RECOVERY_PURPOSE",
    "MCP_SETUP_PURPOSE",
    "NEGATIVE_EXAMPLE",
    "OUTPUT_FREE_SESSION_START_MARKERS",
    "POWERSHELL_STDOUT_WRITE",
    "PRUNE_DIRS",
    "PluginSource",
    "README_DEPENDENCY_HEADING",
    "Report",
    "SAFE_VALUE",
    "SECRET_KEY",
    "SESSION_CONTEXT_IDENTIFIER",
    "SESSION_CONTEXT_MAX_BYTES",
    "SESSION_CONTEXT_MAX_TIMEOUT_SECONDS",
    "SESSION_CONTEXT_ROLES",
    "SESSION_CONTEXT_SCHEMA",
    "SESSION_CONTEXT_VERSION",
    "SESSION_START_SCRIPT_PATH",
    "SHELL_STDOUT_WRITE",
    "SSH_RAW_IP",
    "STRING_LITERAL",
    "SessionContextEntry",
    "TOKEN_HEURISTIC_CHARS",
    "WARNING",
    "_agent_worktrees_repo_root",
    "_check_owned_skill",
    "_custom_instruction_dirs",
    "_custom_instruction_files",
    "_directory_marketplace_plugin",
    "_display_path",
    "_editable_plugin_footprint",
    "_git_head",
    "_has_reviewable_payload",
    "_hook_documents",
    "_hook_registrations",
    "_instruction_files_in",
    "_load_json",
    "_load_json_optional",
    "_load_jsonc",
    "_marketplace_manifest",
    "_mcp_bridge_name",
    "_measure_files",
    "_merged_settings",
    "_metadata_files",
    "_payload_is_clean",
    "_personal_instruction_files",
    "_plugin_commit",
    "_plugin_hook_files",
    "_plugin_origin",
    "_plugin_repo_url",
    "_plugin_version",
    "_powershell_literal_writes_text",
    "_print_context_budget",
    "_print_session_context",
    "_repo_instruction_files",
    "_repo_is_trusted",
    "_resolve_session_start_script",
    "_run_sandboxed_session_start_hook",
    "_script_has_output_free_contract",
    "_session_context_declaration",
    "_session_start_entries",
    "_session_start_has_fast_fail_marker",
    "_session_start_is_structurally_output_free",
    "_session_start_state",
    "_settings_paths",
    "_shell_literal_writes_text",
    "_shell_logical_lines",
    "_sources_from_raw_dir",
    "_strip_inline_comment",
    "_sum_metrics",
    "_synthetic_session_payload",
    "_text_metrics",
    "_unquoted_shell_value",
    "_uses_suite_output_free_conventions",
    "_walk_customization_files",
    "_walk_named_files",
    "agent_can_invoke_task",
    "assemble_enabled_plugins",
    "build_context_budget",
    "capture_dynamic_session_files",
    "extract_prose_triggers",
    "extract_triggers",
    "frontmatter_tool_names",
    "get_field",
    "get_field_block",
    "has_anti_self_delegation",
    "has_mcp_fallback",
    "has_mcp_troubleshooting_skill",
    "main",
    "plugin_root_for_agent",
    "readme_documents_dependencies",
    "repo_owned_agent_files",
    "resolve_owned_agent_roots",
    "resolve_pinned_commits",
    "run",
    "scan_agents",
    "scan_installed_mcp_bridge_collisions",
    "scan_session_context",
    "scan_skills",
    "scan_text_files",
    "split_frontmatter",
    "strip_markdown_fences",
]


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
        projection_root or root,
        selected_projection_sources,
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


def _print_session_context(inventory: dict) -> None:
    """Print the identity-and-role-only session-context inventory."""
    print(f"\nSession context: {inventory['disposition']}")
    for entry in inventory["plugins"]:
        print(f"  {entry['identity']}: {entry['role']}")


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    ap.add_argument("root", nargs="?", default=".", help="repo root (default: .)")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    ap.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if any BLOCKING finding is reported",
    )
    ap.add_argument(
        "--context-budget",
        action="store_true",
        help="report counts-only context usage without executing hooks",
    )
    ap.add_argument(
        "--capture-dynamic",
        action="store_true",
        help="with --context-budget, additionally invoke each "
        "plugin's sessionStart command hook once inside a "
        "sandboxed HOME/USERPROFILE and measure the "
        "session-scoped instructions/*.instructions.md files "
        "it writes (never touches the real session-state tree)",
    )
    ap.add_argument(
        "--from-settings",
        action="store_true",
        help="assemble the plugin set actually LOADED for this repo "
        "(from .github/copilot/settings.json + user settings) and "
        "bring each into scope -- in-repo plugins fully checked, "
        "external skill collisions and agent findings advisory "
        "+ source/version-classified",
    )
    ap.add_argument(
        "--include-plugins",
        action="append",
        default=[],
        metavar="DIR",
        help="installed-plugin tree (<root>/<marketplace>/<plugin>/...) "
        "whose payloads join the inventory; repeatable",
    )
    ap.add_argument(
        "--include-installed",
        action="store_true",
        help="shortcut for --include-plugins ~/.copilot/installed-plugins",
    )
    ap.add_argument(
        "--agent-worktrees-path",
        help=(
            "Resolved agent-worktrees command (e.g. the session command "
            "catalog's argv[0]), used to resolve any agent-worktrees-repo "
            "marketplace source with --from-settings. Falls back to an "
            "ambient PATH lookup when omitted."
        ),
    )
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
        owned_agent_roots = resolve_owned_agent_roots(root, args.owned_agent_root)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    sources: list[PluginSource] = []
    enabled_settings: dict[str, bool] = {}
    projection_settings_error = None
    if args.from_settings:
        enabled_settings, _marketplaces = _merged_settings(root, require_trust=True)
        try:
            sources += assemble_enabled_plugins(
                root, require_trust=True,
                agent_worktrees_command=args.agent_worktrees_path,
            )
        except ValueError as exc:
            print(f"error: {exc}", file=sys.stderr)
            return 2
        repository_projection_sources = []
        try:
            instruction_projections.validate_committed_settings(root)
        except ValueError as exc:
            projection_settings_error = str(exc)
        else:
            try:
                repository_projection_sources = assemble_enabled_plugins(
                    root,
                    require_trust=False,
                    include_user=False,
                    include_local=False,
                    agent_worktrees_command=args.agent_worktrees_path,
                )
            except ValueError as exc:
                print(f"error: {exc}", file=sys.stderr)
                return 2
    else:
        repository_projection_sources = None

    plugin_dirs = list(args.include_plugins)
    if args.include_installed:
        plugin_dirs.append(str(Path.home() / ".copilot" / "installed-plugins"))
    for directory in plugin_dirs:
        path = Path(directory).expanduser().resolve()
        if path.is_dir():
            sources += _sources_from_raw_dir(path)
        else:
            print(
                f"warning: --include-plugins {path} is not a directory (skipped)",
                file=sys.stderr,
            )

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
        scan_session_context(root, sources, report) if args.from_settings else None
    )
    budget = (
        build_context_budget(
            root,
            sources,
            owned_agent_roots=owned_agent_roots,
            capture_dynamic=args.capture_dynamic,
        )
        if args.context_budget
        else None
    )

    if args.json:
        payload = {
            "root": str(root),
            "blocking": report.blocking,
            "total": len(report.findings),
            "findings": [asdict(finding) for finding in report.findings],
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
            for finding in sorted(
                report.findings,
                key=lambda item: (order.get(item.severity, 9), item.check),
            ):
                tag = "BLOCK" if finding.severity == BLOCKING else "WARN "
                print(f"[{tag}] {finding.check}: {finding.path}\n        {finding.message}")
            print(
                f"\n{report.blocking} blocking, "
                f"{len(report.findings) - report.blocking} warning(s)",
            )
        if session_context is not None:
            _print_session_context(session_context)
        if budget is not None:
            _print_context_budget(budget)

    if args.strict and report.blocking:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
