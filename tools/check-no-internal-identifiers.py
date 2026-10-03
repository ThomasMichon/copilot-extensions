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
     exact provisioning command), and
  4. a best-effort **live cross-repo sweep**, when a locally registered
     ``agent-worktrees`` installation is discoverable: ``agent-worktrees
     identifiers sweep --format json`` (run with this repo as cwd, so it
     auto-resolves as the sweep target) aggregates every other locally
     registered repo's own ``.identifier-blocklist/block-for-<tier>.yaml``
     denylist, scoped to this repo's own registered audience-exposure tier.
     The JSON format (rather than the CLI's own default ``ci`` text format)
     is used so a parse failure in one peer repo's blocklist still carries
     whatever entries DID parse successfully, for :class:`LiveSweepFailure`.
     See ``plugins/agent-worktrees/src/agent_worktrees/identifier_blocklist.py``
     for the mechanism and ``docs/identifier-blocklist.md`` for the
     convention. This source is silently absent wherever ``agent-worktrees``
     isn't installed or this repo isn't registered (a fresh clone, CI) --
     never required, only additive on a machine that has it. Opt out with
     ``COPILOT_EXTENSIONS_DISABLE_LIVE_SWEEP=1``.

CI entries (sources 3 and 4 share this grammar) are case-insensitive literal
substrings by default. Prefix a token with ``regex:`` to match a Python
regular expression instead (for example
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
Source 4 never has this gotcha: it only ever reads its own generated
``identifiers sweep`` output, never a hand-pasted file.

With none of these configured (a fresh clone with no local ``agent-worktrees``
registration, or CI with no secret) there is nothing to enforce and the check
is a no-op (exit 0) -- so it is safe to ship in the public repo. On your own
machine, populate one of sources 1/2/3 by hand, or simply register this repo
with ``agent-worktrees`` (source 4) and let the live sweep do the work; wire
this up as a git ``pre-push`` hook either way, and it blocks a push that would
leak any of your identifiers.

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
import shutil
import subprocess
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
HOME_LIST = Path.home() / ".agent-codespaces" / "forbidden-identifiers.txt"
CI_LIST_ENV = "COPILOT_EXTENSIONS_FORBIDDEN_IDS_CI"
# Opt-out for source 4 (the live agent-worktrees sweep) -- also what keeps
# this script's own tests deterministic/machine-independent (set by default
# in their subprocess harness; see tools/test_check_no_internal_identifiers.py).
LIVE_SWEEP_DISABLE_ENV = "COPILOT_EXTENSIONS_DISABLE_LIVE_SWEEP"

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


class LiveSweepFailure(Exception):
    """A configured live sweep (source 4) ran but failed -- as opposed to
    simply being absent/not registered (which is never an error; see
    ``_load_live_sweep_identifiers``'s docstring). Carries whatever
    identifiers the sweep DID manage to parse via :attr:`pairs` before
    failing, so a caller can still use them rather than discarding
    everything a broken peer repo's blocklist didn't actually touch.
    """

    def __init__(self, message: str, pairs: list[tuple[str, str | None]]):
        super().__init__(message)
        self.pairs = pairs


# Exact dispatcher error lines (agent-worktrees' own front_door_cli.py /
# __main__.py) that mean "this install/checkout doesn't support `identifiers`
# at all" -- a benign absence, not a configuration failure. Matched against
# a FULL output line (after stripping any leading symbol/whitespace an
# installed version's `output.err` may prepend), never a loose substring of
# the entire stdout/stderr blob: a real malformed-blocklist error can
# legitimately mention a repo path or embed raw YAML parser text, either of
# which could otherwise coincidentally contain one of these phrases and get
# misclassified as benign absence instead of failing the push.
_BENIGN_ABSENCE_LINE_RE = re.compile(
    r"^(?:Unknown subcommand: identifiers"
    r"|Could not resolve a project(?: for 'identifiers')?\.)",
)


def _is_benign_absence(stdout: str, stderr: str) -> bool:
    for raw_line in (stdout + "\n" + stderr).splitlines():
        line = raw_line.strip().lstrip("✗⚠️").strip()
        if _BENIGN_ABSENCE_LINE_RE.match(line):
            return True
    return False


def _load_live_sweep_identifiers() -> list[tuple[str, str | None]]:
    """Best-effort source 4: a locally registered ``agent-worktrees``' live
    cross-repo identifier-blocklist sweep, scoped to this repo as the sweep
    target (auto-resolved from cwd by ``identifiers sweep``).

    Absent anywhere this isn't installed/registered (a fresh clone, CI) --
    a missing binary, a timeout, or any other environment-level failure to
    even run the command is silently swallowed so this is purely additive,
    never a new requirement. Set ``COPILOT_EXTENSIONS_DISABLE_LIVE_SWEEP=1``
    to opt out entirely.

    A sweep that DID run but failed (nonzero exit -- e.g. another locally
    registered repo's ``.identifier-blocklist/`` file is malformed YAML) is
    different: that is a genuinely broken *local configuration*, not mere
    absence, so it is never silently swallowed. It raises
    :class:`LiveSweepFailure`, which still carries whatever identifiers the
    sweep DID manage to parse before failing, so a caller can use them
    defensively while still treating the overall result as a failure. This
    is why the sweep is invoked with ``--format json`` rather than the
    default ``ci`` text format: the CI format is deliberately all-or-nothing
    on failure (nothing at all on stdout, to avoid ever piping a silently
    partial denylist into a consumer that might ignore the exit code), so
    only the JSON diagnostic format actually carries the partially-parsed
    entries this class promises.

    One specific nonzero-exit case is NOT a configuration failure, though:
    an installed ``agent-worktrees`` old enough to predate this feature
    entirely rejects ``identifiers`` with its generic "Unknown subcommand"
    dispatcher error -- that is absence (an unupdated install), not
    breakage, so it is treated the same as the binary not existing at all.
    Likewise, an installed-but-UNREGISTERED checkout (this repo's cwd isn't
    adopted as an ``agent-worktrees`` project on this machine) is rejected
    even earlier, before subcommand dispatch, with a generic "Could not
    resolve a project" response -- also benign absence, not breakage.
    """
    if os.environ.get(LIVE_SWEEP_DISABLE_ENV, "").strip().lower() in ("1", "true", "yes"):
        return []
    exe = shutil.which("agent-worktrees")
    if not exe:
        return []
    try:
        proc = subprocess.run(
            [exe, "identifiers", "sweep", "--format", "json"],
            cwd=REPO, capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.TimeoutExpired, subprocess.SubprocessError):
        return []
    if proc.returncode != 0 and _is_benign_absence(proc.stdout, proc.stderr):
        # An installed agent-worktrees that either predates the
        # `identifiers` command entirely, or whose cwd isn't a registered
        # project on this machine -- benign absence, not a configuration
        # failure. Matched against the dispatcher's exact error lines (not a
        # loose substring of the whole output), since a real malformed-
        # blocklist error can legitimately include a repo path or YAML
        # parser text that happens to contain one of these phrases.
        return []
    try:
        payload = json.loads(proc.stdout) if proc.stdout.strip() else None
    except json.JSONDecodeError:
        payload = None
    if payload is None:
        # Once a discovered sweep command has been invoked (i.e. it isn't
        # one of the explicitly recognized benign-absence responses above),
        # empty or unparseable stdout is itself a protocol failure -- fail
        # closed rather than silently treating it as "nothing to report",
        # regardless of exit code. A well-behaved `identifiers sweep
        # --format json` always emits a full JSON payload (even an empty
        # sweep reports `{"error": null, "entries": []}`), so anything else
        # means something went wrong in a way this guard can't diagnose,
        # and silently accepting it could disable the live denylist
        # entirely without any visible signal.
        raise LiveSweepFailure(
            "agent-worktrees identifiers sweep failed: its --format json "
            "output was empty or not valid JSON"
            + (f" (exit {proc.returncode})" if proc.returncode != 0 else ""),
            [],
        )
    pairs = [
        (
            "regex:" + e["token"][len("regex:"):]
            if str(e.get("token", "")).lower().startswith("regex:")
            else str(e.get("token", "")).lower(),
            (str(e["reason"]).strip() or None) if e.get("reason") else None,
        )
        for e in payload.get("entries", [])
    ]
    if proc.returncode != 0:
        detail = payload.get("error") or (proc.stderr.strip() or "no details")
        raise LiveSweepFailure(
            f"agent-worktrees identifiers sweep failed: {detail}", pairs,
        )
    return pairs


def _load_identifier_data() -> tuple[list[str], dict[str, str | None], str | None]:
    """Returns ``(identifiers, reasons, live_sweep_error)``.

    ``live_sweep_error`` is ``None`` on success or benign absence of source
    4, or a human-readable message when a configured live sweep failed --
    the caller (``main``) must still fail the push when this is set, even
    though ``identifiers``/``reasons`` already include whatever that sweep
    DID manage to parse.
    """
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
    live_sweep_error: str | None = None
    try:
        sweep_pairs = _load_live_sweep_identifiers()
    except LiveSweepFailure as exc:
        sweep_pairs = exc.pairs
        live_sweep_error = str(exc)
    for ident, reason in sweep_pairs:
        ids.append(ident)
        ci_reasons.setdefault(ident, reason)
    # De-dupe literals case-insensitively without changing regex escapes.
    seen: dict[str, None] = {}
    for i in ids:
        token = "regex:" + i[6:] if i.lower().startswith("regex:") else i.lower()
        if token:
            seen.setdefault(token, None)
    return list(seen), ci_reasons, live_sweep_error


def _load_identifiers() -> list[str]:
    identifiers, _, _ = _load_identifier_data()
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


def _compile_patterns(identifiers: list[str]) -> dict[str, re.Pattern[str]]:
    patterns: dict[str, re.Pattern[str]] = {}
    for ident in identifiers:
        if not ident.startswith("regex:"):
            continue
        try:
            patterns[ident] = re.compile(ident[len("regex:"):], re.IGNORECASE)
        except re.error:
            raise ValueError("invalid regular expression in forbidden identifier list") from None
        if patterns[ident].search("") is not None:
            raise ValueError("empty regular expression match in forbidden identifier list")
    return patterns


def _scan_text(
    rel: str,
    text: str,
    identifiers: list[str],
    reasons: dict[str, str | None],
    *,
    patterns: dict[str, re.Pattern[str]] | None = None,
) -> list[Violation]:
    violations: list[Violation] = []
    if patterns is None:
        patterns = _compile_patterns(identifiers)
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
    patterns = _compile_patterns(identifiers)
    loader = text_loader or _read_worktree_text
    violations: list[Violation] = []
    for rel in files:
        if rel in SELF:
            continue
        text = loader(rel)
        if text is None:
            continue
        violations.extend(_scan_text(rel, text, identifiers, reasons, patterns=patterns))
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

    identifiers, reasons, live_sweep_error = _load_identifier_data()
    if live_sweep_error:
        # A configured live sweep (source 4) ran but failed -- a genuinely
        # broken local configuration (e.g. another registered repo's
        # .identifier-blocklist/ is malformed YAML), never silently
        # swallowed the way simple absence is. Fail the push loudly even
        # though `identifiers` above may already include every entry that
        # sweep DID manage to parse before failing.
        print(f"Internal-identifier guard FAILED: {live_sweep_error}")
        return 1
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
