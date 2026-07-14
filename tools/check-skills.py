#!/usr/bin/env python3
"""Validate SKILL.md files against the Copilot CLI's load-time constraints.

A skill whose frontmatter violates the runtime's limits is silently *dropped*
at load time -- the Copilot CLI reports e.g. "Skill description must be at most
1024 characters" and the skill never becomes available. That failure is only
visible on the machine that deployed it, long after the bad commit shipped.
This guard catches it at commit/push time instead.

Checks, per SKILL.md (truth is the Copilot CLI skill loader + the authoring
conventions in the customizing-copilot `authoring-skills` skill):

  1. **Frontmatter present + parseable** -- a leading `---` ... `---` YAML block.
  2. **name** -- required; lowercase letters/digits/hyphens only; <= 64 chars;
     not a reserved word (`anthropic`, `claude`).
  3. **description** -- required; non-empty; <= 1024 characters (HARD limit --
     the loader rejects the skill above it). Warns when it crowds the limit.
  4. **body length** -- warns past 500 lines (a soft authoring guideline;
     split detail into references/).

Usage:
  python tools/check-skills.py                 # scan every SKILL.md in the repo
  python tools/check-skills.py PATH [PATH ...]  # check only the given files
                                                # (used by the pre-commit hook
                                                #  with staged SKILL.md paths)

Exit 0 = all clear (warnings allowed), 1 = at least one error. Suitable for a
pre-commit / pre-push hook or CI. Stdlib-only; uses PyYAML if importable for a
more exact frontmatter parse, otherwise a self-contained fallback parser.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent

# Hard limit enforced by the Copilot CLI skill loader.
DESC_MAX = 1024
# Warn when a description crowds the hard limit (leaves headroom for edits).
DESC_WARN = 950
# Soft authoring guideline for the markdown body.
BODY_MAX_LINES = 500

NAME_RE = re.compile(r"^[a-z0-9-]+$")
NAME_MAX = 64
RESERVED_NAMES = {"anthropic", "claude"}

# Directories that hold vendored / deployed / generated SKILL.md files we do not
# author and must not gate on.
EXCLUDE_PARTS = {
    ".git", ".venv", "venv", "site-packages", "node_modules",
    "installed-plugins", "__pycache__",
}


def find_skill_files() -> list[Path]:
    """Every authored SKILL.md tracked under the repo, vendored copies excluded."""
    out: list[Path] = []
    for p in REPO.rglob("SKILL.md"):
        if any(part in EXCLUDE_PARTS for part in p.relative_to(REPO).parts):
            continue
        out.append(p)
    return sorted(out)


def split_frontmatter(text: str) -> tuple[str | None, str]:
    """Return (frontmatter_block, body). frontmatter_block is None if absent."""
    m = re.match(r"^---[ \t]*\r?\n(.*?)\r?\n---[ \t]*\r?\n?", text, re.S)
    if not m:
        return None, text
    return m.group(1), text[m.end():]


def _fallback_parse(fm: str) -> dict:
    """Minimal frontmatter parser for `name` (scalar) and `description`
    (inline or `>`/`|` block scalar) -- used only when PyYAML is unavailable.
    Reproduces YAML block-scalar folding closely enough to measure length."""
    data: dict = {}
    lines = fm.splitlines()
    i = 0
    key_re = re.compile(r"^([A-Za-z0-9_-]+):[ \t]*(.*)$")
    while i < len(lines):
        line = lines[i]
        km = key_re.match(line)
        if not km:
            i += 1
            continue
        key, rest = km.group(1), km.group(2)
        rest_stripped = rest.strip()
        block = re.match(r"^([|>])([+-]?)[ \t]*(#.*)?$", rest_stripped)
        if block:
            style, chomp = block.group(1), block.group(2)
            i += 1
            collected: list[str] = []
            indent: int | None = None
            while i < len(lines):
                bl = lines[i]
                if bl.strip() == "":
                    collected.append("")
                    i += 1
                    continue
                cur_indent = len(bl) - len(bl.lstrip())
                if indent is None:
                    indent = cur_indent
                if cur_indent < (indent or 0) and key_re.match(bl.strip()):
                    break
                if cur_indent < (indent or 0):
                    break
                collected.append(bl[indent:] if len(bl) >= (indent or 0) else bl.strip())
                i += 1
            data[key] = _render_block(collected, style, chomp)
            continue
        # Inline scalar (possibly quoted).
        val = rest_stripped
        if len(val) >= 2 and val[0] in "'\"" and val[-1] == val[0]:
            val = val[1:-1]
        data[key] = val
        i += 1
    return data


def _render_block(lines: list[str], style: str, chomp: str) -> str:
    while lines and lines[-1] == "":
        lines.pop()
    if style == "|":
        text = "\n".join(lines)
    else:  # folded
        parts: list[str] = []
        prev_blank = True
        for ln in lines:
            if ln == "":
                parts.append("\n")
                prev_blank = True
            else:
                parts.append(("" if prev_blank else " ") + ln)
                prev_blank = False
        text = "".join(parts)
    if chomp != "-":  # clip (default) or keep -> one trailing newline
        text += "\n"
    return text


def parse_frontmatter(fm: str) -> tuple[dict | None, str | None]:
    """(data, error). Prefer PyYAML; fall back to the local parser."""
    try:
        import yaml  # type: ignore
        try:
            data = yaml.safe_load(fm)
        except Exception as exc:  # noqa: BLE001
            return None, f"frontmatter is not valid YAML: {exc}"
        if not isinstance(data, dict):
            return None, "frontmatter did not parse to a mapping"
        return data, None
    except ImportError:
        try:
            return _fallback_parse(fm), None
        except Exception as exc:  # noqa: BLE001
            return None, f"could not parse frontmatter: {exc}"


def check_file(path: Path) -> tuple[list[str], list[str]]:
    """Return (errors, warnings) for one SKILL.md."""
    errors: list[str] = []
    warnings: list[str] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception as exc:  # noqa: BLE001
        return [f"could not read file: {exc}"], []

    fm, body = split_frontmatter(text)
    if fm is None:
        return ["missing YAML frontmatter (a leading '---' ... '---' block)"], []

    data, err = parse_frontmatter(fm)
    if err:
        return [err], []
    assert data is not None

    name = data.get("name")
    if not name or not isinstance(name, str) or not name.strip():
        errors.append("frontmatter is missing a non-empty 'name'")
    else:
        name = name.strip()
        if len(name) > NAME_MAX:
            errors.append(f"name is {len(name)} chars (max {NAME_MAX})")
        if not NAME_RE.match(name):
            errors.append(
                f"name '{name}' must be lowercase letters, digits, and hyphens only"
            )
        if name.lower() in RESERVED_NAMES:
            errors.append(f"name '{name}' uses a reserved word")

    desc = data.get("description")
    if not desc or not isinstance(desc, str) or not desc.strip():
        errors.append("frontmatter is missing a non-empty 'description'")
    else:
        n = len(desc)
        if n > DESC_MAX:
            errors.append(
                f"description is {n} chars (max {DESC_MAX}) -- the Copilot CLI "
                f"will DROP this skill at load time; trim it"
            )
        elif n >= DESC_WARN:
            warnings.append(
                f"description is {n} chars, close to the {DESC_MAX} limit -- "
                f"consider trimming for edit headroom"
            )

    body_lines = body.count("\n") + (1 if body and not body.endswith("\n") else 0)
    if body_lines > BODY_MAX_LINES:
        warnings.append(
            f"body is {body_lines} lines (guideline {BODY_MAX_LINES}); "
            f"move detail into references/"
        )

    return errors, warnings


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument(
        "paths", nargs="*",
        help="specific SKILL.md files to check (default: every SKILL.md in the repo)",
    )
    args = ap.parse_args()

    if args.paths:
        files = [Path(p).resolve() for p in args.paths]
        files = [p for p in files if p.name == "SKILL.md" and p.is_file()]
    else:
        files = find_skill_files()

    if not files:
        return 0

    total_err = 0
    total_warn = 0
    for path in files:
        errors, warnings = check_file(path)
        if not errors and not warnings:
            continue
        try:
            rel = path.relative_to(REPO)
        except ValueError:
            rel = path
        for e in errors:
            print(f"ERROR {rel}: {e}", file=sys.stderr)
            total_err += 1
        for w in warnings:
            print(f"WARN  {rel}: {w}", file=sys.stderr)
            total_warn += 1

    if total_err:
        print(
            f"\ncheck-skills: {total_err} error(s), {total_warn} warning(s) "
            f"across {len(files)} skill(s).",
            file=sys.stderr,
        )
        return 1
    if total_warn:
        print(
            f"check-skills: {total_warn} warning(s), 0 errors "
            f"across {len(files)} skill(s).",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
