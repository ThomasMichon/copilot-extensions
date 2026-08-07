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
  4. anti-recursion      -- an agent that declares `mcp-servers` also carries an
                            MCP-readiness probe and an anti-self-delegation line.
  5. secrets             -- a secret-looking key is assigned a literal value
                            (not an env-var / placeholder) in a scanned file.
  6. raw IPs             -- an ssh/scp/rsync command targets a raw IPv4 literal
                            instead of a configured alias.

Usage:
    scan-customizations.py [REPO_ROOT] [--json] [--strict]
                           [--from-settings]
                           [--include-plugins DIR ...] [--include-installed]

`REPO_ROOT` defaults to the current directory. `--from-settings` assembles the
plugin set **actually loaded for this repo** -- from its
`.github/copilot/settings.json` (+ user settings) `enabledPlugins` /
`extraKnownMarketplaces` -- and brings each into scope: an in-repo `directory`
marketplace plugin (e.g. `./.ai`) is **owned** (fully checked), while an
external marketplace plugin is **reference-only** (its skills join the collision
map, and a collision that touches it is annotated with a fix pointer + upstream
`source`). `--include-plugins` / `--include-installed` still add raw
installed-plugin trees (layout `<root>/<marketplace>/<plugin>/skills/...`) the
same reference-only way. Exit code is 0 unless `--strict` is given and at least
one BLOCKING finding was reported.
"""

from __future__ import annotations

import argparse
import json
import re
import os
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

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
# Anti-self-delegation intent -- matched against a whitespace-collapsed body so
# it survives line wrapping. "do not ... (task tool|spawn|delegate)" within a
# short window; deliberately lenient (a false negative is safer than crying wolf).
ANTI_DELEGATE = re.compile(r"(?i)do\s*not\b.{0,80}?(task\s*tool|spawn|delegate)\b")
MCP_READINESS = re.compile(r"(?i)mcp[\s_-]*readiness|readiness\s+(check|probe)")

CONFIG_SUFFIXES = {".json", ".yaml", ".yml", ".toml", ".psd1", ".env", ".ini", ".conf"}
# Heavy / irrelevant trees to skip when walking a large monorepo.
PRUNE_DIRS = {
    ".git", "node_modules", ".venv", "venv", "dist", "build", "__pycache__",
    "logs", ".mypy_cache", ".pytest_cache", "target", ".idea", "site-packages",
}


@dataclass
class Finding:
    severity: str
    check: str
    path: str
    message: str


@dataclass
class PluginSource:
    """A plugin whose skills join the review, with ownership + source origin.

    ``controlled`` is True when the repo under review OWNS the plugin (its own
    in-repo ``.ai`` directory-marketplace plugins, or its ``plugins/*`` suite):
    those get full checks and their findings are actionable in-repo. When False
    the plugin is **external** (installed from another marketplace) -- its skills
    are reference-only for collision detection, and a collision that touches it
    carries a remediation pointer (fix in-repo, or upstream at ``source``).
    """

    skills_root: Path
    origin: str                # "<marketplace>/<plugin>" label
    controlled: bool = False
    source: str = ""           # upstream repo URL for an external plugin ("" if in-repo/unknown)


def _load_json(path: Path) -> dict:
    """Best-effort JSON load (settings/manifests); returns {} on any problem."""
    try:
        data = json.loads(path.read_text(encoding="utf-8", errors="replace"))
        return data if isinstance(data, dict) else {}
    except Exception:
        return {}


def _merged_settings(repo_root: Path) -> tuple[dict, dict]:
    """Merge the settings that decide a repo's *loaded* plugin set.

    Reads the repo's committed ``.github/copilot/settings.json`` (and the
    ``.claude/settings.json`` fallback) plus the user ``~/.copilot/settings.json``,
    and returns ``(enabled_plugins, marketplaces)``. Repo settings take
    precedence over user settings for a marketplace of the same name; a plugin
    is *enabled* if either layer enables it.
    """
    layers = [
        Path.home() / ".copilot" / "settings.json",
        repo_root / ".claude" / "settings.json",
        repo_root / ".github" / "copilot" / "settings.json",
    ]
    enabled: dict[str, bool] = {}
    marketplaces: dict[str, dict] = {}
    for p in layers:                       # later layers win (repo over user)
        data = _load_json(p)
        ep = data.get("enabledPlugins")
        if isinstance(ep, dict):
            for k, v in ep.items():
                if v:
                    enabled[str(k)] = True
        mk = data.get("extraKnownMarketplaces")
        if isinstance(mk, dict):
            for k, v in mk.items():
                if isinstance(v, dict):
                    marketplaces[str(k)] = v
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


def assemble_enabled_plugins(
    repo_root: Path, installed_root: Path | None = None,
) -> list[PluginSource]:
    """Assemble the plugin set *actually loaded for this repo* into review scope.

    Resolves the repo's + user's ``settings.json`` ``enabledPlugins`` /
    ``extraKnownMarketplaces`` into concrete :class:`PluginSource` entries: each
    enabled ``<name>@<marketplace>`` mapped to its skills footprint (an in-repo
    ``directory`` marketplace path, else the installed-plugins tree) and
    classified ``controlled`` (in-repo) vs external (with an upstream ``source``).
    Only plugins whose skills footprint exists are returned. Never raises.
    """
    if installed_root is None:
        installed_root = Path.home() / ".copilot" / "installed-plugins"
    enabled, marketplaces = _merged_settings(repo_root)
    out: list[PluginSource] = []
    for key in sorted(enabled):
        name, _, mkt = key.partition("@")
        name = name.strip()
        mkt = mkt.strip()
        if not name:
            continue
        origin = f"{mkt}/{name}" if mkt else name
        src = (marketplaces.get(mkt) or {}).get("source") or {}
        src_kind = str(src.get("source", "")).strip().lower() if isinstance(src, dict) else ""

        if src_kind == "directory":
            # An in-repo local marketplace (e.g. ./.ai): the repo owns it.
            rel = str(src.get("path", "")).strip() if isinstance(src, dict) else ""
            base = (repo_root / rel).resolve() if rel else repo_root
            footprint = base / name
            try:
                controlled = repo_root.resolve() in footprint.parents or footprint == repo_root.resolve()
            except Exception:
                controlled = False
            skills_root = footprint / "skills"
            source_url = ""  # in-repo; fixable here
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

        if skills_root.is_dir() and any(skills_root.glob("*/SKILL.md")):
            out.append(PluginSource(
                skills_root=skills_root, origin=origin,
                controlled=controlled, source=source_url,
            ))
    return out



@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)

    def add(self, severity: str, check: str, path: Path | str, message: str) -> None:
        self.findings.append(Finding(severity, check, str(path), message))

    @property
    def blocking(self) -> int:
        return sum(1 for f in self.findings if f.severity == BLOCKING)


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
    owned += sorted(root.glob("plugins/*/skills/*/SKILL.md"))
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
    for ps in (plugin_sources or []):
        for sf in sorted(ps.skills_root.glob("*/SKILL.md")):
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



def scan_agents(root: Path, report: Report) -> None:
    agent_files = sorted(root.glob(".github/agents/*.agent.md"))
    agent_files += sorted(root.glob("plugins/*/agents/*.agent.md"))
    for af in agent_files:
        text = af.read_text(encoding="utf-8", errors="replace")
        fm = split_frontmatter(text)
        if fm is None:
            report.add(BLOCKING, "agent-frontmatter", af,
                       ".agent.md has no YAML frontmatter (--- block)")
            continue
        frontmatter, body = fm
        if "description" not in frontmatter.lower():
            report.add(BLOCKING, "agent-frontmatter", af,
                       "frontmatter missing `description`")
        if re.search(r"(?im)^\s*mcp-servers\s*:", frontmatter):
            flat = re.sub(r"\s+", " ", body)
            has_readiness = bool(MCP_READINESS.search(flat))
            has_anti = bool(ANTI_DELEGATE.search(flat))
            if not has_readiness:
                report.add(BLOCKING, "anti-recursion", af,
                           "declares mcp-servers but has no MCP-readiness section "
                           "(probe one tool on startup; report and stop on failure)")
            if not has_anti:
                report.add(BLOCKING, "anti-recursion", af,
                           "declares mcp-servers but has no anti-self-delegation "
                           "line (\"do NOT spawn another <agent> agent\")")


def _walk_customization_files(root: Path):
    """Yield customization-surface files, pruning heavy/irrelevant trees."""
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [
            d for d in dirnames
            if d not in PRUNE_DIRS and not (d.startswith(".") and d != ".github")
        ]
        for fn in filenames:
            yield Path(dirpath) / fn


def scan_text_files(root: Path, report: Report) -> None:
    for p in _walk_customization_files(root):
        name = p.name
        suffix = p.suffix.lower()
        parts = set(p.parts)
        under_github = ".github" in parts
        is_mcp = name in (".mcp.json", "mcp-config.json")
        is_surface_md = name == "SKILL.md" or name.endswith(".agent.md") or name == "AGENTS.md"
        # Secrets: only config-shaped files that belong to a customization surface.
        config_target = suffix in CONFIG_SUFFIXES and (
            under_github or is_mcp or "plugins" in parts
        )
        # Raw IPs: surface markdown + those same config files.
        if not (config_target or is_surface_md):
            continue
        try:
            lines = p.read_text(encoding="utf-8", errors="replace").splitlines()
        except OSError:
            continue
        for n, line in enumerate(lines, 1):
            if config_target:
                sm = SECRET_KEY.search(line)
                if sm:
                    val = sm.group("val").strip().strip(",")
                    token = val.split()[0] if val.split() else val
                    if not SAFE_VALUE.match(val) and CREDENTIAL_SHAPE.match(token):
                        report.add(BLOCKING, "secret", f"{p}:{n}",
                                   f"possible hardcoded secret: {sm.group(1)} = {token[:24]}")
            im = SSH_RAW_IP.search(line)
            if im:
                ip = im.group("ip")
                window = "\n".join(lines[max(0, n - 4):n])
                if (not ip.startswith(("0.", "127.", "255."))
                        and not NEGATIVE_EXAMPLE.search(window)):
                    report.add(WARNING, "raw-ip", f"{p}:{n}",
                               f"ssh/scp/rsync targets raw IP {ip} (use an alias)")


def _sources_from_raw_dir(root: Path) -> list[PluginSource]:
    """Convert a raw installed-plugins tree (``<root>/<mkt>/<plugin>/skills``)
    into external :class:`PluginSource` entries (reference-only, source read from
    each plugin manifest)."""
    out: list[PluginSource] = []
    for skills_root in sorted(root.glob("*/*/skills")):
        if not skills_root.is_dir():
            continue
        plugin_dir = skills_root.parent
        origin = f"{plugin_dir.parent.name}/{plugin_dir.name}"
        out.append(PluginSource(
            skills_root=skills_root, origin=origin,
            controlled=False, source=_plugin_repo_url(plugin_dir),
        ))
    return out


def run(root: Path, plugin_sources: list[PluginSource] | None = None) -> Report:
    report = Report()
    scan_skills(root, report, plugin_sources)
    scan_agents(root, report)
    scan_text_files(root, report)
    return report


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("root", nargs="?", default=".", help="repo root (default: .)")
    ap.add_argument("--json", action="store_true", help="emit findings as JSON")
    ap.add_argument("--strict", action="store_true",
                    help="exit non-zero if any BLOCKING finding is reported")
    ap.add_argument("--from-settings", action="store_true",
                    help="assemble the plugin set actually LOADED for this repo "
                         "(from .github/copilot/settings.json + user settings) and "
                         "bring each into scope -- in-repo plugins fully checked, "
                         "external ones reference-only + source-classified")
    ap.add_argument("--include-plugins", action="append", default=[], metavar="DIR",
                    help="installed-plugin tree (<root>/<marketplace>/<plugin>/skills/...) "
                         "whose skills join the collision map; repeatable")
    ap.add_argument("--include-installed", action="store_true",
                    help="shortcut for --include-plugins ~/.copilot/installed-plugins")
    args = ap.parse_args(argv)

    root = Path(args.root).resolve()
    if not root.is_dir():
        print(f"error: {root} is not a directory", file=sys.stderr)
        return 2

    sources: list[PluginSource] = []
    if args.from_settings:
        sources += assemble_enabled_plugins(root)

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

    report = run(root, sources)

    if args.json:
        print(json.dumps({
            "root": str(root),
            "blocking": report.blocking,
            "total": len(report.findings),
            "findings": [asdict(f) for f in report.findings],
        }, indent=2))
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

    if args.strict and report.blocking:
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
