#!/usr/bin/env python3
"""Local guard: fail if any private/internal identifier appears in the tree.

This repo is public, so it must never contain internal org/account/project
identifiers (employer org names, internal repo names, personal aliases, …).
A denylist that *named* those strings would itself leak them, so the list is
**never stored in this repo**. It is sourced, privately, from:

  1. env ``COPILOT_EXTENSIONS_FORBIDDEN_IDS`` (comma-separated), and
  2. ``~/.agent-codespaces/forbidden-identifiers.txt`` (one per line; blank
     lines and ``#`` comments ignored), and
  3. env ``COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI`` (newline- or ``;``-separated
     ``token|reason`` entries for CI/trusted-workflow use -- backed in
     production by the ``FORBIDDEN_IDS_FACILITY`` / ``FORBIDDEN_IDS_WORK``
     repository secrets consumed by
     ``.github/workflows/identifier-leak-guard.yml``, which documents the
     exact provisioning command).

CI entries are case-insensitive literal substrings by default. Prefix a token
with ``regex:`` to match a Python regular expression instead (for example
``regex:\\bexample\\b|Standalone name -- use a generic placeholder``). The
prefix is a matching mode, not part of the reported match. Double a regex
alternation pipe (``||``) in the secret to distinguish it from the first
single ``|`` separating the reason; reasons may contain pipes. Regex entries
cannot contain semicolons or newlines, which delimit entries.

**Gotcha specific to source 3:** unlike source 2's local loader, the CI-mode
loader (``_load_ci_identifiers``) skips blank entries but does **not** skip
``#``-prefixed comment lines -- every non-empty line becomes a literal
token, including a bare ``#`` on its own line, which matches almost any
Markdown heading. Never paste a commented source file straight into
``COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI`` (or the secrets above) -- strip
comments and blank lines first (e.g. ``grep -vE '^\\s*#|^\\s*$' file``).

With neither configured (a fresh clone / CI) there is nothing to enforce and
the check is a no-op (exit 0) -- so it is safe to ship in the public repo. On
your own machine, populate either source and wire this up as a git ``pre-push``
hook; it then blocks a push that would leak any of your identifiers.

Scope: by default the guard only scans the files your push actually **changes**
(``git diff --name-only <base>...HEAD``, base ``origin/main`` -- override via
``--base`` or ``COPILOT_EXTENSIONS_GUARD_BASE``). This keeps a pre-existing
identifier in an *untouched* file from blocking every unrelated push, while
still catching anything a push introduces. Pass ``--all`` to audit the whole
tracked tree instead (useful for a one-off full sweep). If the base ref can't
be resolved (no ``origin/main`` in a fresh clone), the guard falls back to a
full-tree scan. Trusted CI may instead pass ``--paths-file`` plus ``--git-ref``
to scan repo-relative paths from a fetched PR-head tree-ish as inert git data
without checking out or executing that revision.

Run manually:  python tools/check-no-internal-identifiers.py          # push diff
               python tools/check-no-internal-identifiers.py --all    # whole tree
Exit code 0 = clean (or nothing configured), 1 = a forbidden identifier was
found (suitable for a pre-push hook).

The same two private sources drive the agent-codespaces scaffold guard
(``plugins/agent-codespaces/tests/test_config_init.py``).
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOME_LIST = Path.home() / ".agent-codespaces" / "forbidden-identifiers.txt"
CI_LIST_ENV = "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI"

# Files this guard must not flag for merely *implementing* the mechanism.
SELF = {
    "tools/check-no-internal-identifiers.py",
    "plugins/agent-codespaces/tests/test_config_init.py",
}

# Allowlist: (identifier -> path prefixes) where a denylisted substring is a
# legitimate *product/generic* term, not the internal identifier. The sole case
# today is the Microsoft **OneDrive** product -- agent-logger's filesystem sync
# target (``OneDriveTarget`` / ``resolve_onedrive_root`` / the ``onedrive``
# target name / the ``OneDrive*`` env vars / ``~/OneDrive``) legitimately names
# the consumer OneDrive folder, which the bare ``onedrive`` denylist substring
# cannot distinguish from the internal ``onedrive`` ADO org. The org form is
# scrubbed everywhere (``onedrive.visualstudio.com`` etc.), so within these
# paths ``onedrive`` is always the product. Prefixes are matched case-
# insensitively against the repo-relative path.
ALLOW: dict[str, tuple[str, ...]] = {
    "onedrive": ("plugins/agent-logger/", "plugins/agent-vault/tests/", "readme.md"),
}


@dataclass(frozen=True)
class Violation:
    path: str
    line: int
    col: int
    identifier: str
    reason: str | None = None


def _allowed(ident: str, rel: str) -> bool:
    """True when *ident* is an allowlisted product/generic term in *rel*."""
    prefixes = ALLOW.get(ident.lower())
    if not prefixes:
        return False
    low = rel.lower()
    return any(low.startswith(p) for p in prefixes)


def _load_ci_identifiers(raw: str) -> list[tuple[str, str | None]]:
    pairs: list[tuple[str, str | None]] = []
    for chunk in raw.replace(";", "\n").splitlines():
        entry = chunk.strip()
        if not entry:
            continue
        if entry.lower().startswith("regex:"):
            parts: list[str] = []
            offset = 0
            while offset < len(entry):
                if entry.startswith("||", offset):
                    parts.append("|")
                    offset += 2
                elif entry[offset] == "|":
                    break
                else:
                    parts.append(entry[offset])
                    offset += 1
            token = "".join(parts)
            sep = "|" if offset < len(entry) else ""
            reason = entry[offset + 1:] if sep else ""
        else:
            token, sep, reason = entry.partition("|")
        parsed_token = token.strip()
        if not parsed_token:
            continue
        low = (
            "regex:" + parsed_token[len("regex:"):]
            if parsed_token.lower().startswith("regex:")
            else parsed_token.lower()
        )
        parsed_reason = reason.strip() if sep and reason.strip() else None
        pairs.append((low, parsed_reason))
    return pairs


def _load_identifier_data() -> tuple[list[str], dict[str, str | None]]:
    ids: list[str] = []
    env = os.environ.get("COPILOT_EXTENSIONS_FORBIDDEN_IDS", "")
    ids += [s for s in (part.strip() for part in env.split(",")) if s]
    try:
        for raw in HOME_LIST.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if line and not line.startswith("#"):
                ids.append(line)
    except OSError:
        pass
    ci_reasons: dict[str, str | None] = {}
    for ident, reason in _load_ci_identifiers(os.environ.get(CI_LIST_ENV, "")):
        ids.append(ident)
        ci_reasons.setdefault(ident, reason)
    # De-dupe literals case-insensitively without changing regex escapes.
    seen: dict[str, None] = {}
    for i in ids:
        token = "regex:" + i[6:] if i.lower().startswith("regex:") else i.lower()
        if token:
            seen.setdefault(token, None)
    return list(seen), ci_reasons


def _load_identifiers() -> list[str]:
    identifiers, _ = _load_identifier_data()
    return identifiers


def _tracked_files() -> list[str]:
    out = subprocess.run(
        ["git", "ls-files"], cwd=REPO, capture_output=True, text=True, check=True
    )
    return [line for line in out.stdout.splitlines() if line]


def _load_paths_file(path: Path) -> list[str]:
    return [line for line in path.read_text(encoding="utf-8").splitlines() if line]


DEFAULT_BASE = os.environ.get("COPILOT_EXTENSIONS_GUARD_BASE", "origin/main")


def _ref_exists(ref: str) -> bool:
    return (
        subprocess.run(
            ["git", "rev-parse", "--verify", "--quiet", ref],
            cwd=REPO,
            capture_output=True,
            text=True,
            check=False,
        ).returncode
        == 0
    )


def _changed_files(base: str) -> list[str] | None:
    """Files this branch changed vs *base* (``git diff --name-only base...HEAD``).

    Returns the changed paths, or ``None`` when *base* can't be resolved (e.g. a
    fresh clone with no ``origin/main``) so the caller can fall back to a
    full-tree scan.
    """
    if not _ref_exists(base):
        return None
    out = subprocess.run(
        ["git", "diff", "--name-only", f"{base}...HEAD"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    return [line for line in out.stdout.splitlines() if line]


def _files_to_scan(scan_all: bool, base: str, *, emit_status: bool = True) -> list[str]:
    """Resolve the set of repo-relative files the guard should scan."""
    if scan_all:
        return _tracked_files()
    changed = _changed_files(base)
    if changed is None:
        if emit_status:
            print(
                f"base ref '{base}' not found -- scanning the whole tracked tree.",
            )
        return _tracked_files()
    if emit_status:
        print(f"scanning {len(changed)} file(s) changed vs {base}.")
    return changed


def _read_worktree_text(rel: str) -> str | None:
    path = REPO / rel
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return None


def _make_git_ref_loader(ref: str) -> Callable[[str], str | None]:
    def _read_git_ref_text(rel: str) -> str | None:
        try:
            result = subprocess.run(
                ["git", "show", f"{ref}:{rel}"],
                cwd=REPO,
                capture_output=True,
                check=True,
            )
        except subprocess.CalledProcessError:
            return None
        try:
            return result.stdout.decode("utf-8")
        except UnicodeDecodeError:
            return None

    return _read_git_ref_text


def _scan_text(
    rel: str,
    text: str,
    identifiers: list[str],
    reasons: dict[str, str | None],
) -> list[Violation]:
    violations: list[Violation] = []
    patterns: dict[str, re.Pattern[str]] = {}
    for ident in identifiers:
        if not ident.startswith("regex:") or _allowed(ident, rel):
            continue
        try:
            patterns[ident] = re.compile(ident[len("regex:"):], re.IGNORECASE)
        except re.error:
            raise ValueError("invalid regular expression in forbidden identifier list") from None
    if not patterns and not any(
        ident in text.lower() for ident in identifiers if not _allowed(ident, rel)
    ):
        return violations
    for lineno, line in enumerate(text.splitlines(), start=1):
        ll = line.lower()
        for ident in identifiers:
            if _allowed(ident, rel):
                continue
            if ident.startswith("regex:"):
                match = patterns[ident].search(line)
                if match is not None and not match.group():
                    raise ValueError("empty regular expression match in forbidden identifier list")
                col = match.start() if match else -1
                matched = match.group() if match else ident
            else:
                col = ll.find(ident)
                matched = ident
            if col == -1:
                continue
            violations.append(
                Violation(
                    path=rel,
                    line=lineno,
                    col=col + 1,
                    identifier=matched,
                    reason=reasons.get(ident),
                )
            )
    return violations


def _scan(
    files: list[str],
    identifiers: list[str],
    reasons: dict[str, str | None],
    *,
    text_loader: Callable[[str], str | None] | None = None,
) -> list[Violation]:
    loader = text_loader or _read_worktree_text
    violations: list[Violation] = []
    for rel in files:
        if rel in SELF:
            continue
        text = loader(rel)
        if text is None:
            continue
        violations.extend(_scan_text(rel, text, identifiers, reasons))
    return violations


def _identifier_hash(identifier: str) -> str:
    return hashlib.sha256(identifier.lower().encode("utf-8")).hexdigest()


def _write_json(path: Path, violations: list[Violation]) -> None:
    payload = [
        {
            "file": violation.path,
            "line": violation.line,
            "col": violation.col,
            "identifier_hash": _identifier_hash(violation.identifier),
            "has_reason": violation.reason is not None,
        }
        for violation in violations
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def _write_trusted_details_json(path: Path, violations: list[Violation]) -> None:
    payload = [
        {
            "file": violation.path,
            "line": violation.line,
            "col": violation.col,
            "identifier": violation.identifier,
            "reason": violation.reason,
        }
        for violation in violations
    ]
    path.write_text(json.dumps(payload, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Fail if a forbidden internal identifier appears in the "
        "push diff (or the whole tree with --all).",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Scan every tracked file instead of just the push diff.",
    )
    parser.add_argument(
        "--base",
        default=DEFAULT_BASE,
        metavar="REF",
        help=f"Base ref for the push-diff scope (default {DEFAULT_BASE!r}; "
        "override via COPILOT_EXTENSIONS_GUARD_BASE).",
    )
    parser.add_argument(
        "--paths-file",
        metavar="PATH",
        help="Read repo-relative paths to scan from PATH (one per line).",
    )
    parser.add_argument(
        "--git-ref",
        metavar="REF",
        help="Read file contents from git tree-ish REF instead of the working tree.",
    )
    parser.add_argument(
        "--json-out",
        metavar="PATH",
        help="Write structured findings JSON to PATH.",
    )
    parser.add_argument(
        "--trusted-details-json-out",
        metavar="PATH",
        help="Write trusted-workflow-only findings JSON, including matched values and reasons, to PATH.",
    )
    parser.add_argument(
        "--ci",
        action="store_true",
        help="Suppress per-finding stdout and print only a count summary.",
    )
    args = parser.parse_args(argv)
    if args.all and args.paths_file:
        parser.error("--all and --paths-file are mutually exclusive")

    identifiers, reasons = _load_identifier_data()
    if not identifiers:
        print(
            "no forbidden identifiers configured "
            "(set COPILOT_EXTENSIONS_FORBIDDEN_IDS or write "
            "~/.agent-codespaces/forbidden-identifiers.txt) -- skipping.",
        )
        if args.json_out:
            _write_json(Path(args.json_out), [])
        if args.trusted_details_json_out:
            _write_trusted_details_json(Path(args.trusted_details_json_out), [])
        return 0

    files_to_scan = (
        _load_paths_file(Path(args.paths_file))
        if args.paths_file
        else _files_to_scan(args.all, args.base, emit_status=not args.ci)
    )
    text_loader = _make_git_ref_loader(args.git_ref) if args.git_ref else None
    violations = _scan(
        files_to_scan,
        identifiers,
        reasons,
        text_loader=text_loader,
    )
    if args.json_out:
        _write_json(Path(args.json_out), violations)
    if args.trusted_details_json_out:
        _write_trusted_details_json(Path(args.trusted_details_json_out), violations)

    if violations:
        if args.ci:
            print(
                f"{len(violations)} forbidden identifier(s) found -- "
                "see the 'identifier leak guard' Check Run output for details."
            )
        else:
            print("Internal-identifier guard FAILED -- remove these before pushing:")
            for violation in violations:
                print(
                    f"  {violation.path}:{violation.line}: forbidden identifier "
                    f"'{violation.identifier}'"
                )
            print(f"\n{len(violations)} occurrence(s) in the scanned files.")
        return 1

    print(f"Internal-identifier guard OK ({len(identifiers)} identifier(s) checked).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
