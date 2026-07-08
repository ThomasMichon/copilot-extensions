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

Usage:  python tools/check-docs-consistency.py            # check
        python tools/check-docs-consistency.py --counts   # print derived counts
Exit 0 = consistent, 1 = drift (suitable for a pre-push hook / CI).
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
README = REPO / "README.md"

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
                + check_counts(expected))

    if problems:
        print(f"[FAIL] {len(problems)} docs-consistency problem(s):")
        print("\n".join(problems))
        print("\nFix the doc, or (better) de-count / point at the canonical list.")
        return 1
    print(f"[OK] docs consistent: {total} plugins ({runtime} runtime, {payload} payload-only).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
