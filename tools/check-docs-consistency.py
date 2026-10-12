#!/usr/bin/env python3
"""Guard against plugin drift across the repo's documentation.

Docs repeatedly re-count and re-list the plugins ("nine plugins", "the five
plugins", per-plugin skill tables). Those hand-maintained duplicates rot every
time a plugin or skill is added. This check derives the truth from the repo and
fails when a doc disagrees, so drift is caught at pre-push / CI instead of by a
reader months later.

Three checks (truth is `.github/plugin/marketplace.json` + `plugins/`):

  A. **README completeness** -- every plugin in the marketplace is linked from
     the root README (its plugin table / doc index).
  B. **Per-plugin skill lists** -- if a `plugins/<p>/README.md` enumerates skills
     (links `skills/<s>/SKILL.md`), that set must equal the plugin's actual
     skills on disk (no missing, no extra).
  C. **Count phrases** -- canonical count sentences ("<n> plugins, one
     marketplace", "How the <n> ... plugins fit together", "<n> ship a runtime",
     "<n> ... payload-only", "bundles <n> focused skills", "all <n>
     copilot-extensions plugins") must match the real counts.
  D. **Adopter navigation** -- primary reading pages exist, link to their
     next waypoints, and resolve local paths/heading anchors with exact casing.

Usage:  python tools/check-docs-consistency.py            # check
        python tools/check-docs-consistency.py --counts   # print derived counts
Exit 0 = consistent, 1 = drift (suitable for a pre-push hook / CI).
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
from pathlib import Path
from urllib.parse import unquote, urlsplit

REPO = Path(__file__).resolve().parent.parent
PLUGINS_DIR = REPO / "plugins"
MARKETPLACE = REPO / ".github" / "plugin" / "marketplace.json"
README = REPO / "README.md"

ADOPTER_PAGES = (
    "README.md", "docs/README.md", "docs/installation.md",
    "docs/capabilities.md", "docs/adoption.md", "docs/configuration.md",
    "docs/architecture.md", "docs/harness-runbook.md",
    "docs/reference-index.md", "docs/documentation-architecture.md",
)
READING_EDGES = {
    "README.md": ("docs/README.md", "docs/installation.md", "docs/capabilities.md",
                  "docs/adoption.md", "docs/reference-index.md"),
    "docs/README.md": ("README.md", "docs/installation.md", "docs/capabilities.md",
                       "docs/adoption.md", "docs/configuration.md",
                       "docs/architecture.md", "docs/harness-runbook.md",
                       "docs/reference-index.md"),
}


def prose(text: str) -> str:
    """Exclude examples and comments from executable navigation."""
    result = []
    fence = ""
    for line in text.splitlines():
        match = re.match(r"^\s*(`{3,}|~{3,})", line)
        if match:
            marker = match.group(1)
            if not fence:
                fence = marker
            elif marker[0] == fence[0] and len(marker) >= len(fence):
                fence = ""
            continue
        if not fence:
            result.append(line)
    return re.sub(r"<!--.*?-->", "", "\n".join(result), flags=re.S)


def local_links(text: str) -> list[str]:
    """Inline/reference Markdown links and HTML image/link destinations."""
    text = prose(text)
    text = re.sub(r"(`+).*?\1", "", text)
    inline = r"!?\[[^\]\n]*\]\(\s*<?([^\s)>]+)>?(?:\s+[^)]*)?\)"
    links = re.findall(inline, text)
    definitions = dict(re.findall(r"^\s*\[([^\]\n]+)\]:\s*<?([^\s>]+)>?", text, re.M))

    def reference_key(label: str) -> str:
        return " ".join(label.split()).casefold()

    definitions = {reference_key(key): value for key, value in definitions.items()}
    remaining = re.sub(r"^\s*\[[^\]\n]+\]:[^\n]*$", "", text, flags=re.M)
    remaining = re.sub(inline, "", remaining)

    def full_reference(match: re.Match) -> str:
        label, reference = match.groups()
        destination = definitions.get(reference_key(reference or label))
        if destination:
            links.append(destination)
        return ""

    remaining = re.sub(r"!?\[([^\]\n]+)\]\[([^\]\n]*)\]", full_reference, remaining)
    for reference in re.findall(r"!?\[([^\]\n]+)\]", remaining):
        destination = definitions.get(reference_key(reference))
        if destination:
            links.append(destination)
    links.extend(re.findall(r"<(?:a|img)\b[^>]*?\b(?:href|src)=[\"']([^\"']+)[\"']",
                            text, re.I))
    return [link for link in links if not urlsplit(link).scheme and not link.startswith("//")]


def heading_anchors(text: str) -> set[str]:
    text = prose(text)
    anchors = set(re.findall(r"<a\b[^>]*?\b(?:id|name)=[\"']([^\"']+)[\"']", text, re.I))
    seen: set[str] = set()
    for heading in re.findall(r"^ {0,3}#{1,6}\s+(.+?)\s*#*\s*$", text, re.M):
        heading = re.sub(r"\[([^\]]+)\]\([^)]+\)", r"\1", heading)
        heading = re.sub(r"</?(?:a|em|strong|code|span|img|br|sup|sub|b|i)\b[^>]*>",
                         "", heading, flags=re.I)
        base = re.sub(r"[^\w\- ]", "", html.unescape(heading).lower()).replace(" ", "-")
        anchor = base
        suffix = 0
        while anchor in seen:
            suffix += 1
            anchor = f"{base}-{suffix}"
        seen.add(anchor)
        anchors.add(anchor)
    return anchors


def exact_path(path: Path, repo: Path) -> bool:
    try:
        parts = path.relative_to(repo).parts
    except ValueError:
        return False
    current = repo
    for part in parts:
        if not current.is_dir() or part not in {entry.name for entry in current.iterdir()}:
            return False
        current = current / part
    return current.exists()


def check_adopter_navigation(
    repo: Path = REPO,
    pages: tuple[str, ...] = ADOPTER_PAGES,
    edges: dict[str, tuple[str, ...]] | None = None,
) -> list[str]:
    repo = repo.resolve()
    edges = READING_EDGES if edges is None else edges
    problems: list[str] = []
    anchor_cache: dict[Path, set[str]] = {}
    for name in pages:
        page = repo / name
        if not exact_path(page, repo):
            problems.append(f"  {name}: adopter page missing or wrong case")
            continue
        destinations: set[str] = set()
        for link in local_links(page.read_text(encoding="utf-8")):
            url = urlsplit(link)
            target = Path(os.path.abspath(page.parent / unquote(url.path))) if url.path else page
            if not exact_path(target, repo):
                problems.append(f"  {name}: local target missing/outside repo/wrong case: {link}")
                continue
            destinations.add(target.relative_to(repo).as_posix())
            if url.fragment:
                document = target / "README.md" if target.is_dir() else target
                if document.suffix.lower() != ".md" or not document.is_file():
                    problems.append(f"  {name}: anchor target is not a Markdown page: {link}")
                    continue
                if document not in anchor_cache:
                    anchor_cache[document] = heading_anchors(document.read_text(encoding="utf-8"))
                if unquote(url.fragment) not in anchor_cache[document]:
                    problems.append(f"  {name}: missing local heading/explicit anchor: {link}")
        for required in edges.get(name, ()):
            if required not in destinations:
                problems.append(f"  {name}: missing reading-path link to {required}")
    return problems

NUM2WORD = {
    1: "one", 2: "two", 3: "three", 4: "four", 5: "five", 6: "six", 7: "seven",
    8: "eight", 9: "nine", 10: "ten", 11: "eleven", 12: "twelve", 13: "thirteen",
    14: "fourteen", 15: "fifteen", 16: "sixteen", 17: "seventeen", 18: "eighteen",
    19: "nineteen", 20: "twenty",
}
WORD2NUM = {w: n for n, w in NUM2WORD.items()}


def plugins() -> list[str]:
    data = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
    return [p["name"] for p in data.get("plugins", [])]


def is_runtime(name: str) -> bool:
    """A runtime plugin ships a Python package (has a pyproject.toml)."""
    return (PLUGINS_DIR / name / "pyproject.toml").exists()


def skills_of(name: str) -> set[str]:
    d = PLUGINS_DIR / name / "skills"
    return {p.parent.name for p in d.glob("*/SKILL.md")} if d.is_dir() else set()


def _num(token: str) -> int | None:
    token = token.strip().lower()
    if token.isdigit():
        return int(token)
    return WORD2NUM.get(token)


def check_readme_completeness(names: list[str]) -> list[str]:
    text = README.read_text(encoding="utf-8")
    return [f"  README.md: plugin `{n}` is not linked (`](plugins/{n}/` absent)"
            for n in names if f"](plugins/{n}/" not in text]


def check_plugin_skill_lists(names: list[str]) -> list[str]:
    problems: list[str] = []
    for n in names:
        readme = PLUGINS_DIR / n / "README.md"
        if not readme.exists():
            continue
        listed = set(re.findall(r"skills/([a-z0-9-]+)/SKILL\.md", readme.read_text(encoding="utf-8")))
        if not listed:
            continue  # README doesn't enumerate skills -> nothing to drift
        actual = skills_of(n)
        missing = actual - listed
        extra = listed - actual
        if missing:
            problems.append(f"  plugins/{n}/README.md: skill table MISSING {sorted(missing)}")
        if extra:
            problems.append(f"  plugins/{n}/README.md: skill table lists non-existent {sorted(extra)}")
    return problems


COUNT_PATTERNS = [
    # (regex, kind)  -- kind resolves to an expected number
    (re.compile(r"\b([A-Za-z0-9]+)\s+plugins,\s+one\s+marketplace", re.I), "total"),
    (re.compile(r"How\s+the\s+([A-Za-z0-9]+)\s+copilot-extensions\s+plugins", re.I), "total"),
    (re.compile(r"\ball\s+([A-Za-z0-9]+)\s+copilot-extensions\s+plugins", re.I), "total"),
    (re.compile(r"\b([A-Za-z0-9]+)-plugin\s+(?:suite|marketplace)", re.I), "total"),
    (re.compile(r"\b([A-Za-z0-9]+)\s+ship\s+a\s+runtime", re.I), "runtime"),
    (re.compile(r"\b([A-Za-z0-9]+)\s+runtime\s+plugins", re.I), "runtime"),
    (re.compile(r"\b([A-Za-z0-9]+)\s+(?:are|is)\s+payload-only", re.I), "payload"),
    (re.compile(r"bundles\s+([A-Za-z0-9]+)\s+focused\s+skills", re.I), "cc_skills"),
]


def check_counts(expected: dict[str, int]) -> list[str]:
    problems: list[str] = []
    for md in sorted(REPO.glob("**/*.md")):
        if ".worktrees" in md.parts or "node_modules" in md.parts:
            continue
        raw = md.read_text(encoding="utf-8")
        # Strip markdown emphasis so "**five**" / "`five`" still match.
        text = raw.replace("*", "").replace("`", "")
        rel = md.relative_to(REPO)
        for rx, kind in COUNT_PATTERNS:
            for m in rx.finditer(text):
                got = _num(m.group(1))
                want = expected[kind]
                if got is not None and got != want:
                    line = text[: m.start()].count("\n") + 1
                    problems.append(
                        f"  {rel}:{line}: says {m.group(1)!r} for {kind} "
                        f"(expected {want}/{NUM2WORD.get(want, want)}): \"{m.group(0).strip()}\"")
    return problems


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--counts", action="store_true", help="print derived counts and exit")
    args = ap.parse_args()

    names = plugins()
    total = len(names)
    runtime = sum(is_runtime(n) for n in names)
    payload = total - runtime
    expected = {
        "total": total,
        "runtime": runtime,
        "payload": payload,
        "cc_skills": len(skills_of("customizing-copilot")),
    }

    if args.counts:
        for k, v in expected.items():
            print(f"{k}: {v}")
        print(f"runtime plugins: {sorted(n for n in names if is_runtime(n))}")
        print(f"payload plugins: {sorted(n for n in names if not is_runtime(n))}")
        return 0

    problems = (check_readme_completeness(names)
                + check_plugin_skill_lists(names)
                + check_counts(expected)
                + check_adopter_navigation())

    if problems:
        print(f"[FAIL] {len(problems)} docs-consistency problem(s):")
        print("\n".join(problems))
        print("\nFix the doc, or (better) de-count / point at the canonical list.")
        return 1
    print(f"[OK] docs consistent: {total} plugins ({runtime} runtime, {payload} payload-only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
