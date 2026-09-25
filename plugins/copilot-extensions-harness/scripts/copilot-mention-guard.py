#!/usr/bin/env python3
"""Decide whether a Copilot shell command posts an ``@copilot`` mention to a
GitHub PR/issue comment or review, **scoped to this plugin's own repo**
(``ThomasMichon/copilot-extensions``, read from ``plugin.json``'s
``repository`` field -- a maintainer *rename* of that field retargets the
guard without a second string to keep in sync; a *fork* does NOT, since a
fork's checked-in ``plugin.json`` still names the upstream repo -- see
``target_repo_from_manifest()``'s own docstring for that limit).

On GitHub, an ``@copilot`` mention in a PR/issue comment or review does NOT
nudge the ``copilot-pull-request-reviewer`` bot to re-review -- it delegates
a task to the separate Copilot **cloud coding agent**, which starts pushing
its own commits directly to the PR branch (consuming its own credit budget,
independent of and unreviewed by the submitting session, and potentially
touching unrelated files if the branch has drifted). The review bot already
re-runs on every push; a fresh verdict needs only a new commit, never a
mention. See ``CONTRIBUTING.md``'s own "Do not comment `@copilot review`"
rule -- this is that policy's mechanical enforcement seam, scoped to this
plugin's own repo so an unrelated repo's `gh` workflow is never touched.

This guard denies the specific action that actually causes the harm -- a
``gh`` invocation that PUBLISHES comment/review/PR-body text containing an
``@copilot`` mention -- not any file write that merely happens to discuss
the topic (this very module and its docs are exempt for that reason).
"""

from __future__ import annotations

import json
import os
import re
import shlex
import subprocess
import sys
from pathlib import Path

# ``@copilot-extensions`` (the marketplace/org handle) and similar
# `@copilot-<word>` identifiers are legitimate and pervasive in this facility
# -- only a bare ``@copilot`` mention (not immediately continued by a word
# character or hyphen) is the GitHub cloud-agent trigger.
MENTION = re.compile(r"@copilot(?![\w-])", re.I)

# Shell statement/pipeline separators, recognized as their own tokens by
# ``shlex.shlex(..., punctuation_chars=...)`` -- NOT a text-level split.
# A naive ``str.split``/regex split on these characters would cut a quoted
# ``--body`` value in half the moment it contains ordinary punctuation like
# ``;`` or ``|`` (e.g. ``--body "Fixed the bug; @copilot review"``), silently
# truncating the very text this guard exists to scan. Splitting the
# quote-aware token STREAM instead keeps a quoted argument's separator
# characters part of that one token, never a statement boundary.
STATEMENT_SEPARATORS = {";", "&", "&&", "||", "|", "\n"}

# gh subcommand chains that publish comment/review/PR-or-issue-body text.
WRITE_SUBCOMMANDS = {
    ("pr", "comment"),
    ("issue", "comment"),
    ("pr", "review"),
    ("pr", "create"),
    ("pr", "edit"),
    ("issue", "create"),
    ("issue", "edit"),
}

# --body / --body-file style flags whose value (or referenced file) is the
# published text to scan. ``-f``/``-F`` cover ``gh api ... -f body=...`` /
# ``-F body=@path`` (used for review-comment replies, which have no first-
# class ``gh pr comment`` equivalent).
INLINE_BODY_FLAGS = {"--body", "-b"}
FILE_BODY_FLAGS = {"--body-file"}

# GitHub permits dots in a repo name (``owner/repo.name``), so the repo
# component must not exclude ``.`` -- only a trailing ``.git`` suffix is
# stripped afterward, not during the match itself (#3663 review).
_REPO_URL_RE = re.compile(r"github\.com[:/]([^/\s]+)/([^/\s]+?)/?$", re.I)


def _basename(token: str) -> str:
    return os.path.basename(token.replace("\\", "/")).lower()


def _parse_github_repo(url: str) -> str | None:
    match = _REPO_URL_RE.search(url)
    if not match:
        return None
    owner, repo = match.group(1), match.group(2)
    if repo.lower().endswith(".git"):
        repo = repo[: -len(".git")]
    return f"{owner}/{repo}".lower()


def target_repo_from_manifest(plugin_root: Path) -> str | None:
    """``owner/repo`` this guard applies to, read from this plugin's own
    ``plugin.json`` ``repository``/``homepage`` field rather than a second
    hardcoded string -- so editing that ONE field (a maintainer rename) is
    the only place to update. This does NOT auto-retarget on a GitHub
    *fork*: a fork's checked-in ``plugin.json`` still names the upstream
    repo, so the guard stays scoped to the upstream repo there too (a
    conservative default -- the mention-post hazard this guard exists for
    is a property of GitHub's Copilot cloud agent, not of any one repo, so
    staying scoped to the plugin's declared home is a safe default even on
    an unrelated fork; it is not a "this fork's own repo" retarget)."""
    try:
        manifest = json.loads((plugin_root / "plugin.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    for key in ("repository", "homepage"):
        url = manifest.get(key)
        if isinstance(url, str):
            repo = _parse_github_repo(url)
            if repo:
                return repo
    return None


def current_repo_candidates(cwd: str | None = None) -> list[str]:
    """Every ``owner/repo`` resolvable from the git remotes configured at
    *cwd* -- not just ``origin``. This codebase's own convention allows a
    configurable remote name (``plugins/agent-worktrees/src/agent_worktrees/
    config.py``'s ``RepoConfig.remote``), and a fork checkout commonly also
    carries an ``upstream`` remote pointing at the real target repo, so
    checking only ``origin`` would silently no-op the guard for either
    shape."""
    try:
        result = subprocess.run(
            ["git", "remote"],
            cwd=cwd, capture_output=True, text=True, timeout=5, check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    repos: list[str] = []
    for name in (line.strip() for line in result.stdout.splitlines()):
        if not name:
            continue
        try:
            url_result = subprocess.run(
                ["git", "remote", "get-url", name],
                cwd=cwd, capture_output=True, text=True, timeout=5, check=False,
            )
        except (OSError, subprocess.SubprocessError):
            continue
        if url_result.returncode != 0:
            continue
        repo = _parse_github_repo(url_result.stdout.strip())
        if repo:
            repos.append(repo)
    return repos


def current_repo_matches(target: str, cwd: str | None = None) -> bool:
    """True if *target* (``owner/repo``) is among the GitHub repos resolved
    from any configured remote at *cwd* (see ``current_repo_candidates``)."""
    return target in current_repo_candidates(cwd)


def _split_unquoted_newlines(command: str) -> list[str]:
    """Split *command* on newline characters that are NOT inside a single-
    or double-quoted span (bash-style quoting; a backslash escapes the next
    character outside single quotes). ``shlex.shlex`` treats ``\\n`` as
    ordinary whitespace, never a statement separator, regardless of
    ``punctuation_chars`` -- newline is not a valid member of that set (it is
    pre-classified as whitespace) -- so a genuine multi-line tool command
    (e.g. ``echo ok\\ngh pr comment ... --body "@copilot review"``) needs
    this dedicated pre-pass or it is silently read as a single token stream
    starting with ``echo``, never reaching the ``gh`` invocation at all."""
    lines: list[str] = []
    current: list[str] = []
    quote: str | None = None
    escaped = False
    for ch in command:
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if ch == "\\" and quote != "'":
            current.append(ch)
            escaped = True
            continue
        if quote:
            current.append(ch)
            if ch == quote:
                quote = None
            continue
        if ch in ("'", '"'):
            quote = ch
            current.append(ch)
            continue
        if ch == "\n":
            lines.append("".join(current))
            current = []
            continue
        current.append(ch)
    lines.append("".join(current))
    return lines


def _split_statements(command: str) -> list[list[str]]:
    """Tokenize the whole command ONCE, respecting quotes, then split the
    resulting token stream on real (unquoted) statement/pipeline separators.

    Using ``shlex.shlex`` with ``punctuation_chars`` set makes it recognize
    ``;``/``&``/``&&``/``||``/``|`` as their own tokens when they appear
    unquoted -- a separator character that occurs INSIDE a quoted argument
    stays part of that single token, so it is never mistaken for a statement
    boundary (see ``STATEMENT_SEPARATORS``' docstring above). Real (unquoted)
    newlines are split out first via ``_split_unquoted_newlines`` -- shlex
    itself never treats one as a separator (see that function's docstring)."""
    statements: list[list[str]] = []
    for line in _split_unquoted_newlines(command):
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars="();<>|&")
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError:
            continue
        current: list[str] = []
        for token in tokens:
            if token in STATEMENT_SEPARATORS or token in {"(", ")", "<", ">"}:
                if current:
                    statements.append(current)
                    current = []
                continue
            current.append(token)
        if current:
            statements.append(current)
    return statements


def _is_gh(tokens: list[str]) -> bool:
    return bool(tokens) and _basename(tokens[0]) in {"gh", "gh.exe"}


def _has_write_subcommand(tokens: list[str]) -> bool:
    pair_stream = [t.lower() for t in tokens[1:]]
    for first, second in WRITE_SUBCOMMANDS:
        for index in range(len(pair_stream) - 1):
            if pair_stream[index] == first and pair_stream[index + 1] == second:
                return True
    return False


def _is_api_comment_or_review_write(tokens: list[str]) -> bool:
    """``gh api ...`` writing a body via ``-f``/``-F``/``--input`` to a
    comments/reviews endpoint (PR review-comment replies, issue comments via
    the raw API)."""
    lowered = [t.lower() for t in tokens[1:]]
    if "api" not in lowered:
        return False
    has_body_field = any(
        (token in {"-f", "-F", "--field", "--raw-field"} and index + 1 < len(tokens)
         and tokens[index + 1].startswith("body="))
        or token.startswith(("-f", "-F", "--field=", "--raw-field=")) and "body=" in token
        for index, token in enumerate(tokens[1:], start=1)
    )
    has_input = any(
        token in {"--input"} or token.startswith("--input=")
        for token in tokens[1:]
    )
    if not (has_body_field or has_input):
        return False
    return any(
        "/comments" in t or "/reviews" in t
        for t in tokens[1:]
        if not t.startswith("-")
    )


def _read_bounded(path: str, limit: int = 200_000) -> str:
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            return fh.read(limit)
    except OSError:
        return ""


_UNSCANNABLE = object()  # sentinel: the body is sourced from stdin, which a
# preToolUse hook cannot inspect (it runs before the command executes, with
# no visibility into whatever gets piped into its future stdin) -- treat as
# unscannable and FAIL CLOSED (deny) rather than silently pass an
# unverifiable publish through.


def _extract_input_json_body(path: str) -> str | object:
    """``gh api --input <file>`` (or ``--input -`` for stdin) sends a raw
    JSON request body -- read *path*, parse it, and return its ``"body"``
    field if present as a string. Any failure (stdin, unreadable file,
    invalid JSON, no string ``"body"`` field) returns ``_UNSCANNABLE``: fail
    closed rather than assume an unparseable payload is safe."""
    if path == "-":
        return _UNSCANNABLE
    try:
        with open(path, encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return _UNSCANNABLE
    if not isinstance(payload, dict):
        return _UNSCANNABLE
    body = payload.get("body")
    return body if isinstance(body, str) else _UNSCANNABLE


def _extract_body_text(tokens: list[str]) -> str | object:
    """Best-effort: the literal body text this invocation would publish, or
    the ``_UNSCANNABLE`` sentinel if the body is sourced from stdin
    (``--body-file -`` / ``-F body=@-``, gh's stdin convention)."""
    parts: list[str] = []
    index = 1
    while index < len(tokens):
        token = tokens[index]
        matched = False
        for flag in INLINE_BODY_FLAGS:
            if token == flag and index + 1 < len(tokens):
                parts.append(tokens[index + 1])
                matched = True
                break
            if token.startswith(flag + "="):
                parts.append(token[len(flag) + 1 :])
                matched = True
                break
        if matched:
            index += 2 if token in INLINE_BODY_FLAGS else 1
            continue
        for flag in FILE_BODY_FLAGS:
            if token == flag and index + 1 < len(tokens):
                if tokens[index + 1] == "-":
                    return _UNSCANNABLE
                parts.append(_read_bounded(tokens[index + 1]))
                matched = True
                break
            if token.startswith(flag + "="):
                value = token[len(flag) + 1 :]
                if value == "-":
                    return _UNSCANNABLE
                parts.append(_read_bounded(value))
                matched = True
                break
        if matched:
            index += 2 if token in FILE_BODY_FLAGS else 1
            continue
        if token == "--input" and index + 1 < len(tokens):
            result = _extract_input_json_body(tokens[index + 1])
            if result is _UNSCANNABLE:
                return _UNSCANNABLE
            parts.append(result)
            index += 2
            continue
        if token.startswith("--input="):
            result = _extract_input_json_body(token[len("--input=") :])
            if result is _UNSCANNABLE:
                return _UNSCANNABLE
            parts.append(result)
            index += 1
            continue
        # ``gh api ... -f body=VALUE`` / ``-F body=@path`` / ``-F body=@-``.
        if token in {"-f", "-F"} and index + 1 < len(tokens):
            field = tokens[index + 1]
            if field.startswith("body="):
                value = field[len("body=") :]
                if value == "@-":
                    return _UNSCANNABLE
                if token == "-F" and value.startswith("@"):
                    parts.append(_read_bounded(value[1:]))
                else:
                    parts.append(value)
            index += 2
            continue
        if token.startswith("body=") and "-f" not in tokens[:index]:
            parts.append(token[len("body=") :])
        index += 1
    if not parts:
        # Fall back to scanning every token this guard couldn't attribute to
        # a recognized body flag (covers shapes this best-effort flag scan
        # didn't anticipate) rather than silently passing an unrecognized
        # invocation.
        return " ".join(tokens)
    return "\n".join(parts)


def command_publishes_copilot_mention(command: str) -> str | None:
    """Returns a deny-reason string if *command* should be blocked, else
    ``None``. Two distinct reasons: an actual ``@copilot`` mention, or a
    stdin-sourced body this hook cannot inspect before the command runs."""
    for tokens in _split_statements(command):
        if not _is_gh(tokens):
            continue
        if not (_has_write_subcommand(tokens) or _is_api_comment_or_review_write(tokens)):
            continue
        body_text = _extract_body_text(tokens)
        if body_text is _UNSCANNABLE:
            # A stdin-sourced body (`--body-file -` / `-F body=@-`) cannot be
            # inspected before the command runs -- fail closed rather than
            # silently pass an unverifiable publish through.
            return "unscannable"
        if MENTION.search(body_text):
            return "mention"
    return None


def main() -> int:
    plugin_root = Path(
        os.environ.get("COPILOT_PLUGIN_ROOT")
        or Path(__file__).resolve().parent.parent
    )
    try:
        data = json.load(sys.stdin)
        tool_name = data.get("toolName")
        if tool_name not in {"bash", "powershell"}:
            return 0
        args = data.get("toolArgs") or {}
        if isinstance(args, str):
            args = json.loads(args)
        command = args.get("command", "") if isinstance(args, dict) else ""
        # Prefer an explicit cwd on the tool-call payload if the host
        # provides one; fall back to this hook process's own cwd (which the
        # CLI sets to match the session's current directory).
        hook_cwd = data.get("cwd") or (args.get("cwd") if isinstance(args, dict) else None)
    except Exception:
        return 0
    target = target_repo_from_manifest(plugin_root)
    if target is None or not current_repo_matches(target, hook_cwd):
        # Scoped to this plugin's own repo -- a session working elsewhere
        # (even with this plugin enabled purely for its instruction
        # projections), or one where the target repo couldn't be resolved
        # at all, is never touched by this guard (fail open, not closed).
        return 0
    reason = command_publishes_copilot_mention(command)
    if reason == "mention":
        print(
            json.dumps(
                {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "DENIED: an @copilot mention in a GitHub PR/issue "
                        "comment or review does not nudge the review bot -- "
                        "it delegates to the separate Copilot cloud coding "
                        "agent, which will push its own unreviewed commits "
                        "to the PR branch. The review bot already re-runs "
                        "on every push; just push a commit for a fresh "
                        "verdict, or post the comment without the mention."
                    ),
                    "decision": "deny",
                    "message": "DENIED: do not @-mention copilot in a PR/issue comment or review.",
                },
                separators=(",", ":"),
            )
        )
    elif reason == "unscannable":
        print(
            json.dumps(
                {
                    "permissionDecision": "deny",
                    "permissionDecisionReason": (
                        "DENIED: this publishes a PR/issue comment or review "
                        "body sourced from stdin (--body-file - / -F "
                        "body=@-), which this guard cannot inspect before "
                        "the command runs. Write the body to a file first "
                        "(--body-file <path>) or pass it inline (--body "
                        "\"...\") so it can be scanned for an @copilot "
                        "mention."
                    ),
                    "decision": "deny",
                    "message": "DENIED: stdin-sourced PR/issue body text cannot be scanned.",
                },
                separators=(",", ":"),
            )
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
