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

import importlib.util
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

DECISION_BUDGET_SECONDS = 1.5

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


def target_repo_from_manifest(plugin_root: Path, deadline: float | None = None) -> str | None:
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
        content = _read_bounded(
            str(plugin_root / "plugin.json"), deadline=deadline, errors="strict",
        )
        manifest = json.loads(content) if content is not None else {}
    except ValueError:
        return None
    for key in ("repository", "homepage"):
        url = manifest.get(key)
        if isinstance(url, str):
            repo = _parse_github_repo(url)
            if repo:
                return repo
    return None


_GIT_ENV_VARS_TO_STRIP = (
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES",
)


def _clean_git_env() -> dict[str, str]:
    """A copy of this process's environment with the ``GIT_*`` variables
    that redirect git's repository discovery stripped, so a caller-supplied
    ambient value (``GIT_DIR``, ``GIT_WORK_TREE``, ...) can't point our own
    ``git remote`` probes at a different repo than *cwd* actually is --
    matching the sanitized-environment precedent in this same codebase's
    ``agent_worktrees.git_ops``."""
    return {k: v for k, v in os.environ.items() if k not in _GIT_ENV_VARS_TO_STRIP}


def current_repo_candidates(
    cwd: str | None = None, *, deadline: float | None = None,
) -> list[str]:
    """Every ``owner/repo`` resolvable from the git remotes configured at
    *cwd* -- not just ``origin``. This codebase's own convention allows a
    configurable remote name (``plugins/agent-worktrees/src/agent_worktrees/
    config.py``'s ``RepoConfig.remote``), and a fork checkout commonly also
    carries an ``upstream`` remote pointing at the real target repo, so
    checking only ``origin`` would silently no-op the guard for either
    shape."""
    # Most hook calls never discover a repository. Defer subprocess (and its
    # threading imports) until the local publication classifier needs Git.
    import subprocess

    if deadline is None:
        deadline = time.monotonic() + DECISION_BUDGET_SECONDS
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return []
    try:
        result = subprocess.run(
            ["git", "remote", "--verbose"],
            cwd=cwd, capture_output=True, text=True, timeout=remaining, check=False,
            env=_clean_git_env(), creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        return []
    repos: list[str] = []
    seen: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split(maxsplit=2)
        if (
            len(fields) != 3
            or not re.match(r"^\(fetch\)(?:\s|$)", fields[2])
            or fields[0] in seen
        ):
            continue
        # Like `remote get-url NAME`, use the first fetch URL, including
        # Git's insteadOf expansion, never a push-only URL.
        seen.add(fields[0])
        repo = _parse_github_repo(fields[1])
        if repo:
            repos.append(repo)
    return repos


def current_repo_matches(
    target: str, cwd: str | None = None, *, deadline: float | None = None,
) -> bool:
    """True if *target* (``owner/repo``) is among the GitHub repos resolved
    from any configured remote at *cwd* (see ``current_repo_candidates``)."""
    return target in current_repo_candidates(cwd, deadline=deadline)


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
    return [_strip_leading_prefixes(statement) for statement in statements]


ASSIGNMENT = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*=")


def _strip_leading_prefixes(tokens: list[str]) -> list[str]:
    """Strip leading ``VAR=value`` assignments and ``env``/``command``
    wrapper tokens so ``gh`` is recognized even when it isn't literally the
    first token -- e.g. ``GH_TOKEN="$TOKEN" gh pr comment ...`` or
    ``env gh pr comment ...``, both valid, ordinary shell forms."""
    tokens = list(tokens)
    while tokens:
        while tokens and ASSIGNMENT.match(tokens[0]):
            tokens.pop(0)
        if not tokens:
            break
        executable = _basename(tokens[0])
        if executable in {"env", "env.exe"}:
            tokens.pop(0)
            while tokens and (tokens[0].startswith("-") or ASSIGNMENT.match(tokens[0])):
                tokens.pop(0)
            continue
        if executable in {"command", "command.exe"}:
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
            continue
        break
    return tokens


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
    comments/reviews/issue/pull-request endpoint (PR review-comment
    replies, issue comments via the raw API, and a PR/issue's own body
    update, e.g. ``PATCH /repos/o/r/issues/1``)."""
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
        "/comments" in t or "/reviews" in t or "/issues" in t or "/pulls" in t
        for t in tokens[1:]
        if not t.startswith("-")
    )


def _read_bounded(
    path: str, limit: int = 200_000, *, deadline: float | None = None,
    errors: str = "replace", cwd: str | None = None,
) -> str | None:
    """Contents of *path*, or ``None`` if it can't be read/is too large to
    fully inspect -- NEVER an empty or truncated string on failure, either
    of which would make an uninspectable body file (unreadable, a
    variable-expanded path this best-effort tokenizer couldn't resolve, or
    one whose real content exceeds *limit*) look identical to an empty or
    fully-scanned one and silently pass it through. Reads one byte past
    *limit* specifically to detect truncation rather than ever treating a
    cut-off prefix as the complete body."""
    def read() -> str | None:
        try:
            source = Path(path)
            if cwd and not source.is_absolute():
                source = Path(cwd) / source
            with open(source, encoding="utf-8", errors=errors) as fh:
                content = fh.read(limit + 1)
        except (OSError, UnicodeError):
            return None
        return content if len(content) <= limit else None

    if deadline is None:
        return read()
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None
    # A FIFO or stalled filesystem read must not hold the hook past its
    # decision budget. The daemon reader is owned by this short-lived hook
    # process; exiting the hook closes any still-blocked reader and handle.
    import queue
    import threading

    results: queue.Queue[str | None] = queue.Queue(maxsize=1)
    threading.Thread(target=lambda: results.put(read()), daemon=True).start()
    try:
        return results.get(timeout=max(0, deadline - time.monotonic()))
    except queue.Empty:
        return None


_UNSCANNABLE = object()  # sentinel: the body is sourced from stdin, which a
# preToolUse hook cannot inspect (it runs before the command executes, with
# no visibility into whatever gets piped into its future stdin) -- treat as
# unscannable and FAIL CLOSED (deny) rather than silently pass an
# unverifiable publish through.


def _extract_input_json_body(
    path: str, deadline: float | None = None, *, cwd: str | None = None,
) -> str | object:
    """``gh api --input <file>`` (or ``--input -`` for stdin) sends a raw
    JSON request body -- read *path*, parse it, and return its ``"body"``
    field if present as a string. Any failure (stdin, unreadable file,
    invalid JSON, no string ``"body"`` field) returns ``_UNSCANNABLE``: fail
    closed rather than assume an unparseable payload is safe."""
    if path == "-":
        return _UNSCANNABLE
    try:
        content = _read_bounded(path, deadline=deadline, errors="strict", cwd=cwd)
        if content is None:
            return _UNSCANNABLE
        payload = json.loads(content)
    except ValueError:
        return _UNSCANNABLE
    if not isinstance(payload, dict):
        return _UNSCANNABLE
    body = payload.get("body")
    return body if isinstance(body, str) else _UNSCANNABLE


def _is_create_subcommand(tokens: list[str]) -> bool:
    """``pr create``/``issue create`` can derive their body from an
    interactive prompt or ``--fill`` (commit messages) when no explicit
    ``--body``/``--body-file``/``--input`` is given -- content that never
    appears in argv at all, so it can't be scanned (see
    ``_extract_body_text``'s fail-closed handling for this case)."""
    pair_stream = [t.lower() for t in tokens[1:]]
    for index in range(len(pair_stream) - 1):
        if pair_stream[index] in ("pr", "issue") and pair_stream[index + 1] == "create":
            return True
    return False


# A literal shell-expansion reference (``$VAR``, ``${VAR}``, ``$(...)``,
# or a backtick command substitution) that shlex's quote-removal leaves
# intact as plain text, since shlex only strips quoting -- it is not a real
# shell and never performs the expansion itself. A body argument containing
# one of these is NOT the actual published text; the real content is only
# known at gh's own execution time, after this hook has already decided.
_UNEXPANDED_REFERENCE_RE = re.compile(r"\$\{|\$\(|\$[A-Za-z_]|`")


def _contains_unexpanded_reference(value: str) -> bool:
    return bool(_UNEXPANDED_REFERENCE_RE.search(value))


def _extract_body_text(
    tokens: list[str], deadline: float | None = None, *, cwd: str | None = None,
) -> str | object:
    """Best-effort: the literal body text this invocation would publish, or
    the ``_UNSCANNABLE`` sentinel if the body is sourced from stdin
    (``--body-file -`` / ``-F body=@-``, gh's stdin convention) or is an
    unexpanded shell variable/command-substitution reference this hook
    cannot resolve before the command actually runs."""
    parts: list[str] = []
    index = 1
    while index < len(tokens):
        if deadline is not None and time.monotonic() >= deadline:
            return _UNSCANNABLE
        token = tokens[index]
        matched = False
        for flag in INLINE_BODY_FLAGS:
            if token == flag and index + 1 < len(tokens):
                value = tokens[index + 1]
                if _contains_unexpanded_reference(value):
                    return _UNSCANNABLE
                parts.append(value)
                matched = True
                break
            if token.startswith(flag + "="):
                value = token[len(flag) + 1 :]
                if _contains_unexpanded_reference(value):
                    return _UNSCANNABLE
                parts.append(value)
                matched = True
                break
        if matched:
            index += 2 if token in INLINE_BODY_FLAGS else 1
            continue
        for flag in FILE_BODY_FLAGS:
            if token == flag and index + 1 < len(tokens):
                if tokens[index + 1] == "-":
                    return _UNSCANNABLE
                content = _read_bounded(tokens[index + 1], deadline=deadline, cwd=cwd)
                if content is None:
                    return _UNSCANNABLE
                parts.append(content)
                matched = True
                break
            if token.startswith(flag + "="):
                value = token[len(flag) + 1 :]
                if value == "-":
                    return _UNSCANNABLE
                content = _read_bounded(value, deadline=deadline, cwd=cwd)
                if content is None:
                    return _UNSCANNABLE
                parts.append(content)
                matched = True
                break
        if matched:
            index += 2 if token in FILE_BODY_FLAGS else 1
            continue
        if token == "--input" and index + 1 < len(tokens):
            result = _extract_input_json_body(tokens[index + 1], deadline, cwd=cwd)
            if result is _UNSCANNABLE:
                return _UNSCANNABLE
            parts.append(result)
            index += 2
            continue
        if token.startswith("--input="):
            result = _extract_input_json_body(token[len("--input=") :], deadline, cwd=cwd)
            if result is _UNSCANNABLE:
                return _UNSCANNABLE
            parts.append(result)
            index += 1
            continue
        # ``gh api ... -f body=VALUE`` / ``-F body=@path`` / ``-F body=@-``
        # (and their ``--field``/``--raw-field`` long-flag aliases, space or
        # ``=``-joined form). Both flag families support an ``@file``
        # reference -- neither is raw-only for that purpose.
        body_field_flags = {"-f", "-F", "--field", "--raw-field"}
        field = None
        consumed = 0
        if token in body_field_flags and index + 1 < len(tokens):
            field = tokens[index + 1]
            consumed = 2
        else:
            for flag in body_field_flags:
                if token.startswith(flag + "="):
                    field = token[len(flag) + 1 :]
                    consumed = 1
                    break
        if field is not None:
            if field.startswith("body="):
                value = field[len("body=") :]
                if value == "@-":
                    return _UNSCANNABLE
                if value.startswith("@"):
                    content = _read_bounded(value[1:], deadline=deadline, cwd=cwd)
                    if content is None:
                        return _UNSCANNABLE
                    parts.append(content)
                elif _contains_unexpanded_reference(value):
                    return _UNSCANNABLE
                else:
                    parts.append(value)
            index += consumed
            continue
        if token.startswith("body=") and "-f" not in tokens[:index]:
            value = token[len("body=") :]
            if _contains_unexpanded_reference(value):
                return _UNSCANNABLE
            parts.append(value)
        index += 1
    if not parts:
        # No recognized body flag found. For a `pr create`/`issue create`
        # invocation, that means the body is interactive or `--fill`-derived
        # -- content that never appears in argv at all -- so fail CLOSED
        # rather than assume it's safe. Every other write subcommand this
        # guard covers (comment/review/edit) requires an explicit body
        # argument from gh itself, so falling back to a raw token scan there
        # still covers shapes this best-effort flag scan didn't anticipate.
        if _is_create_subcommand(tokens):
            return _UNSCANNABLE
        return " ".join(tokens)
    return "\n".join(parts)


def _publication_statements(command: str) -> list[list[str]]:
    """Local syntax only: retain every publication the body scanner handles."""
    return [
        tokens for tokens in _split_statements(command)
        if _is_gh(tokens)
        and (_has_write_subcommand(tokens) or _is_api_comment_or_review_write(tokens))
    ]


def command_publishes_copilot_mention(
    command: str, *, statements: list[list[str]] | None = None,
    deadline: float | None = None, cwd: str | None = None,
) -> str | None:
    """Returns a deny-reason string if *command* should be blocked, else
    ``None``. Two distinct reasons: an actual ``@copilot`` mention, or a
    stdin-sourced body this hook cannot inspect before the command runs."""
    for tokens in _publication_statements(command) if statements is None else statements:
        if deadline is not None and time.monotonic() >= deadline:
            return "unscannable"
        body_text = _extract_body_text(tokens, deadline, cwd=cwd)
        if deadline is not None and time.monotonic() >= deadline:
            return "unscannable"
        if body_text is _UNSCANNABLE:
            # A stdin-sourced body (`--body-file -` / `-F body=@-`) cannot be
            # inspected before the command runs -- fail closed rather than
            # silently pass an unverifiable publish through.
            return "unscannable"
        if MENTION.search(body_text):
            return "mention"
    return None


def adopting_harness(cwd: str | None, deadline: float) -> bool:
    """Require project adoption; a globally installed payload is not consent."""
    raw = cwd or os.environ.get("COPILOT_PROJECT_DIR")
    if not isinstance(raw, str) or not raw:
        return False
    root = Path(raw)
    if not root.is_absolute():
        return False
    root = root.resolve()
    for directory in (root, *root.parents):
        if time.monotonic() >= deadline:
            return False
        settings = directory / ".github" / "copilot"
        enabled = {}
        found = False
        for name in ("settings.json", "settings.local.json"):
            path = settings / name
            if not path.is_file():
                continue
            found = True
            text = _read_bounded(str(path), deadline=deadline, errors="strict")
            if text is None:
                return False
            try:
                value = json.loads(text)
            except ValueError:
                return False
            if not isinstance(value, dict) or not isinstance(value.get("enabledPlugins", {}), dict):
                return False
            enabled.update(value.get("enabledPlugins", {}))
        if found:
            return any(
                isinstance(key, str) and key.startswith("copilot-extensions-harness@")
                and key.split("@", 1)[1] and value is True
                for key, value in enabled.items()
            )
        if (directory / ".git").exists():
            return False
    return False


def main() -> int:
    spec = importlib.util.spec_from_file_location(
        "pretool_killswitch", Path(__file__).with_name("pretool_killswitch.py"),
    )
    if spec is None or spec.loader is None:
        print("[pretool-killswitch] helper unavailable; guard remains enabled", file=sys.stderr)
    else:
        switch = importlib.util.module_from_spec(spec)
        try:
            spec.loader.exec_module(switch)
        except (OSError, ImportError) as exc:
            print(f"[pretool-killswitch] helper unavailable; guard remains enabled: {exc}",
                  file=sys.stderr)
        else:
            if switch.is_active():
                return 0
    deadline = time.monotonic() + DECISION_BUDGET_SECONDS
    try:
        data = json.load(sys.stdin)
        tool_name = data.get("toolName")
        if tool_name not in {"bash", "powershell"}:
            return 0
        args = data.get("toolArgs") or {}
        if isinstance(args, str):
            args = json.loads(args)
        command = args.get("command", "") if isinstance(args, dict) else ""
        # Plugin process CWD may be its payload, not the calling workspace.
        hook_cwd = (
            data.get("cwd") or data.get("workingDirectory")
            or (args.get("cwd") if isinstance(args, dict) else None)
            or os.environ.get("COPILOT_PROJECT_DIR")
        )
    except Exception:
        return 0
    if not isinstance(command, str):
        return 0
    statements = _publication_statements(command)
    if not statements:
        return 0
    if not adopting_harness(hook_cwd, deadline):
        return 0
    plugin_root = Path(
        os.environ.get("COPILOT_PLUGIN_ROOT")
        or Path(__file__).resolve().parent.parent
    )
    target = target_repo_from_manifest(plugin_root, deadline)
    if target is None or not current_repo_matches(target, hook_cwd, deadline=deadline):
        # Scoped to this plugin's own repo -- a session working elsewhere
        # (even with this plugin enabled purely for its instruction
        # projections), or one where the target repo couldn't be resolved
        # at all, is never touched by this guard (fail open, not closed).
        return 0
    reason = command_publishes_copilot_mention(
        command, statements=statements, deadline=deadline, cwd=hook_cwd,
    )
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
                        "body this guard cannot fully inspect within its "
                        "decision budget (including stdin, unresolved "
                        "input, or a slow file). Write the body to a local file "
                        "(--body-file <path>) or pass it inline (--body "
                        "\"...\") so it can be scanned for an @copilot "
                        "mention."
                    ),
                    "decision": "deny",
                    "message": "DENIED: PR/issue body text cannot be fully scanned.",
                },
                separators=(",", ":"),
            )
        )
    return 0


def run_guard(*, wrapped: bool = False) -> int:
    try:
        return main()
    except Exception:
        print("[copilot-extensions-harness] mention guard unavailable; skipped", file=sys.stderr)
        return 1 if wrapped else 0


if __name__ == "__main__":
    raise SystemExit(run_guard(wrapped=sys.argv[1:] == ["--wrapped"]))
