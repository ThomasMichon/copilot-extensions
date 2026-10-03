#!/usr/bin/env python3
"""Run a plugin's pytest suite inside the test-isolation devcontainer.

Phase 1 of the ``devcontainer-test-isolation`` effort
(``efforts/active/devcontainer-test-isolation/README.md``): invokes the
``.devcontainer/test-isolation/devcontainer.json`` spec and runs
``tools/run-plugin-tests.py`` *inside* it, for a real OS-level filesystem/
privilege boundary on top of (not instead of) that runner's existing
process-level containment. Networking is NOT (yet) part of that boundary
-- the container keeps Docker's default bridge with full outbound reach
(a known, named, open design gap; see the effort README's journal).

This is a deliberately separate, opt-in wrapper -- it never replaces
``run-plugin-tests.py`` for contributors who aren't using the devcontainer,
and it never mounts the host checkout into the container. Everything the
container's test run sees is a point-in-time COPY: the host checkout is
only ever read from, never written to, by anything this script spawns.

Usage::

    python tools/run_tests_in_devcontainer.py agent-worktrees
    python tools/run_tests_in_devcontainer.py --changed
    python tools/run_tests_in_devcontainer.py --all -- -k some_filter

Everything after the recognized flags below (or a literal ``--`` anywhere in
the remaining arguments) passes through to ``tools/run-plugin-tests.py``
inside the container, with one normalization (``--base`` rewritten to its
resolved commit SHA) and two exceptions: ``--allow-host-state`` is
rejected outright, and ``--admission-wait``'s host-wide lease loses its
cross-process coordination inside the container.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import shutil
import signal
import subprocess
import sys
import tarfile
import tempfile
import uuid
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
# A NAMED alternate config (``.devcontainer/<name>/devcontainer.json``),
# never the canonical ``.devcontainer/devcontainer.json`` root path --
# this spec is a narrow, test-isolation-only container, not a general
# development environment, but the canonical path is exactly what
# standard "Reopen in Container"/`devcontainer up` auto-discovery (and
# this repo's own Codespaces tooling) picks up with NO explicit choice
# required. Living at the canonical path would silently hand a direct
# user an empty workspace (this volume starts empty; only this wrapper
# ever populates it) instead of a real development environment.
DEVCONTAINER_CONFIG = REPO / ".devcontainer" / "test-isolation" / "devcontainer.json"
CONTAINER_WORKSPACE = "/workspaces/copilot-extensions"
#: Must match ``.devcontainer/test-isolation/devcontainer.json``'s ``remoteUser``/
#: ``containerUser`` -- the non-root user tests actually run as.
REMOTE_USER = "vscode"

# Must match the literal volume name baked into
# ``.devcontainer/test-isolation/devcontainer.json``'s ``workspaceMount``
# -- ``_per_instance_config`` below rewrites this to a unique,
# per-invocation name so concurrent and successive runs each get their
# own isolated, fresh workspace volume instead of silently sharing (and
# accumulating state in) one fixed volume.
BASE_VOLUME_NAME = "copilot-extensions-test-isolation-ws"

# The workspace volume's size is bounded (a tmpfs-backed Docker volume, not
# the default unbounded local-disk volume) so a buggy or adversarial test
# cannot fill the host's Docker storage before teardown runs -- matches the
# bounded-writable-surface model `agent-containers`' own restricted fleet
# uses (`plugins/agent-containers/src/agent_containers/fleet.py`'s tmpfs
# surfaces). The checkout snapshot plus a fresh venv comfortably fits.
WORKSPACE_VOLUME_SIZE = "4g"

# Excluded from the point-in-time copy made into the container even if
# `git ls-files` would otherwise include them: large, host-specific
# artifacts the test run inside the container does not need and should not
# reproduce. Belt-and-suspenders only -- `_tracked_paths` already excludes
# anything gitignored (including `.test-venvs`, which is git-ignored per
# `TESTING.md`). `.devcontainer` is deliberately NOT excluded: excluding
# it while rebuilding the index from the full `HEAD` tree (which still
# lists it) made every in-container checkout appear dirty (`git status`
# reporting it as "deleted"); the host CLI's own per-run config is
# already a separate temporary copy (`_per_instance_config`) regardless.
EXCLUDED_TOP_LEVEL = {
    ".test-venvs",
    "node_modules",
    "__pycache__",
}


def _minimal_repo_selection_env() -> dict[str, str]:
    """A blanket ``GIT_*`` strip used only by
    `_discover_configured_clean_filters`, which runs BEFORE
    `_scrubbed_git_env`'s own overrides exist. `check-attr` never invokes
    a clean filter, but still needs `core.fsmonitor=false` (confirmed
    live that `GIT_OPTIONAL_LOCKS=0` can make even read-only probes
    consult a configured hook) and `GIT_NO_LAZY_FETCH=1`/
    `GIT_NO_REPLACE_OBJECTS=1`."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_CONFIG_COUNT"] = "1"
    env["GIT_CONFIG_KEY_0"] = "core.fsmonitor"
    env["GIT_CONFIG_VALUE_0"] = "false"
    return env


def _discover_configured_clean_filters() -> list[str]:
    """Discover every distinct Git ``filter`` attribute name assigned to
    any TRACKED path, honoring the WORKING-TREE `.gitattributes` (not
    `--cached`, confirmed live to silently miss an uncommitted edit). A
    read-only probe invokes a configured `filter.<name>.clean` for any
    path needing re-hashing, OR `filter.<name>.process` (higher-
    precedence) if ALSO configured -- both confirmed live to execute
    host code during `git status`, so `_scrubbed_git_env` neutralizes
    both for every name here. FAILS CLOSED on any subprocess failure."""
    env = _minimal_repo_selection_env()
    ls = subprocess.run(["git", "-C", str(REPO), "ls-files", "-z"], capture_output=True, timeout=60, env=env)
    if ls.returncode != 0:
        raise SystemExit("could not list tracked files to discover configured clean/process filters")
    check = subprocess.run(
        ["git", "-C", str(REPO), "check-attr", "filter", "--stdin", "-z"],
        input=ls.stdout, capture_output=True, timeout=60, env=env,
    )
    if check.returncode != 0:
        raise SystemExit("could not discover configured clean/process filters via check-attr")
    parts = check.stdout.split(b"\0")
    names: set[str] = set()
    for i in range(0, len(parts) - 2, 3):
        value = parts[i + 2]
        if value and value not in (b"unspecified", b"unset"):
            names.add(os.fsdecode(value))
    return sorted(names)


def _scrubbed_git_env() -> dict[str, str]:
    """Ambient environment with EVERY inherited ``GIT_*`` variable removed
    (matching `tools/agent_bridge_contract_git.py`'s hardened env).
    Forces `GIT_OPTIONAL_LOCKS=0`, `GIT_NO_LAZY_FETCH=1`/
    `GIT_NO_REPLACE_OBJECTS=1`, disabled global/system config,
    `core.fsmonitor=false`, and for every `_discover_configured_clean_
    filters` name, both `filter.<name>.clean` forced to `cat` and
    `filter.<name>.process` forced empty (confirmed live: `process`
    still runs host code even with `clean` alone neutralized)."""
    env = {key: value for key, value in os.environ.items() if not key.upper().startswith("GIT_")}
    env["GIT_TERMINAL_PROMPT"] = "0"
    env["GIT_OPTIONAL_LOCKS"] = "0"
    env["GIT_NO_LAZY_FETCH"] = "1"
    env["GIT_NO_REPLACE_OBJECTS"] = "1"
    env["GIT_CONFIG_GLOBAL"] = os.devnull
    env["GIT_CONFIG_NOSYSTEM"] = "1"
    overrides = [("core.fsmonitor", "false")]
    for name in _discover_configured_clean_filters():
        overrides.append((f"filter.{name}.clean", "cat"))
        overrides.append((f"filter.{name}.process", ""))
    env["GIT_CONFIG_COUNT"] = str(len(overrides))
    for i, (key, value) in enumerate(overrides):
        env[f"GIT_CONFIG_KEY_{i}"] = key
        env[f"GIT_CONFIG_VALUE_{i}"] = value
    return env


def _devcontainer_exe() -> str:
    exe = shutil.which("devcontainer")
    if not exe:
        raise SystemExit(
            "devcontainer CLI not found. Install with `npm i -g @devcontainers/cli`."
        )
    return exe


def _per_instance_config(instance_label: str) -> tuple[Path, str]:
    """Write a copy of ``DEVCONTAINER_CONFIG`` with its workspace volume
    name made unique to this invocation, so each run gets its own fresh,
    isolated workspace instead of reusing one fixed, shared volume.
    Returns the temp config path and the volume name, so the caller can
    remove that exact volume at teardown. Written into a fresh temp
    DIRECTORY as literally ``devcontainer.json`` -- the devcontainer CLI
    rejects any ``--config`` basename other than that or
    ``.devcontainer.json``."""
    volume_name = f"{BASE_VOLUME_NAME}-{instance_label}"
    text = DEVCONTAINER_CONFIG.read_text()
    if BASE_VOLUME_NAME not in text:
        raise SystemExit(
            f"expected volume name '{BASE_VOLUME_NAME}' not found in {DEVCONTAINER_CONFIG}"
        )
    text = text.replace(BASE_VOLUME_NAME, volume_name)
    tmp_dir = Path(tempfile.mkdtemp(prefix="devcontainer-test-isolation-"))
    config_path = tmp_dir / "devcontainer.json"
    config_path.write_text(text)
    return config_path, volume_name


def _create_bounded_volume(volume_name: str) -> None:
    """Create the per-invocation workspace volume up front, as a
    size-bounded tmpfs-backed volume (not the default unbounded local-disk
    volume) -- see ``WORKSPACE_VOLUME_SIZE``. ``devcontainer up`` creates
    the volume implicitly if it doesn't already exist, but implicitly means
    with Docker's own unbounded default; creating it explicitly first with
    these options means ``devcontainer up`` just reuses it instead."""
    res = subprocess.run(
        [
            "docker", "volume", "create",
            "--driver", "local",
            "--opt", "type=tmpfs",
            "--opt", "device=tmpfs",
            "--opt", f"o=size={WORKSPACE_VOLUME_SIZE}",
            volume_name,
        ],
        capture_output=True, text=True, timeout=30,
    )
    if res.returncode != 0:
        raise SystemExit(f"failed to create bounded workspace volume: {res.stderr.strip()}")


def _bring_up(instance_label: str, config_path: Path) -> str:
    """Run ``devcontainer up`` and return the resulting container id."""
    exe = _devcontainer_exe()
    args = [
        exe, "up",
        "--workspace-folder", str(REPO),
        "--config", str(config_path),
        "--id-label", f"devcontainer-test-isolation.instance={instance_label}",
    ]
    res = subprocess.run(args, capture_output=True, text=True, timeout=1800)
    if res.returncode != 0:
        raise SystemExit(f"devcontainer up failed: {res.stderr.strip() or res.stdout.strip()}")
    container_id = None
    for line in res.stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        container_id = obj.get("containerId") or container_id
    if not container_id:
        raise SystemExit("could not determine containerId from `devcontainer up` output")
    return container_id


def _tracked_paths(*, include_untracked: bool) -> list[str]:
    """Repo-relative paths of files the snapshot should contain --
    deliberately NOT every file physically present under ``REPO``. Default
    (``include_untracked=False``) is git-TRACKED files only (``git
    ls-files --cached``): there's no blanket `.gitignore` rule for
    `.env`-style config, so an untracked-but-not-ignored secret file would
    otherwise be copied into a container with outbound network access --
    tracked files are the set contributors/CI already trust to keep
    secrets out of the repository. ``include_untracked=True`` (the
    wrapper's ``--include-untracked`` flag) additionally includes
    untracked-but-not-gitignored files via ``--others --exclude-standard``.

    Known, accepted residual exposure: a tracked path's CURRENT on-disk
    content is copied (uncommitted edits included), not the
    last-committed blob, so a secret pasted into an otherwise-tracked
    file and never committed is still copied in.
    """
    args = ["git", "-C", str(REPO), "ls-files", "-z", "--cached"]
    if include_untracked:
        args += ["--others", "--exclude-standard"]
    res = subprocess.run(args, capture_output=True, timeout=60, env=_scrubbed_git_env())
    if res.returncode != 0:
        raise SystemExit(
            f"git ls-files failed: {res.stderr.decode(errors='replace').strip()}"
        )
    # `os.fsdecode` (surrogate-escape), not a plain UTF-8 `.decode()` --
    # a git-tracked path on Linux is arbitrary bytes, and a plain decode
    # would raise `UnicodeDecodeError` outright for a valid tracked
    # filename that happens not to be valid UTF-8, aborting the whole
    # snapshot over one oddly-named file.
    paths = [p for p in os.fsdecode(res.stdout).split("\0") if p]
    excluded_prefixes = tuple(f"{name}/" for name in EXCLUDED_TOP_LEVEL)
    return [
        p for p in paths
        if p not in EXCLUDED_TOP_LEVEL and not p.startswith(excluded_prefixes)
    ]


def _warn_about_dirty_tracked_files() -> None:
    """Print a clear, explicit stderr warning naming every tracked file
    with an uncommitted modification -- the tracked-files-only boundary
    is about which PATHS are copied, not which BYTES; a secret pasted
    into an otherwise-tracked file and never committed is still copied
    in. Fails CLOSED (raises) if ``git status`` itself cannot be run.
    ``--ignore-submodules=all`` is required: ``git status`` otherwise
    recursively inspects any initialized submodule, consulting a
    SUBMODULE-specific `filter.<name>.clean`/`.process` assignment
    `_discover_configured_clean_filters` never covers (superproject
    tracked paths only). The snapshot never copies submodule contents
    anyway (see `_write_tar_of_repo`)."""
    res = subprocess.run(
        ["git", "-C", str(REPO), "status", "--porcelain=v1", "--untracked-files=no",
         "--ignore-submodules=all"],
        capture_output=True, timeout=30, env=_scrubbed_git_env(),
    )
    if res.returncode != 0:
        raise SystemExit(
            "failed to check for uncommitted changes to tracked files "
            f"(refusing to build a snapshot with an unknown dirty state): "
            f"{res.stderr.decode(errors='replace').strip()}"
        )
    dirty = [
        line[3:] for line in os.fsdecode(res.stdout).splitlines() if line.strip()
    ]
    if not dirty:
        return
    print(
        "warning: the following tracked file(s) have uncommitted changes and "
        "their CURRENT on-disk content (not the last-committed version) will "
        "be copied into the test-isolation container, which has outbound "
        "network access -- do not run this against a checkout with an "
        "uncommitted secret pasted into an otherwise-tracked file:",
        file=sys.stderr,
    )
    for path in dirty:
        print(f"  {path}", file=sys.stderr)


def _warn_about_hidden_tracked_file_flags() -> None:
    """Print a clear, explicit stderr warning naming every tracked file
    whose index entry carries ``assume-unchanged`` or ``skip-worktree``.
    ``git status`` is NOT fail-closed for these paths: both flags
    suppress reporting an on-disk difference, while the snapshot still
    archives current bytes regardless. ``git ls-files -v`` marks a
    flagged entry with a lowercase letter (assume-unchanged) or
    uppercase ``S`` (skip-worktree); ordinary is uppercase (``H``). Fails
    CLOSED on a failed ``ls-files``."""
    res = subprocess.run(
        ["git", "-C", str(REPO), "ls-files", "-v", "--cached"],
        capture_output=True, timeout=60, env=_scrubbed_git_env(),
    )
    if res.returncode != 0:
        raise SystemExit(
            "failed to check tracked files for assume-unchanged/skip-worktree "
            f"flags (refusing to build a snapshot with an unknown state): "
            f"{res.stderr.decode(errors='replace').strip()}"
        )
    flagged: list[str] = []
    for line in os.fsdecode(res.stdout).splitlines():
        if not line.strip():
            continue
        flag, _, path = line.partition(" ")
        if flag.islower() or flag == "S":
            flagged.append(path)
    if not flagged:
        return
    print(
        "warning: the following tracked file(s) carry a Git "
        "assume-unchanged/skip-worktree flag -- `git status` will NOT report "
        "an on-disk modification for them, but their CURRENT (possibly "
        "locally customized) content is still copied into the "
        "test-isolation container, which has outbound network access:",
        file=sys.stderr,
    )
    for path in flagged:
        print(f"  {path}", file=sys.stderr)


# Every `tools/run-plugin-tests.py` flag that consumes a SEPARATE following
# token as its value (as opposed to a bare `store_true` flag, or the
# single-token `--flag=value` form, which `.startswith("-")` already
# catches below) -- kept in sync by hand with that script's own
# `argparse` definitions, mirrored here only to tell a flag's value token
# apart from a positional plugin name, never to fully re-parse its CLI.
_VALUE_CONSUMING_FLAGS = frozenset({
    "--base", "-k", "--admission-wait", "--timeout", "--subsuite-timeout",
    "--plugin-timeout", "--test-timeout", "--max-files-per-sub-suite",
    "--max-processes", "--max-memory-mb", "--max-temp-mb", "--exclude",
})

# Every bare (`store_true`) `tools/run-plugin-tests.py` flag -- kept in
# sync by hand alongside `_VALUE_CONSUMING_FLAGS` above, for the same
# reason: distinguishing a recognized flag from a positional plugin name,
# never fully re-parsing that runner's CLI.
_BARE_FLAGS = frozenset({
    "--all", "--changed", "--reinstall", "--guards", "--collect-only",
    "--list", "--pre-push", "--allow-explicit-tiers", "--allow-host-state",
})

_ALL_LONG_FLAGS = _VALUE_CONSUMING_FLAGS | _BARE_FLAGS


def _canonicalize_flag(name: str) -> str:
    """Resolve a bare long-flag token to its canonical name via
    argparse's own unambiguous-prefix abbreviation (e.g. ``--bas`` ->
    ``--base``) against `_ALL_LONG_FLAGS` -- without this, an abbreviated
    flag goes unrecognized by `_resolve_base_ref`/`_changed_mode_active`."""
    if name in _ALL_LONG_FLAGS or not name.startswith("--") or len(name) <= 2:
        return name
    matches = [flag for flag in _ALL_LONG_FLAGS if flag.startswith(name)]
    return matches[0] if len(matches) == 1 else name


def _resolve_base_ref(passthrough: list[str]) -> str:
    """Best-effort extraction of the ``--base`` value a passthrough
    invocation will use, so ``_materialized_git_dir`` includes exactly
    that ref's closure. Falls back to the runner's own default when
    absent, mirrors argparse's last-occurrence-wins, and recognizes an
    abbreviation -- see `_canonicalize_flag`."""
    resolved = "origin/main"
    for i, arg in enumerate(passthrough):
        name, eq, value = arg.partition("=")
        if _canonicalize_flag(name) != "--base":
            continue
        if eq:
            resolved = value
        elif i + 1 < len(passthrough):
            resolved = passthrough[i + 1]
    return resolved


def _changed_mode_active(passthrough: list[str]) -> bool:
    """Whether a ``tools/run-plugin-tests.py`` invocation with these
    passthrough args resolves targets via ``changed_plugins()`` -- true
    for an explicit ``--changed``, AND that runner's own default (no
    ``--all``, no explicit plugin names). Only then does an unresolvable
    ``--base`` matter."""
    has_all = False
    has_positional = False
    skip_next = False
    for arg in passthrough:
        if skip_next:
            skip_next = False
            continue
        # A single-token `--flag=value` form never consumes a SEPARATE
        # following token, so no canonicalization is needed here.
        canonical = _canonicalize_flag(arg) if "=" not in arg else arg
        if canonical == "--all":
            has_all = True
        elif canonical in _VALUE_CONSUMING_FLAGS:
            skip_next = True
        elif arg.startswith("-"):
            continue
        else:
            has_positional = True
    return not has_all and not has_positional


def _git_rev_parse(ref: str) -> str | None:
    """Resolve ``ref`` to a commit sha via the scrubbed environment.
    Returns ``None`` (rather than raising) when unresolvable -- the
    CALLER decides tolerance: `_materialized_git_dir` treats it as fatal
    in changed-selection mode, tolerant otherwise. Peels to
    ``ref^{commit}``: plain ``rev-parse --verify`` accepts ANY object
    type, but the downstream diff needs a commit-ish.
    ``--end-of-options`` keeps a ``-``-prefixed ref from misreading as a
    flag."""
    res = subprocess.run(
        ["git", "-C", str(REPO), "rev-parse", "--verify", "--end-of-options", f"{ref}^{{commit}}"],
        capture_output=True, text=True, timeout=30, env=_scrubbed_git_env(),
    )
    return res.stdout.strip() if res.returncode == 0 else None


def _rewrite_base_to_resolved_sha(passthrough: list[str]) -> list[str]:
    """Replace any ``--base`` value (bare, ``=value``, or an unambiguous
    abbreviation -- see `_canonicalize_flag`) in ``passthrough`` with its
    resolved commit SHA, when it resolves on the host -- APPENDING an
    explicit ``--base <sha>`` instead when absent entirely (this runner's
    own implicit default, ``origin/main``). A bundle clone never
    preserves a remote-tracking ref as a named ref -- named directly or
    via a ref-relative expression (e.g. ``origin/dev~1``) -- EITHER
    resolves fine on the host but leaves the in-container command with
    no working named ref; a bare SHA has no such problem. Without the
    append case, the single MOST COMMON invocation would have nothing to
    rewrite and silently run no suites. Leaves ``passthrough`` unchanged
    when the base doesn't resolve locally (handled via
    `_materialized_git_dir`'s own fail-loud guard)."""
    resolved_sha = _git_rev_parse(_resolve_base_ref(passthrough))
    if resolved_sha is None:
        return passthrough
    has_base_flag = any(
        _canonicalize_flag(arg.partition("=")[0]) == "--base" for arg in passthrough
    )
    if not has_base_flag:
        # Only append when changed-selection is actually active -- an
        # `--all` run or an explicit plugin name never consults `--base`
        # at all, so adding it there would be noise with no effect.
        if not _changed_mode_active(passthrough):
            return passthrough
        return [*passthrough, "--base", resolved_sha]
    rewritten: list[str] = []
    skip_next = False
    for arg in passthrough:
        if skip_next:
            rewritten.append(resolved_sha)
            skip_next = False
            continue
        name, eq, _value = arg.partition("=")
        if "=" not in arg and _canonicalize_flag(arg) == "--base":
            rewritten.append(arg)
            skip_next = True
        elif eq and _canonicalize_flag(name) == "--base":
            rewritten.append(f"{name}={resolved_sha}")
        else:
            rewritten.append(arg)
    return rewritten


# A fresh, credential-free `.git/config` written into every materialized
# copy (see `_materialized_git_dir` below) -- deliberately NOT a copy of
# the host's own config, which may embed an authenticated remote URL,
# `credential.helper` settings, or other credential-bearing values. None of
# that is needed for `git diff`/`git status`/`git rev-parse` against
# already-resolved local refs; losing it only matters for `fetch`/`push`
# network operations this wrapper's own `git` calls never perform.
_MINIMAL_GIT_CONFIG = (
    "[core]\n"
    "\trepositoryformatversion = 0\n"
    "\tfilemode = true\n"
    "\tbare = false\n"
    "\tlogallrefupdates = true\n"
)


def _materialized_git_dir(stack: contextlib.ExitStack, passthrough: list[str]) -> Path:
    """Return a path to a self-contained ``.git`` directory to copy into
    the container, containing ONLY the object closure of ``HEAD`` and the
    ``--changed`` diff base -- never the full repository history.
    Copying the full local git database (every branch, stash, reflog, and
    unreachable object) into a container with outbound networking would
    let an adversarial/buggy test exfiltrate local-only content unrelated
    to the plugin suite being run. Instead: ``git bundle create`` with
    only ``HEAD`` and (when it resolves locally) the ``--base`` ref
    `run-plugin-tests.py --changed`` will diff against, then ``git clone
    --bare`` that bundle into a fresh directory. ``main`` rewrites
    ``--base``'s own value to this same resolved SHA first (see
    `_rewrite_base_to_resolved_sha`), so the clone needs no named ref.
    The index is rebuilt from ``HEAD`` (``git read-tree HEAD``) rather
    than copied: the host's real index can reference a staged blob
    unreachable from both tips, which the bundle would then be missing --
    a copied index pointing at a missing object breaks `git diff`/`status`
    outright. Staging isn't preserved, but every modification is still
    visible as an ordinary working-tree difference (`_tracked_paths`
    copies the file's CURRENT content regardless). ``config`` is replaced
    with a fresh, credential-free one and ``hooks`` is dropped."""
    tmp_dir = Path(tempfile.mkdtemp(prefix="devcontainer-test-isolation-git-"))
    stack.callback(shutil.rmtree, tmp_dir, ignore_errors=True)
    bundle_file = tmp_dir / "snapshot.bundle"
    merged = tmp_dir / ".git"
    # An explicitly empty template directory for `git clone` below --
    # without it, `git clone` honors the HOST's global `init.templateDir`,
    # which can plant arbitrary files (not just `hooks`/`config`, both of
    # which are otherwise explicitly handled below) into the "clean"
    # synthetic `.git` directory, which then ships into the
    # network-enabled container.
    empty_template_dir = tmp_dir / "empty-template"
    empty_template_dir.mkdir()

    base_ref = _resolve_base_ref(passthrough)
    base_resolves = _git_rev_parse(base_ref) is not None
    # Changed-selection mode (explicit `--changed`, or the default with no
    # `--all`/plugin names) is the one mode that diffs against `base_ref`.
    # `run-plugin-tests.py`'s `changed_plugins()` ignores a nonzero `git
    # diff` and reports an EMPTY target set rather than erroring, so EITHER
    # an unresolvable base OR one resolving to an orphan/unrelated-history
    # commit (no shared ancestor -> `git diff <base>...HEAD` fails with "no
    # merge base") silently degrades to "No plugin suites to run." instead
    # of surfacing the real problem. Fail loudly here instead, before any
    # snapshot work; an `--all`/explicit-plugin run never consults
    # `base_ref` so an unresolvable default must not block those.
    if _changed_mode_active(passthrough):
        if not base_resolves:
            raise SystemExit(
                f"changed-selection mode is active but its diff base "
                f"({base_ref!r}) does not resolve on the host -- refusing "
                "to silently build a snapshot that would make the "
                "in-container run report \"no plugin suites to run\" "
                "instead of the real problem. Fetch or correct --base."
            )
        merge_base_res = subprocess.run(
            ["git", "-C", str(REPO), "merge-base", base_ref, "HEAD"],
            capture_output=True, text=True, timeout=30, env=_scrubbed_git_env(),
        )
        if merge_base_res.returncode != 0:
            raise SystemExit(
                f"changed-selection mode is active but {base_ref!r} and "
                "HEAD share no merge base (orphan/unrelated history) -- "
                "refusing to silently build a snapshot that would make the "
                "in-container three-dot diff fail. Correct --base."
            )
    # Only include the base ref's closure when changed-selection actually
    # consults it -- an `--all` run or an explicit plugin name never uses
    # `base_ref` at all, so bundling it there would needlessly widen the
    # minimal-history boundary with unrelated commits/trees/blobs reachable
    # from a base that may have diverged significantly from `HEAD`.
    bundle_refs = (
        ["HEAD", base_ref]
        if base_resolves and _changed_mode_active(passthrough)
        else ["HEAD"]
    )

    bundle_res = subprocess.run(
        ["git", "-C", str(REPO), "bundle", "create", str(bundle_file), *bundle_refs],
        capture_output=True, text=True, timeout=300, env=_scrubbed_git_env(),
    )
    if bundle_res.returncode != 0:
        raise SystemExit(f"git bundle create failed: {bundle_res.stderr.strip()}")

    clone_res = subprocess.run(
        ["git", "clone", "--bare", "--quiet", f"--template={empty_template_dir}",
         str(bundle_file), str(merged)],
        capture_output=True, text=True, timeout=120, env=_scrubbed_git_env(),
    )
    if clone_res.returncode != 0:
        raise SystemExit(f"git clone (from bundle) failed: {clone_res.stderr.strip()}")

    read_tree_res = subprocess.run(
        ["git", f"--git-dir={merged}", "read-tree", "HEAD"],
        capture_output=True, text=True, timeout=60, env=_scrubbed_git_env(),
    )
    if read_tree_res.returncode != 0:
        raise SystemExit(f"git read-tree HEAD failed: {read_tree_res.stderr.strip()}")

    (merged / "config").write_text(_MINIMAL_GIT_CONFIG)
    shutil.rmtree(merged / "hooks", ignore_errors=True)
    return merged


def _write_tar_of_repo(dest: Path, passthrough: list[str], *, include_untracked: bool) -> None:
    """Write a tarball of the host checkout to ``dest`` on disk (never
    held in memory as one ``bytes`` object). Only ever READS the host
    tree -- ``.git`` is handled separately via ``_materialized_git_dir``;
    everything else comes from ``_tracked_paths``, so gitignored (and,
    unless ``include_untracked``) untracked files are never included.

    An absent path (e.g. an unstaged deletion, which ``ls-files --cached``
    still lists) is checked via ``os.path.lexists`` and silently skipped.
    An initialized submodule (a ``160000``-mode path that's a real
    directory on disk) is added as an empty directory entry only
    (``recursive=False``), never its contents. A tracked path's own
    ANCESTOR directory can be replaced with a symlink to outside
    ``REPO`` -- confirmed live that ``lexists`` alone misses this (it
    checks only the FINAL component); each path's PARENT directory (not
    the leaf, which may legitimately be a tracked symlink) has its real
    path checked against ``REPO``'s, failing closed on an escape.

    Also warns (``_warn_about_dirty_tracked_files``,
    ``_warn_about_hidden_tracked_file_flags``) before copying anything.
    """
    _warn_about_dirty_tracked_files()
    _warn_about_hidden_tracked_file_flags()
    real_repo = Path(os.path.realpath(REPO))
    with tarfile.open(dest, mode="w") as tar, contextlib.ExitStack() as stack:
        tar.add(_materialized_git_dir(stack, passthrough), arcname=".git")
        for rel_path in _tracked_paths(include_untracked=include_untracked):
            abs_path = REPO / rel_path
            if not os.path.lexists(abs_path):
                continue
            real_parent = Path(os.path.realpath(abs_path.parent))
            if real_parent != real_repo and real_repo not in real_parent.parents:
                raise SystemExit(
                    f"tracked path {rel_path!r} has an ancestor directory that "
                    "resolves outside the repository root (replaced with a "
                    "symlink) -- refusing to archive it rather than silently "
                    "copy external content into the test-isolation container."
                )
            tar.add(abs_path, arcname=rel_path, recursive=False)


def _populate_workspace(container_id: str, passthrough: list[str], *, include_untracked: bool) -> None:
    """Copy a point-in-time snapshot of the host checkout into the
    container's workspace VOLUME (never a host bind). A freshly created
    Docker volume is root-owned, so a one-off root ``chmod`` opens its
    empty PERMISSION bits first (root remains OWNER; `--cap-drop=ALL`
    means even root can't `chown`). Extraction runs AS ``vscode``, so
    the checkout ends up natively ``vscode``-owned -- matters since
    Git's "dubious ownership" check inspects the working-tree ROOT's
    owner, and the mountpoint stays root-owned for the container's
    lifetime; the devcontainer spec's `safe.directory` exemption covers
    that gap. The permission-opening pass only targets files/directories,
    never a symlink (`chmod` on one dereferences it)."""
    chmod_root = subprocess.run(
        ["docker", "exec", "-u", "root", container_id,
         "chmod", "0777", CONTAINER_WORKSPACE],
        capture_output=True, text=True, timeout=60,
    )
    if chmod_root.returncode != 0:
        raise SystemExit(
            f"failed to open up the empty container workspace volume: "
            f"{chmod_root.stderr.strip()}"
        )
    with tempfile.NamedTemporaryFile(
        prefix="devcontainer-test-isolation-snapshot-", suffix=".tar",
    ) as tar_file:
        _write_tar_of_repo(Path(tar_file.name), passthrough, include_untracked=include_untracked)
        tar_file.seek(0)
        res = subprocess.run(
            [
                "docker", "exec", "-i", "-u", REMOTE_USER, container_id,
                "tar", "-xf", "-", "-C", CONTAINER_WORKSPACE,
            ],
            stdin=tar_file,
            capture_output=True,
            timeout=600,
        )
    if res.returncode != 0:
        raise SystemExit(
            f"failed to populate container workspace: {res.stderr.decode(errors='replace').strip()}"
        )
    chmod = subprocess.run(
        ["docker", "exec", "-u", REMOTE_USER, container_id,
         "find", CONTAINER_WORKSPACE, "-mindepth", "1",
         "(", "-type", "f", "-o", "-type", "d", ")", "-exec",
         "chmod", "u+rwX", "{}", "+"],
        capture_output=True, text=True, timeout=120,
    )
    if chmod.returncode != 0:
        raise SystemExit(f"failed to open up container workspace permissions: {chmod.stderr.strip()}")


def _run_tests(container_id: str, config_path: Path, passthrough: list[str]) -> int:
    exe = _devcontainer_exe()
    args = [
        exe, "exec",
        "--workspace-folder", str(REPO),
        "--config", str(config_path),
        "--container-id", container_id,
        "--", "python", "tools/run-plugin-tests.py", *passthrough,
    ]
    res = subprocess.run(args)
    return res.returncode


def _tear_down(container_id: str, volume_name: str) -> None:
    """Remove the container, then the per-invocation volume it owned --
    both failures are surfaced, since a failed removal leaves a live
    container running or an orphaned volume on the host. Each removal is
    individually guarded against ``subprocess.SubprocessError``/
    ``OSError`` so an exception from one can never skip the other. Every
    ``docker`` call runs with ``start_new_session=True``: without it, a
    terminal Ctrl-C's ``SIGINT`` reaches these children too (same
    foreground process group), which retain the default handler --
    `_cleanup_signals_deferred` only protects the Python PARENT."""
    errors: list[str] = []
    try:
        res = subprocess.run(
            ["docker", "rm", "-f", container_id],
            capture_output=True, text=True, timeout=60, start_new_session=True,
        )
        if res.returncode != 0:
            errors.append(f"failed to remove container {container_id}: {res.stderr.strip()}")
    except (subprocess.SubprocessError, OSError) as exc:
        errors.append(f"failed to remove container {container_id}: {exc}")
    try:
        vol = subprocess.run(
            ["docker", "volume", "rm", volume_name],
            capture_output=True, text=True, timeout=60, start_new_session=True,
        )
        if vol.returncode != 0:
            errors.append(f"failed to remove volume {volume_name}: {vol.stderr.strip()}")
    except (subprocess.SubprocessError, OSError) as exc:
        errors.append(f"failed to remove volume {volume_name}: {exc}")
    if errors:
        raise SystemExit("; ".join(errors))


def _cleanup_orphan(instance_label: str, volume_name: str) -> None:
    """Best-effort cleanup when ``devcontainer up`` itself fails (timeout,
    a failure during ``onCreateCommand``, or unparseable output): a
    container may have been created under this instance's id-label even
    though ``_bring_up`` never returned an id. Finds and removes it by
    label, then removes the volume. Every subprocess call is individually
    guarded so one failing step never skips the rest, and this function
    itself never raises. Every call runs with ``start_new_session=True``
    (see `_tear_down`'s docstring)."""
    container_ids: list[str] = []
    try:
        find = subprocess.run(
            ["docker", "ps", "-aq", "--filter",
             f"label=devcontainer-test-isolation.instance={instance_label}"],
            capture_output=True, text=True, timeout=30, start_new_session=True,
        )
        if find.returncode != 0:
            print(f"warning: orphan-cleanup 'docker ps' failed: {find.stderr.strip()}", file=sys.stderr)
        else:
            container_ids = find.stdout.split()
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"warning: orphan-cleanup 'docker ps' failed: {exc}", file=sys.stderr)
    for container_id in container_ids:
        try:
            rm = subprocess.run(
                ["docker", "rm", "-f", container_id],
                capture_output=True, text=True, timeout=60, start_new_session=True,
            )
            if rm.returncode != 0:
                print(f"warning: orphan-cleanup failed to remove container {container_id}: "
                      f"{rm.stderr.strip()}", file=sys.stderr)
        except (subprocess.SubprocessError, OSError) as exc:
            print(f"warning: orphan-cleanup failed to remove container {container_id}: {exc}",
                  file=sys.stderr)
    try:
        vol = subprocess.run(
            ["docker", "volume", "rm", volume_name],
            capture_output=True, text=True, timeout=60, start_new_session=True,
        )
        if vol.returncode != 0:
            print(f"warning: orphan-cleanup failed to remove volume {volume_name}: "
                  f"{vol.stderr.strip()}", file=sys.stderr)
    except (subprocess.SubprocessError, OSError) as exc:
        print(f"warning: orphan-cleanup failed to remove volume {volume_name}: {exc}", file=sys.stderr)


class _TerminationRequested(BaseException):
    """Raised so the wrapper's own try/finally cleanup runs instead of the
    process dying silently on ``SIGTERM`` (whose default action terminates
    immediately, bypassing every ``finally`` block including container/
    volume teardown -- `_cleanup_orphan` can't find a leaked resource from
    a DIFFERENT run's random instance label). A ``BaseException`` subclass
    (matching ``KeyboardInterrupt``'s own placement) so the existing
    ``except BaseException`` cleanup paths already handle it."""


def _raise_on_sigterm(signum: int, frame: object) -> None:
    raise _TerminationRequested(f"received signal {signum}")


# `SIGINT` (Ctrl-C) already becomes `KeyboardInterrupt` via Python's own
# default handling -- only `SIGTERM` needs `_raise_on_sigterm` above. Both
# still need deferring during cleanup itself (`_cleanup_signals_deferred`),
# so a REPEAT signal mid-cleanup can't interrupt it partway.
_CLEANUP_DEFERRED_SIGNALS = (signal.SIGINT, signal.SIGTERM)


@contextlib.contextmanager
def _cleanup_signals_deferred():
    """Defer both `_CLEANUP_DEFERRED_SIGNALS` for a cleanup step
    (``_tear_down``/``_cleanup_orphan``): RECORD receipt instead of acting
    immediately, restore the previous handlers once cleanup finishes, then
    raise `_TerminationRequested` if one was recorded AND no exception is
    propagating. Plain ``signal.SIG_IGN`` would DISCARD a signal (not
    defer it), letting `main` silently return 0 for a cancelled run;
    replaying unconditionally could instead REPLACE a genuine failure
    already propagating -- a single ``sys.exc_info()`` check covers both.

    Installing/restoring TWO handlers isn't atomic -- a signal mid-swap
    can hit whichever OLD handler is still active for the second one
    (confirmed live). ``pthread_sigmask`` blocks both for each swap,
    restoring the EXACT prior mask via ``SIG_SETMASK`` (never
    ``SIG_UNBLOCK``, which would silently unblock a caller-pre-blocked
    signal, confirmed live). Such a signal arriving during ``yield``
    stays PENDING until unblocked -- unblocking only at the FINAL
    restore step would deliver it to the already-restored OLD handler,
    bypassing the replay decision (confirmed live); exit therefore
    flushes first (brief ``SIG_UNBLOCK``
    while `_record` is still installed), then restores handlers in their
    own separately-masked swap."""
    received: list[int] = []

    def _record(signum: int, frame: object) -> None:
        received.append(signum)

    entry_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _CLEANUP_DEFERRED_SIGNALS)
    try:
        previous = {sig: signal.signal(sig, _record) for sig in _CLEANUP_DEFERRED_SIGNALS}
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, entry_mask)
    try:
        yield
    finally:
        # Flush any signal already pending (e.g. a caller-pre-blocked
        # mask) to `_record` -- still active here -- before touching
        # the handlers at all.
        caller_mask = signal.pthread_sigmask(signal.SIG_UNBLOCK, _CLEANUP_DEFERRED_SIGNALS)
        signal.pthread_sigmask(signal.SIG_SETMASK, caller_mask)
        # Now restore the old handlers, atomically.
        pre_restore_mask = signal.pthread_sigmask(signal.SIG_BLOCK, _CLEANUP_DEFERRED_SIGNALS)
        try:
            for sig, handler in previous.items():
                signal.signal(sig, handler)
        finally:
            signal.pthread_sigmask(signal.SIG_SETMASK, pre_restore_mask)
        if received and sys.exc_info()[0] is not None:
            print(
                f"warning: received signal {received[0]} during cleanup, "
                "but a failure is already propagating -- not replacing "
                "it; the signal itself is not re-raised",
                file=sys.stderr,
            )
        elif received:
            raise _TerminationRequested(f"received signal {received[0]} during cleanup")


def main(argv: list[str] | None = None) -> int:
    # Converts a SIGTERM into a normal raised exception so this
    # function's own try/finally cleanup runs -- see
    # `_TerminationRequested`'s docstring. SIGINT needs no equivalent
    # handler (Python already raises `KeyboardInterrupt`) --
    # `_cleanup_signals_deferred` protects against a REPEAT of either
    # during cleanup itself. The previous handler is restored in the
    # outer `finally` below, since `main` is also invoked in-process by
    # this module's own tests.
    previous_sigterm_handler = signal.signal(signal.SIGTERM, _raise_on_sigterm)
    try:
        ap = argparse.ArgumentParser(
            description=(
                "Run tools/run-plugin-tests.py inside the test-isolation devcontainer."
            ),
        )
        ap.add_argument("--keep", action="store_true",
                         help="leave the container running after the test run (debugging)")
        ap.add_argument("--include-untracked", action="store_true",
                         help=(
                             "also copy untracked-but-not-gitignored files into the "
                             "snapshot (default: tracked files only -- an untracked "
                             "secret-shaped file sitting in the working tree is not "
                             "necessarily gitignored, so this is opt-in, not default)"
                         ))
        ns, passthrough = ap.parse_known_args(argv)
        # "--" is argparse's own flags/positionals separator, not a real
        # run-plugin-tests.py argument -- strip every occurrence (not just a
        # leading one), since it can appear anywhere in the extras list
        # (e.g. ``--all -- -k some_filter`` leaves it in the MIDDLE).
        passthrough = [arg for arg in passthrough if arg != "--"]
        # `--allow-host-state`'s documented contract (preserve the
        # caller's real HOME/config/credentials) cannot be honored here --
        # the container always gets a fresh, credential-free tmpfs $HOME
        # by design. Reject rather than silently proceed without the
        # credentials a credential-dependent test asked for.
        if any(
            _canonicalize_flag(arg.partition("=")[0]) == "--allow-host-state"
            for arg in passthrough
        ):
            raise SystemExit(
                "--allow-host-state is not supported through "
                "tools/run_tests_in_devcontainer.py: its documented contract "
                "(preserve the caller's real HOME/config/credentials) cannot "
                "be honored here -- the container always gets a fresh, "
                "credential-free tmpfs $HOME by design. Run "
                "tools/run-plugin-tests.py directly (outside the "
                "devcontainer) for an --allow-host-state check instead."
            )
        # Rewriting `--base` to its resolved SHA here (before EITHER the
        # snapshot is built or the in-container command is assembled) means
        # both consistently see and use the SAME resolved commit, including
        # for a ref-relative expression that would otherwise fail to resolve
        # again inside the materialized bundle clone -- see
        # `_rewrite_base_to_resolved_sha`'s own docstring.
        passthrough = _rewrite_base_to_resolved_sha(passthrough)

        instance_label = uuid.uuid4().hex[:12]
        config_path, volume_name = _per_instance_config(instance_label)
        # `container_id` doubles as the lifecycle marker the `finally`
        # below uses to pick cleanup: still `None` means `_bring_up`
        # never returned one (orphan cleanup, regardless of `--keep`), a
        # real id means normal teardown. One try/finally spanning the
        # whole lifecycle (vs. two separate blocks with a gap) means no
        # window where a SIGTERM/SIGINT could raise before any cleanup
        # guard is active and leak both the container and its volume.
        container_id: str | None = None
        result: int | None = None
        primary_failed = False
        try:
            try:
                _create_bounded_volume(volume_name)
                container_id = _bring_up(instance_label, config_path)
                _populate_workspace(container_id, passthrough, include_untracked=ns.include_untracked)
                result = _run_tests(container_id, config_path, passthrough)
                primary_failed = result != 0
            except BaseException:
                primary_failed = True
                raise
            finally:
                # The primary path's result/exception must win over a
                # secondary cleanup failure -- a bare `finally` raising
                # would otherwise silently discard it. `primary_failed`
                # tells which case this is: report (don't re-raise) a
                # cleanup failure once the primary already failed; raise
                # it directly only when the primary truly succeeded.
                if container_id is None:
                    with _cleanup_signals_deferred():
                        _cleanup_orphan(instance_label, volume_name)
                elif not ns.keep:
                    try:
                        with _cleanup_signals_deferred():
                            _tear_down(container_id, volume_name)
                    except BaseException as teardown_exc:
                        if not primary_failed:
                            raise
                        print(f"warning: teardown also failed: {teardown_exc}", file=sys.stderr)
            return result
        finally:
            shutil.rmtree(config_path.parent, ignore_errors=True)
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)



if __name__ == "__main__":
    raise SystemExit(main())
