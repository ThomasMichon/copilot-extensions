#!/usr/bin/env python3
"""Print a compact, read-only digest of an effort for targeted readback.

The effort README stays the authority. This digest only tells an agent where
to look: an outline with line ranges and sizes, the open Plan and Validation
Plan items, the latest journal entries and, with ``--since``, the sections and
files that changed since a git revision. Read the named line ranges with a
ranged file view instead of re-reading the whole effort.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path

HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
FENCE = re.compile(r"^\s*(`{3,}|~{3,})(.*)$")
OPEN_ITEM = re.compile(r"^\s*[-*+]\s+\[ \]\s+(.*)$")
DATE = re.compile(r"\d{4}-\d{2}-\d{2}")
PLAN_SECTIONS = ("plan", "validation plan")
JOURNAL_SECTION = "journal"
ITEM_TEXT_LIMIT = 200


class DigestError(Exception):
    pass


@dataclass
class Section:
    level: int
    title: str
    path: tuple[str, ...]
    start: int  # 1-based heading line
    end: int  # 1-based last line, inclusive
    body: list[str] = field(default_factory=list)
    levels: tuple[int, ...] = ()

    @property
    def area(self) -> str | None:
        """Lowercased title of the enclosing H2 (or deeper) heading, below any H1 title."""
        for level, title in zip(self.levels, self.path):
            if level >= 2:
                return title.lower()
        return None

    @property
    def area_depth(self) -> int:
        """Heading depth below the area heading (0 for the area heading itself)."""
        deep = [level for level in self.levels if level >= 2]
        return len(deep) - 1

    @property
    def label(self) -> str:
        """Heading path below any H1 document title."""
        parts = [t for lv, t in zip(self.levels, self.path) if lv >= 2]
        return " > ".join(parts) or self.title

    @property
    def size(self) -> int:
        return sum(len(line.encode("utf-8")) + 1 for line in self.body)

    @property
    def content(self) -> list[str]:
        return self.body[1:] if self.level else self.body


def fenced_lines(lines: list[str]) -> list[bool]:
    """Mark fence delimiters and fenced content.

    A fence closes only on the same character repeated at least as many times
    as the opener with nothing after it, so a longer outer fence can quote an
    inner triple-backtick example.
    """
    mask: list[bool] = []
    fence: tuple[str, int] | None = None
    for line in lines:
        match = FENCE.match(line)
        if fence is None:
            if match and not (match.group(1)[0] == "`" and "`" in match.group(2)):
                fence = (match.group(1)[0], len(match.group(1)))
                mask.append(True)
            else:
                mask.append(False)
            continue
        mask.append(True)
        if (
            match
            and match.group(1)[0] == fence[0]
            and len(match.group(1)) >= fence[1]
            and not match.group(2).strip()
        ):
            fence = None
    return mask


def parse_sections(text: str) -> list[Section]:
    """Split Markdown into heading sections, ignoring headings in code fences."""
    lines = text.splitlines()
    sections: list[Section] = []
    stack: list[tuple[int, str]] = []
    current: Section | None = None
    preamble = Section(0, "(preamble)", ("(preamble)",), 1, 0)
    for number, (line, fenced) in enumerate(zip(lines, fenced_lines(lines)), start=1):
        match = None if fenced else HEADING.match(line)
        if match:
            level = len(match.group(1))
            title = match.group(2).strip()
            while stack and stack[-1][0] >= level:
                stack.pop()
            stack.append((level, title))
            if current is not None:
                current.end = number - 1
            current = Section(
                level, title, tuple(t for _, t in stack), number, number,
                levels=tuple(lv for lv, _ in stack),
            )
            sections.append(current)
        (current if current is not None else preamble).body.append(line)
    if current is not None:
        current.end = len(lines)
    if preamble.body:
        preamble.end = len(preamble.body)
        sections.insert(0, preamble)
    return sections


def open_items(sections: list[Section]) -> list[dict]:
    """Unchecked items under the Plan and Validation Plan headings."""
    items: list[dict] = []
    for section in sections:
        if section.area not in PLAN_SECTIONS:
            continue
        for offset, (line, fenced) in enumerate(zip(section.body, fenced_lines(section.body))):
            match = None if fenced else OPEN_ITEM.match(line)
            if match:
                text = match.group(1).strip()
                if len(text) > ITEM_TEXT_LIMIT:
                    text = text[: ITEM_TEXT_LIMIT - 1] + "…"
                items.append(
                    {
                        "line": section.start + offset,
                        "section": section.label,
                        "text": text,
                    }
                )
    return items


def _entry_date(title: str) -> str:
    match = DATE.search(title)
    return match.group(0) if match else ""


def _newest_first(entries: list[dict]) -> list[dict]:
    """Order entries newest first by their dated titles.

    The document's overall direction (prepended or appended) orders entries
    that share a date or carry none; undated entries follow dated ones.
    """
    dates = [d for d in (_entry_date(e["title"]) for e in entries) if d]
    ordered = entries if len(dates) >= 2 and dates[0] > dates[-1] else entries[::-1]
    return sorted(ordered, key=lambda e: _entry_date(e["title"]), reverse=True)


def _sidecar_entries(path: Path, rel: str) -> list[dict]:
    """Top-level entries of an extracted ``journal.md`` sidecar."""
    sections = parse_sections(path.read_text(encoding="utf-8"))
    nested = any(s.area == JOURNAL_SECTION and s.area_depth == 0 for s in sections)
    depth = 1 if nested else 0
    return [
        {"file": rel, "line": s.start, "title": s.title, "bytes": s.size}
        for s in sections
        if s.level >= 2 and s.area_depth == depth and (not nested or s.area == JOURNAL_SECTION)
    ]


def journal_entries(readme: Path, sections: list[Section], limit: int) -> dict:
    """Latest journal entries (README inline and ``journal.md``) and ``journal/`` files."""
    entries = [
        {"file": readme.name, "line": s.start, "title": s.title, "bytes": s.size}
        for s in sections
        if s.area == JOURNAL_SECTION and s.area_depth == 1
    ]
    sidecar = readme.parent / "journal.md"
    if sidecar.is_file() and sidecar != readme:
        entries += _sidecar_entries(sidecar, sidecar.name)
    files: list[dict] = []
    journal_dir = readme.parent / "journal"
    if journal_dir.is_dir():
        for path in journal_dir.rglob("*.md"):
            rel = path.relative_to(readme.parent).as_posix()
            files.append({"file": rel, "title": rel, "bytes": path.stat().st_size})
        files.sort(key=lambda entry: entry["file"])
    latest_files = [
        {"file": e["file"], "bytes": e["bytes"]} for e in _newest_first(files)[:limit]
    ]
    return {
        "entries_total": len(entries),
        "latest": _newest_first(entries)[:limit],
        "files_total": len(files),
        "files_latest": latest_files,
    }


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(cwd), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
    )


def changes_since(readme: Path, sections: list[Section], rev: str) -> dict:
    """Sections of the README and files of the effort folder changed since ``rev``."""
    folder = readme.parent.resolve()
    top = _git(folder, "rev-parse", "--show-toplevel")
    if top.returncode != 0:
        raise DigestError(f"--since needs a git checkout: {folder}")
    root = Path(top.stdout.strip()).resolve()
    if _git(root, "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}").returncode != 0:
        raise DigestError(f"unknown git revision: {rev}")
    folder_rel = folder.relative_to(root).as_posix()
    readme_rel = readme.resolve().relative_to(root).as_posix()

    files = []
    diff = _git(root, "diff", "--name-status", rev, "--", folder_rel)
    for line in diff.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) >= 2:
            files.append({"status": parts[0], "file": parts[-1]})
    untracked = _git(root, "ls-files", "--others", "--exclude-standard", "--", folder_rel)
    files.extend({"status": "?", "file": name} for name in untracked.stdout.splitlines() if name)

    old = _git(root, "show", f"{rev}:{readme_rel}")
    # Match by heading path, keeping repeated sibling headings as distinct
    # occurrences: unchanged copies pair first, then leftovers pair in order.
    remaining: dict[tuple[str, ...], list[list[str]]] = {}
    if old.returncode == 0:
        for section in parse_sections(old.stdout):
            remaining.setdefault(section.path, []).append(section.content)
    pending = []
    for section in sections:
        previous = remaining.get(section.path, [])
        if section.content in previous:
            previous.remove(section.content)
        else:
            pending.append(section)
    changed = []
    for section in pending:
        previous = remaining.get(section.path)
        changed.append(
            {
                "state": "changed" if previous else "added",
                "section": section.label,
                "lines": [section.start, section.end],
                "bytes": section.size,
            }
        )
        if previous:
            previous.pop(0)
    removed = [" > ".join(path) for path, left in remaining.items() for _ in left]
    return {
        "rev": rev,
        "readme_existed": old.returncode == 0,
        "files": files,
        "sections": changed,
        "removed_sections": removed,
    }


def build_digest(target: Path, since: str | None = None, journal_limit: int = 3) -> dict:
    readme = target / "README.md" if target.is_dir() else target
    if not readme.is_file():
        raise DigestError(f"effort README not found: {readme}")
    text = readme.read_text(encoding="utf-8")
    sections = parse_sections(text)
    digest = {
        "readme": str(readme),
        "bytes": len(text.encode("utf-8")),
        "lines": len(text.splitlines()),
        # Journal entries are listed under "journal", not repeated in the outline.
        "outline": [
            {"level": s.level, "title": s.title, "lines": [s.start, s.end], "bytes": s.size}
            for s in sections
            if not (s.area == JOURNAL_SECTION and s.area_depth >= 1)
        ],
        "open_items": open_items(sections),
        "journal": journal_entries(readme, sections, max(journal_limit, 0)),
    }
    if since:
        digest["since"] = changes_since(readme, sections, since)
    return digest


def _kb(size: int) -> str:
    return f"{size / 1024:.1f}KB" if size >= 1024 else f"{size}B"


def render(digest: dict) -> str:
    out = [
        f"# Effort digest: {digest['readme']}",
        f"{digest['lines']} lines, {_kb(digest['bytes'])}",
        "",
        "## Outline",
    ]
    for entry in digest["outline"]:
        start, end = entry["lines"]
        marker = "#" * entry["level"]
        out.append(f"L{start}-{end}\t{_kb(entry['bytes'])}\t{marker} {entry['title']}".rstrip())
    out += ["", f"## Open items ({len(digest['open_items'])})"]
    group = None
    for item in digest["open_items"]:
        if item["section"] != group:
            group = item["section"]
            out.append(f"[{group}]")
        out.append(f"L{item['line']}\t{item['text']}")
    journal = digest["journal"]
    out += ["", f"## Latest journal (entries {journal['entries_total']}, files {journal['files_total']})"]
    out += [
        f"{e['file']}:L{e['line']}\t{_kb(e['bytes'])}\t{e['title']}" for e in journal["latest"]
    ]
    out += [f"{_kb(e['bytes'])}\t{e['file']}" for e in journal["files_latest"]]
    since = digest.get("since")
    if since:
        out += ["", f"## Changed since {since['rev']}"]
        if not (since["files"] or since["sections"] or since["removed_sections"]):
            out.append("(no changes)")
        out += [f"{e['status']}\t{e['file']}" for e in since["files"]]
        for entry in since["sections"]:
            start, end = entry["lines"]
            out.append(f"L{start}-{end}\t{entry['state']}\t{entry['section']}")
        out += [f"removed\t{title}" for title in since["removed_sections"]]
    return "\n".join(out) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("effort", help="effort folder or its README.md")
    parser.add_argument("--since", metavar="REV", help="also list sections and files changed since this git revision")
    parser.add_argument("--journal", type=int, default=3, metavar="N", help="latest journal entries to list (default 3)")
    parser.add_argument("--json", action="store_true", help="emit JSON")
    args = parser.parse_args(argv)
    try:
        digest = build_digest(Path(args.effort), args.since, args.journal)
    except DigestError as error:
        print(f"effort_digest: {error}", file=sys.stderr)
        return 2
    sys.stdout.write(json.dumps(digest, indent=2, ensure_ascii=False) + "\n" if args.json else render(digest))
    return 0


if __name__ == "__main__":
    sys.exit(main())
