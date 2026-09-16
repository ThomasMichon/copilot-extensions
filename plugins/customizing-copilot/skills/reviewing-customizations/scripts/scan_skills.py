from __future__ import annotations

import re
from pathlib import Path

from scan_plugin_sources import PluginSource

BLOCKING = "blocking"
WARNING = "warning"

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
        prefix = text[clause_start + 1 : match.start()]
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


def split_frontmatter(text: str) -> tuple[str, str] | None:
    """Return (frontmatter, body) if the file opens with a --- YAML block."""
    if not text.startswith("---"):
        return None
    match = re.match(r"^---\s*\n(.*?)\n---\s*\n?(.*)$", text, re.DOTALL)
    if not match:
        return None
    return match.group(1), match.group(2)


def _dedup(items: list[str]) -> list[str]:
    """Case-insensitive dedup preserving first-seen order."""
    seen: set[str] = set()
    out: list[str] = []
    for token in items:
        key = token.strip().lower()
        if key and key not in seen:
            seen.add(key)
            out.append(token.strip())
    return out


def get_field_block(frontmatter: str, key: str) -> str:
    """Return a field's value including a multi-line YAML block/folded scalar."""
    lines = frontmatter.splitlines()
    for index, line in enumerate(lines):
        match = re.match(rf"(?i)^{re.escape(key)}\s*:\s*(.*)$", line)
        if not match:
            continue
        first = match.group(1).strip()
        if first in ("|", ">", "|-", ">-", "|+", ">+", ""):
            block: list[str] = []
            for cont in lines[index + 1 :]:
                if cont.strip() == "" or re.match(r"^\s", cont):
                    block.append(cont)
                else:
                    break
            return "\n".join(block)
        return first
    return ""


def extract_triggers(frontmatter: str) -> list[str]:
    """Pull structured trigger phrases from a Trigger phrases include block."""
    idx = frontmatter.lower().find("trigger phrases")
    if idx == -1:
        return []
    tail = frontmatter[idx:]
    triggers: list[str] = []
    for match in re.finditer(r"-\s*['\"]([^'\"]+)['\"]", tail):
        triggers.append(match.group(1).strip())
    for line in tail.splitlines()[1:]:
        match = re.match(r"\s*-\s+(?!['\"])(.+?)\s*$", line)
        if match:
            triggers.append(match.group(1).strip())
    return _dedup(triggers)


def extract_prose_triggers(frontmatter: str) -> list[str]:
    """Quoted multi-word trigger phrases embedded in a prose description."""
    desc = get_field_block(frontmatter, "description")
    if not desc:
        return []
    out: list[str] = []
    for match in re.finditer(
        r"['\"\u201c]([A-Za-z][^'\"\u201c\u201d]{3,60})['\"\u201d]",
        desc,
    ):
        phrase = match.group(1).strip()
        if " " in phrase:
            out.append(phrase)
    return _dedup(out)


def _plugin_origin(sf: Path) -> str:
    """Infer <marketplace>/<plugin> (or <plugin>) from a skill path."""
    parts = sf.parts
    try:
        skills_index = len(parts) - 1 - parts[::-1].index("skills")
    except ValueError:
        return ""
    plugin = parts[skills_index - 1] if skills_index - 1 >= 0 else ""
    marketplace = parts[skills_index - 2] if skills_index - 2 >= 0 else ""
    return f"{marketplace}/{plugin}" if marketplace and plugin else plugin


def get_field(frontmatter: str, key: str) -> str | None:
    match = re.search(rf"(?im)^{re.escape(key)}\s*:\s*(.*)$", frontmatter)
    return match.group(1).strip() if match else None


def has_mcp_troubleshooting_skill(plugin_root: Path) -> bool:
    """Whether a plugin ships a discoverable skill for its MCP failure path."""
    for skill_file in sorted(plugin_root.glob("skills/*/SKILL.md")):
        text = skill_file.read_text(encoding="utf-8", errors="replace")
        frontmatter_body = split_frontmatter(text)
        if frontmatter_body is None:
            continue
        frontmatter, _ = frontmatter_body
        identity = " ".join(
            filter(
                None,
                (
                    get_field(frontmatter, "name"),
                    get_field_block(frontmatter, "description"),
                ),
            )
        )
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
            section_start + next_heading.start() if next_heading else len(text)
        )
        if text[section_start:section_end].strip():
            return True
    return False


def _check_owned_skill(sf: Path, report, frontmatter: str) -> str:
    """Run the owned-skill frontmatter/name checks; return the skill's name."""
    name = get_field(frontmatter, "name")
    desc = "description" in frontmatter.lower()
    if not name:
        report.add(BLOCKING, "skill-frontmatter", sf, "frontmatter missing `name`")
    if not desc:
        report.add(BLOCKING, "skill-frontmatter", sf, "frontmatter missing `description`")
    folder = sf.parent.name
    if name and name != folder:
        report.add(
            BLOCKING,
            "name-folder-match",
            sf,
            f"skill `name: {name}` != folder `{folder}`",
        )
    structured = extract_triggers(frontmatter)
    if not structured:
        report.add(
            WARNING,
            "skill-triggers",
            sf,
            "description advertises no structured trigger phrases "
            "(`Trigger phrases include:` list)",
        )
    return name or folder


def scan_skills(
    root: Path,
    report,
    plugin_sources: list[PluginSource] | None = None,
) -> None:
    trigger_owner: dict[str, set[str]] = {}
    owner_meta: dict[str, tuple[bool, str]] = {}

    owned = sorted(root.glob(".github/skills/*/SKILL.md"))
    suite_owned = sorted(root.glob("plugins/*/skills/*/SKILL.md"))
    owned += suite_owned
    owned_plugin_skills = {
        (sf.parent.parent.parent.name, sf.parent.name) for sf in suite_owned
    }
    for sf in owned:
        text = sf.read_text(encoding="utf-8", errors="replace")
        frontmatter_body = split_frontmatter(text)
        if frontmatter_body is None:
            report.add(
                BLOCKING,
                "skill-frontmatter",
                sf,
                "SKILL.md has no YAML frontmatter (--- block)",
            )
            continue
        frontmatter, _ = frontmatter_body
        name = _check_owned_skill(sf, report, frontmatter)
        for trigger in _dedup(
            extract_triggers(frontmatter) + extract_prose_triggers(frontmatter)
        ):
            trigger_owner.setdefault(trigger.lower(), set()).add(name)

    for ps in sorted(plugin_sources or [], key=lambda item: not item.controlled):
        for sf in sorted(ps.skills_root.glob("*/SKILL.md")):
            plugin_name = ps.origin.rsplit("/", 1)[-1]
            logical_key = (plugin_name, sf.parent.name)
            if not ps.controlled and logical_key in owned_plugin_skills:
                continue
            frontmatter_body = split_frontmatter(
                sf.read_text(encoding="utf-8", errors="replace")
            )
            if frontmatter_body is None:
                continue
            frontmatter, _ = frontmatter_body
            if ps.controlled:
                label = _check_owned_skill(sf, report, frontmatter)
            else:
                name = get_field(frontmatter, "name") or sf.parent.name
                label = f"{name} [{ps.origin}]"
                owner_meta[label] = (False, ps.source)
            for trigger in _dedup(
                extract_triggers(frontmatter) + extract_prose_triggers(frontmatter)
            ):
                trigger_owner.setdefault(trigger.lower(), set()).add(label)
            if ps.controlled:
                owned_plugin_skills.add(logical_key)

    for phrase, owners in sorted(trigger_owner.items()):
        unique = sorted(owners)
        if len(unique) <= 1:
            continue
        message = f"trigger '{phrase}' claimed by: {', '.join(unique)}"
        externals = [
            owner for owner in unique if owner in owner_meta and owner_meta[owner][0] is False
        ]
        if externals:
            sources = sorted({owner_meta[owner][1] for owner in externals if owner_meta[owner][1]})
            where = f" (source: {', '.join(sources)})" if sources else ""
            message += (
                f"  --  involves plugin(s) OUTSIDE this repo's control{where}. "
                "Fix in-repo (reclaim the phrase with a local authority-override "
                "skill, or disable the plugin), OR file an issue/PR upstream -- "
                "if a `<repo>-harness` plugin is enabled for that source, use "
                "its contributing-to-<repo> skill (e.g. "
                "copilot-extensions-harness -> "
                "contributing-to-copilot-extensions)."
            )
        report.add(WARNING, "trigger-collision", ".github/skills", message)
