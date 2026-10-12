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

Install the dev-only parser: python -m pip install -r tools/requirements-docs.txt
Usage:  python tools/check-docs-consistency.py            # check
        python tools/check-docs-consistency.py --counts   # print derived counts
Exit 0 = consistent, 1 = drift (suitable for a pre-push hook / CI).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sys
from functools import lru_cache
from html.parser import HTMLParser
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
    "docs/installation.md": ("docs/capabilities.md",),
    "docs/capabilities.md": ("docs/adoption.md",),
    "docs/adoption.md": ("docs/configuration.md",),
    "docs/configuration.md": ("docs/architecture.md",),
    "docs/architecture.md": ("docs/harness-runbook.md",),
}


@lru_cache(maxsize=1)
def markdown_parser():
    try:
        from markdown_it import MarkdownIt
    except ImportError as exc:
        raise RuntimeError(
            "Install documentation tooling: python -m pip install -r tools/requirements-docs.txt"
        ) from exc
    return MarkdownIt("commonmark")


class HtmlTargets(HTMLParser):
    def __init__(self, include_images: bool = True):
        super().__init__(convert_charrefs=True)
        self.include_images = include_images
        self.links: list[str] = []
        self.anchors: set[str] = set()

    def handle_starttag(self, tag, attrs):
        values = dict(attrs)
        if tag == "a" and values.get("href"):
            self.links.append(values["href"])
        if tag == "img" and self.include_images and values.get("src"):
            self.links.append(values["src"])
        if values.get("id"):
            self.anchors.add(values["id"])
        if tag == "a" and values.get("name"):
            self.anchors.add(values["name"])


def html_targets(text: str, include_images: bool = True) -> HtmlTargets:
    parser = HtmlTargets(include_images)
    parser.feed(text)
    parser.close()
    return parser


def local_links(text: str, include_images: bool = True) -> list[str]:
    """Collect rendered CommonMark links/assets, not code or definition text."""
    links: list[str] = []
    for token in markdown_parser().parse(text):
        if token.type == "html_block":
            links.extend(html_targets(token.content, include_images).links)
        for child in token.children or ():
            if child.type == "link_open":
                links.append(child.attrGet("href"))
            elif child.type == "image" and include_images:
                links.append(child.attrGet("src"))
            elif child.type == "html_inline":
                links.extend(html_targets(child.content, include_images).links)
    return [link for link in links if not urlsplit(link).scheme and not link.startswith("//")]


def rendered_heading(children) -> str:
    parts = []
    for child in children or ():
        if child.type in {"text", "code_inline"}:
            parts.append(child.content)
        elif child.type in {"softbreak", "hardbreak"}:
            parts.append(" ")
        elif child.type == "image":
            parts.append(rendered_heading(child.children))
    return "".join(parts)


def heading_anchors(text: str) -> set[str]:
    tokens = markdown_parser().parse(text)
    anchors: set[str] = set()
    seen: set[str] = set()
    for index, token in enumerate(tokens):
        if token.type == "html_block":
            anchors.update(html_targets(token.content).anchors)
        for child in token.children or ():
            if child.type == "html_inline":
                anchors.update(html_targets(child.content).anchors)
        if token.type != "heading_open":
            continue
        heading = rendered_heading(tokens[index + 1].children)
        base = re.sub(r"[^\w\- ]", "", heading.lower()).replace(" ", "-")
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
        path.resolve(strict=True).relative_to(repo)
    except (ValueError, OSError, RuntimeError):
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
        text = page.read_text(encoding="utf-8")
        navigation = set(local_links(text, include_images=False))
        for link in local_links(text):
            url = urlsplit(link)
            target = Path(os.path.abspath(page.parent / unquote(url.path))) if url.path else page
            if not exact_path(target, repo):
                problems.append(f"  {name}: local target missing/outside repo/wrong case: {link}")
                continue
            if link in navigation:
                destinations.add(target.relative_to(repo).as_posix())
            if url.fragment:
                document = target / "README.md" if target.is_dir() else target
                if document.suffix.lower() != ".md" or not exact_path(document, repo):
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

    try:
        navigation = check_adopter_navigation()
    except RuntimeError as exc:
        print(f"[FAIL] {exc}")
        return 1

    problems = (check_readme_completeness(names)
                + check_plugin_skill_lists(names)
                + check_counts(expected)
                + navigation)

    if problems:
        print(f"[FAIL] {len(problems)} docs-consistency problem(s):")
        print("\n".join(problems))
        print("\nFix the doc, or (better) de-count / point at the canonical list.")
        return 1
    print(f"[OK] docs consistent: {total} plugins ({runtime} runtime, {payload} payload-only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
