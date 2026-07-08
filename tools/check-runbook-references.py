#!/usr/bin/env python3
"""Check that a doc's skill/plugin references resolve against the live repo.

The Control-Harness Runbook (docs/harness-runbook.md) and the plugin skills
name a lot of *other* skills, plugins, and binstubs by identifier. As the suite
evolves -- a skill is renamed, a plugin is added or removed -- those references
silently rot. This is the static-conformance guard: it extracts every
identifier a doc uses **in a skill/plugin context** and asserts it exists.

Authoritative sets are built from the repo itself, so there is nothing to keep
in sync by hand:
  - known plugins  <- .github/plugin/marketplace.json  (plugins[].name)
  - known skills   <- plugins/*/skills/*/SKILL.md       (dir name == frontmatter name)

Reference extraction is deliberately *context-scoped* (a backticked identifier
next to the word "skill"/"plugin", an `@copilot-extensions` install token, or an
`enabledPlugins` key) so ordinary backticked prose (paths, flags, filenames)
does not produce false positives. Placeholders like `harness-<repo>` are ignored
(they are not plain identifiers).

Usage:
  python tools/check-runbook-references.py                 # checks the runbook
  python tools/check-runbook-references.py docs/foo.md ... # checks given docs
  python tools/check-runbook-references.py --list          # print known sets

Exit code 0 = all references resolve, 1 = one or more broken (suitable for a
pre-push hook or CI).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
MARKETPLACE = REPO / ".github" / "plugin" / "marketplace.json"
DEFAULT_DOCS = [REPO / "docs" / "harness-runbook.md"]

# A plain lowercase kebab identifier (a skill or plugin name shape).
IDENT = r"[a-z][a-z0-9]*(?:-[a-z0-9]+)+"

# Identifiers that are valid in a skill/plugin-adjacent context but are NOT
# repo skills or plugins -- built-in sub-agents and generic tools. Keeps the
# context regexes from false-failing if one is captured.
ALLOWLIST = {
    "rubber-duck", "code-review", "general-purpose", "security-review",
    "agent-worktrees-wsl-provision",  # real skill; also guards odd captures
    "copilot-extensions",  # the marketplace / repo name, not a plugin
}


def load_known_plugins() -> set[str]:
    data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    return {p["name"] for p in data.get("plugins", [])}


def load_known_skills() -> set[str]:
    skills: set[str] = set()
    for skill_md in PLUGINS_DIR.glob("*/skills/*/SKILL.md"):
        skills.add(skill_md.parent.name)
    return skills


def _idents(text: str) -> list[str]:
    """All plain kebab identifiers in a fragment of text (inside backticks)."""
    return re.findall(rf"`({IDENT})`", text)


def extract_skill_refs(text: str) -> set[str]:
    """Backticked identifiers used in a *skill* context."""
    refs: set[str] = set()
    # "... `x` skill" / "... `x` skills"
    refs.update(re.findall(rf"`({IDENT})`\**\s+skills?\b", text))
    # "skill: `x`" / "skills: `a`, `b`, `c`" -- capture the whole run after the
    # keyword up to a closing paren / newline / sentence end, then pull idents.
    for run in re.findall(r"skills?:\s*([^\n)]*)", text):
        refs.update(_idents(run))
    # "the `x` skill" already covered by the first rule.
    return refs


def extract_plugin_refs(text: str) -> set[str]:
    """Backticked / @-qualified identifiers used in a *plugin* context."""
    refs: set[str] = set()
    # "`x` plugin" / "`x` plugins"
    refs.update(re.findall(rf"`({IDENT})`\**\s+plugins?\b", text))
    # "plugin: `x`" / "plugins: `a`, `b`"
    for run in re.findall(r"plugins?:\s*([^\n)]*)", text):
        refs.update(_idents(run))
    # marketplace-qualified: "x@copilot-extensions" (install cmds, enabledPlugins)
    refs.update(re.findall(rf"({IDENT})@copilot-extensions", text))
    return refs


def check_doc(path: Path, plugins: set[str], skills: set[str]) -> list[str]:
    text = path.read_text(encoding="utf-8")
    problems: list[str] = []

    skill_refs = extract_skill_refs(text)
    plugin_refs = extract_plugin_refs(text)

    for name in sorted(skill_refs):
        if name in ALLOWLIST or name in skills:
            continue
        # A plugin name used with the word "skill" nearby (e.g. "the efforts
        # skill-plugin") is not a broken skill ref.
        if name in plugins:
            continue
        problems.append(f"  [skill] `{name}` -- no such skill under plugins/*/skills/")

    for name in sorted(plugin_refs):
        if name in ALLOWLIST or name in plugins:
            continue
        problems.append(f"  [plugin] `{name}@copilot-extensions` -- not in marketplace.json")

    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("docs", nargs="*", type=Path,
                    help="doc files to check (default: docs/harness-runbook.md)")
    ap.add_argument("--list", action="store_true",
                    help="print the known plugin/skill sets and exit")
    args = ap.parse_args()

    plugins = load_known_plugins()
    skills = load_known_skills()

    if args.list:
        print(f"known plugins ({len(plugins)}): {', '.join(sorted(plugins))}")
        print(f"known skills  ({len(skills)}): {', '.join(sorted(skills))}")
        return 0

    docs = args.docs or DEFAULT_DOCS
    total = 0
    for doc in docs:
        if not doc.exists():
            print(f"[FAIL] {doc}: file not found")
            total += 1
            continue
        problems = check_doc(doc, plugins, skills)
        resolved = doc.resolve()
        rel = resolved.relative_to(REPO) if REPO in resolved.parents else doc
        if problems:
            print(f"[FAIL] {rel}: {len(problems)} broken reference(s)")
            print("\n".join(problems))
            total += len(problems)
        else:
            print(f"[OK]   {rel}: all skill/plugin references resolve")

    if total:
        print(f"\n{total} broken reference(s). Fix the doc or the name it cites.")
        return 1
    print(f"\nAll references resolve ({len(plugins)} plugins, {len(skills)} skills known).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
