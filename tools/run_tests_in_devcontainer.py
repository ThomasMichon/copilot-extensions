#!/usr/bin/env python3
"""Run a plugin's pytest suite inside the test-isolation devcontainer.

Phase 1 of the ``devcontainer-test-isolation`` effort
(``efforts/active/devcontainer-test-isolation/README.md``): invokes the
``.devcontainer/test-isolation/devcontainer.json`` spec and runs
``tools/run-plugin-tests.py`` *inside* it, a real OS-level filesystem/
privilege boundary atop that runner's process-level containment. Phase 2
scoped networking too: deps resolve with network reach
(``--prepare-only``), then every network disconnects before the real
test pass.

This is a deliberately separate, opt-in wrapper -- it never replaces
``run-plugin-tests.py`` for contributors who aren't using the devcontainer,
and it never mounts the host checkout into the container. Everything the
container's test run sees is a point-in-time COPY: the host checkout is
only ever read, never written to, by anything this script spawns.

Usage::

    python tools/run_tests_in_devcontainer.py agent-worktrees
    python tools/run_tests_in_devcontainer.py --changed
    python tools/run_tests_in_devcontainer.py --all -- -k some_filter

Everything after the recognized flags below (or a literal ``--`` anywhere in
the remaining arguments) passes through to ``tools/run-plugin-tests.py``
inside the container, with two normalizations (``--base`` rewritten to its
resolved SHA; ``--reinstall`` applied to the prep pass then stripped from
the real pass) and two exceptions: ``--allow-host-state`` is rejected, and
an over-ceiling resource-limit override is rejected. ``--admission-wait``
is also consulted for a HOST-side lease acquisition before any work begins.
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
import _devcontainer_host_admission as _admission
import _devcontainer_network_scope as _net_scope

REPO = Path(__file__).resolve().parents[1]
# A NAMED alternate config (``.devcontainer/<name>/devcontainer.json``),
# never the canonical root path: a narrow, test-isolation-only container,
# not a general development environment, but the canonical path is what
# standard "Reopen in Container" auto-discovery picks up with NO
# explicit choice required -- living there would silently hand a direct
# user an empty workspace (only this wrapper ever populates it).
DEVCONTAINER_CONFIG = REPO / ".devcontainer" / "test-isolation" / "devcontainer.json"
CONTAINER_WORKSPACE = "/workspaces/copilot-extensions"
REMOTE_USER = "vscode"  #: matches the devcontainer spec's remoteUser/containerUser

# Must match the literal volume name baked into the devcontainer spec's
# ``workspaceMount`` -- ``_per_instance_config`` rewrites this to a
# unique, per-invocation name so each run gets its own fresh volume
# instead of sharing (and accumulating state in) one fixed one.
BASE_VOLUME_NAME = "copilot-extensions-test-isolation-ws"

# The workspace volume's size is bounded (a tmpfs-backed Docker volume, not
# the default unbounded local-disk volume) so a buggy or adversarial test
# cannot fill the host's Docker storage before teardown runs -- matches the
# bounded-writable-surface model `agent-containers`' own restricted fleet
# uses (`plugins/agent-containers/src/agent_containers/fleet.py`'s tmpfs
# surfaces). The checkout snapshot plus a fresh venv comfortably fits.
WORKSPACE_VOLUME_SIZE = "4g"

# Hard ceilings this wrapper's OWN container enforces (kept in sync with
# devcontainer.json's `runArgs`/tmpfs `size=`), plus reserved overhead
# and `run-plugin-tests.py`'s own argparse defaults -- all consumed by
# `_reject_resource_overrides_exceeding_container_ceilings`.
_CONTAINER_MEMORY_MB_CEILING = 14 * 1024
_CONTAINER_PIDS_CEILING = 512
_CONTAINER_TMP_MB_CEILING = 6144
_CONTAINER_OVERHEAD_MB = 512
_CONTAINER_PIDS_RESERVED = 32
_RUNNER_DEFAULT_MEMORY_MB = 4096
_RUNNER_DEFAULT_TEMP_MB = 2048

# Excluded from the point-in-time copy made into the container even if
# `git ls-files` would otherwise include them -- belt-and-suspenders only
# (`_tracked_paths` already excludes anything gitignored, including
# `.test-venvs`). `.devcontainer` is deliberately NOT excluded: excluding
# it made every in-container checkout appear dirty (rebuilt index from
# the full `HEAD` tree still lists it) -- the per-run config is already a
# separate temp copy (`_per_instance_config`) regardless.
EXCLUDED_TOP_LEVEL = {
    ".test-venvs",
    "node_modules",
    "__pycache__",
}


def _minimal_repo_selection_env() -> dict[str, str]:
    """A blanket ``GIT_*`` strip used only by
    `_discover_configured_clean_filters`, before `_scrubbed_git_env`'s
    overrides exist. `check-attr` never invokes a clean filter, but
    still needs `core.fsmonitor=false` (confirmed `GIT_OPTIONAL_LOCKS=0`
    can make read-only probes consult a hook) and `GIT_NO_LAZY_FETCH=1`/
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
    `--cached`, confirmed to silently miss an uncommitted edit). A
    read-only probe invokes a configured `filter.<name>.clean` for any
    path needing re-hashing, OR `.process` (higher-precedence) if ALSO
    configured -- both confirmed to execute host code during `git
    status`, so `_scrubbed_git_env` neutralizes both. FAILS CLOSED on
    any subprocess failure."""
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
    """Ambient environment with EVERY inherited ``GIT_*`` variable removed (matching
    `agent_bridge_contract_git.py`'s hardened env). Forces `GIT_OPTIONAL_LOCKS=0`,
    `GIT_NO_LAZY_FETCH=1`/`GIT_NO_REPLACE_OBJECTS=1`, disabled global/system config,
    `core.fsmonitor=false`, and for every `_discover_configured_clean_filters` name, both
    `.clean` forced to `cat` and `.process` forced empty (`process` runs host code even
    with `clean` alone neutralized)."""
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
        raise SystemExit("devcontainer CLI not found. Install with `npm i -g @devcontainers/cli`.")
    return exe


def _per_instance_config(instance_label: str) -> tuple[Path, str]:
    """Write a copy of ``DEVCONTAINER_CONFIG`` with its workspace volume name made unique
    to this invocation, so each run gets its own fresh volume instead of reusing one fixed,
    shared one. Returns the temp config path and volume name, so the caller can remove that
    volume at teardown. Written as literally ``devcontainer.json`` -- the devcontainer CLI
    rejects any other ``--config`` basename."""
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
    """Create the per-invocation workspace volume up front, size-bounded
    and tmpfs-backed -- see ``WORKSPACE_VOLUME_SIZE``. ``devcontainer
    up`` then just reuses this one instead of implicitly creating an
    unbounded default."""
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
    """Repo-relative paths of files the snapshot should contain -- deliberately NOT every file
    physically present under ``REPO``. Default (``include_untracked=False``) is git-TRACKED
    files only: there's no blanket `.gitignore` rule for `.env`-style config, so an untracked-
    but-not-ignored secret file would otherwise be copied into a container with outbound network
    access. ``include_untracked=True`` (the wrapper's ``--include-untracked`` flag) additionally
    includes untracked-but-not-gitignored files. Known residual exposure: a tracked path's
    CURRENT on-disk content is copied, not the last-committed blob, so an uncommitted secret in
    an otherwise-tracked file is still copied in."""
    args = ["git", "-C", str(REPO), "ls-files", "-z", "--cached"]
    if include_untracked:
        args += ["--others", "--exclude-standard"]
    res = subprocess.run(args, capture_output=True, timeout=60, env=_scrubbed_git_env())
    if res.returncode != 0:
        raise SystemExit(
            f"git ls-files failed: {res.stderr.decode(errors='replace').strip()}"
        )
    # `os.fsdecode` (surrogate-escape): a tracked path is arbitrary bytes
    # on Linux, and a plain `.decode()` would raise outright for a valid
    # filename that isn't valid UTF-8.
    paths = [p for p in os.fsdecode(res.stdout).split("\0") if p]
    excluded_prefixes = tuple(f"{name}/" for name in EXCLUDED_TOP_LEVEL)
    return [
        p for p in paths
        if p not in EXCLUDED_TOP_LEVEL and not p.startswith(excluded_prefixes)
    ]


def _warn_about_dirty_tracked_files() -> None:
    """Print a clear, explicit stderr warning naming every tracked file with an uncommitted
    modification -- the tracked-files-only boundary is about which PATHS are copied, not
    BYTES; an uncommitted secret in an otherwise-tracked file is still copied in. Fails
    CLOSED if ``git status`` can't run. ``--ignore-submodules=all`` is required: ``git
    status`` otherwise recursively inspects any initialized submodule, consulting a
    SUBMODULE-specific filter assignment `_discover_configured_clean_filters` never covers
    -- the snapshot never copies submodule contents anyway."""
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
        "warning: the following tracked file(s) have uncommitted changes and their CURRENT "
        "on-disk content (not the last-committed version) will be copied into the "
        "test-isolation container, which has outbound network access -- do not run this "
        "against a checkout with an uncommitted secret pasted into an otherwise-tracked file:",
        file=sys.stderr,
    )
    for path in dirty:
        print(f"  {path}", file=sys.stderr)


def _warn_about_hidden_tracked_file_flags() -> None:
    """Warns on every tracked file whose index entry carries
    ``assume-unchanged``/``skip-worktree`` (suppresses ``git status``
    reporting an on-disk diff while the snapshot still archives current
    bytes). Fails CLOSED on a failed ``ls-files``."""
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
        "test-isolation container, which has outbound network access during dependency preparation:",
        file=sys.stderr,
    )
    for path in flagged:
        print(f"  {path}", file=sys.stderr)


# Every `run-plugin-tests.py` flag that consumes a SEPARATE following
# token as its value -- kept in sync by hand with that script's own
# argparse, only to tell a flag's value apart from a positional plugin
# name, never to fully re-parse its CLI.
_VALUE_CONSUMING_FLAGS = frozenset({
    "--base", "-k", "--admission-wait", "--timeout", "--subsuite-timeout",
    "--plugin-timeout", "--test-timeout", "--max-files-per-sub-suite",
    "--max-processes", "--max-memory-mb", "--max-temp-mb", "--exclude",
})

# Every bare (`store_true`) flag -- kept in sync alongside
# `_VALUE_CONSUMING_FLAGS` above, same reason.
_BARE_FLAGS = frozenset({
    "--all", "--changed", "--reinstall", "--guards", "--collect-only",
    "--prepare-only", "--list", "--pre-push", "--allow-explicit-tiers",
    "--allow-host-state",
})

_ALL_LONG_FLAGS = _VALUE_CONSUMING_FLAGS | _BARE_FLAGS


def _canonicalize_flag(name: str) -> str:
    """Resolve an abbreviated long-flag token to its canonical form via
    `_ALL_LONG_FLAGS`."""
    if name in _ALL_LONG_FLAGS or not name.startswith("--") or len(name) <= 2:
        return name
    matches = [flag for flag in _ALL_LONG_FLAGS if flag.startswith(name)]
    return matches[0] if len(matches) == 1 else name


def _reject_resource_overrides_exceeding_container_ceilings(passthrough: list[str]) -> None:
    """Reject a `--max-memory-mb`/`--max-processes`/`--max-temp-mb`
    combination this wrapper's container can't honor -- a second
    exception alongside `--allow-host-state`. Resolves each flag's LAST
    occurrence (matching argparse semantics) and, for any not given, the
    inner runner's own default. Checks `--max-processes` against a
    REDUCED ceiling, and memory+temp COMBINED against a reduced memory
    ceiling: `/tmp` is memory-backed tmpfs sharing the SAME cgroup, so
    two individually-safe values can together still exceed it."""
    requested = {"--max-memory-mb": None, "--max-processes": None, "--max-temp-mb": None}
    for i, arg in enumerate(passthrough):
        name, eq, value_str = arg.partition("=")
        canonical = _canonicalize_flag(name)
        if canonical not in requested:
            continue
        if not eq:
            value_str = passthrough[i + 1] if i + 1 < len(passthrough) else ""
        try:
            requested[canonical] = int(value_str)
        except ValueError:
            continue
    memory_mb = requested["--max-memory-mb"]
    if memory_mb is None:
        memory_mb = _RUNNER_DEFAULT_MEMORY_MB
    temp_mb = requested["--max-temp-mb"]
    if temp_mb is None:
        temp_mb = _RUNNER_DEFAULT_TEMP_MB
    processes = requested["--max-processes"]

    if temp_mb > _CONTAINER_TMP_MB_CEILING:
        raise SystemExit(
            f"--max-temp-mb {temp_mb} exceeds /tmp's physical tmpfs ceiling "
            f"({_CONTAINER_TMP_MB_CEILING} MiB) -- ENOSPC regardless of memory "
            "budget. Lower it, or run run-plugin-tests.py directly."
        )
    effective_memory_ceiling = _CONTAINER_MEMORY_MB_CEILING - _CONTAINER_OVERHEAD_MB
    if memory_mb + temp_mb > effective_memory_ceiling:
        raise SystemExit(
            f"--max-memory-mb {memory_mb} plus --max-temp-mb {temp_mb} "
            f"({memory_mb + temp_mb} MiB combined) exceeds the effective "
            f"container memory budget ({effective_memory_ceiling} MiB) -- /tmp is "
            "memory-backed tmpfs, sharing the SAME --memory cgroup. Lower one or "
            "both, or run run-plugin-tests.py directly."
        )
    effective_pids_ceiling = _CONTAINER_PIDS_CEILING - _CONTAINER_PIDS_RESERVED
    if processes is not None and processes > effective_pids_ceiling:
        raise SystemExit(
            f"--max-processes {processes} exceeds the effective container PID "
            f"budget ({effective_pids_ceiling}) -- would be silently preempted. "
            "Lower it, or run run-plugin-tests.py directly."
        )


def _resolve_base_ref(passthrough: list[str]) -> str:
    """Best-effort extraction of ``--base`` for
    ``_materialized_git_dir``'s closure; falls back to the default."""
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
    """Whether targets resolve via ``changed_plugins()`` -- true for
    ``--changed``, AND the runner's default (no ``--all``, no plugin
    names)."""
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
    ``ref^{commit}``: plain ``rev-parse --verify`` accepts any object
    type, but the downstream diff needs a commit-ish; ``--end-of-options``
    keeps a ``-``-prefixed ref from misreading as a flag."""
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
# copy -- deliberately NOT a copy of the host's own config, which may
# embed an authenticated remote URL or `credential.helper` settings.
# None of that is needed for local-ref `diff`/`status`/`rev-parse`.
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
    ``--changed`` diff base -- never the full repository history (every
    branch/stash/reflog/unreachable object), which a container with
    outbound networking could otherwise exfiltrate. ``git bundle
    create`` with only ``HEAD`` and (when resolvable) the ``--base`` ref,
    then ``git clone --bare`` into a fresh directory; ``main`` rewrites
    ``--base`` to the same resolved SHA first (see
    `_rewrite_base_to_resolved_sha`) so the clone needs no named ref. The
    index is rebuilt from ``HEAD`` rather than copied: the host's real
    index can reference a staged blob unreachable from both tips, which
    the bundle would then be missing -- a copied index pointing at a
    missing object breaks `git diff`/`status` outright. Staging isn't
    preserved, but every modification is still visible as an ordinary
    working-tree difference (`_tracked_paths` copies CURRENT content
    regardless). ``config`` is fresh/credential-free; ``hooks`` dropped."""
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
    # `changed_plugins()` ignores a nonzero `git diff`, reporting an
    # EMPTY target set rather than erroring -- EITHER an unresolvable
    # base OR an orphan/unrelated-history one degrades silently to "No
    # plugin suites to run." instead of the real problem. Fail loudly
    # before any snapshot work; `--all`/an explicit plugin never
    # consults `base_ref`.
    if _changed_mode_active(passthrough):
        if not base_resolves:
            raise SystemExit(
                f"changed-selection mode is active but its diff base ({base_ref!r}) does not "
                "resolve on the host -- refusing to silently build a snapshot that would make "
                "the in-container run report \"no plugin suites to run\" instead of the real "
                "problem. Fetch or correct --base."
            )
        merge_base_res = subprocess.run(
            ["git", "-C", str(REPO), "merge-base", base_ref, "HEAD"],
            capture_output=True, text=True, timeout=30, env=_scrubbed_git_env(),
        )
        if merge_base_res.returncode != 0:
            raise SystemExit(
                f"changed-selection mode is active but {base_ref!r} and HEAD share no merge "
                "base (orphan/unrelated history) -- refusing to silently build a snapshot "
                "that would make the in-container three-dot diff fail. Correct --base."
            )
    # Only include the base ref's closure when changed-selection actually
    # consults it -- an `--all`/explicit-plugin run never uses `base_ref`,
    # so bundling it would needlessly widen the minimal-history boundary.
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
    An absent path (e.g. an unstaged deletion) is checked via
    ``os.path.lexists`` and silently skipped. An initialized submodule
    (a ``160000``-mode path that's a real directory on disk) is added
    as an empty directory entry only (``recursive=False``), never its
    contents. A tracked path's own ANCESTOR directory can be replaced
    with a symlink to outside ``REPO`` -- confirmed live that
    ``lexists`` alone misses this; each path's PARENT directory (not the
    leaf, which may legitimately be a tracked symlink) has its real path
    checked against ``REPO``'s, failing closed on an escape. Also warns
    (``_warn_about_dirty_tracked_files``,
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
    """Copy a point-in-time snapshot of the host checkout into the container's workspace
    VOLUME (never a host bind). A freshly created Docker volume is root-owned, so a one-off
    root ``chmod`` opens its empty PERMISSION bits first (root remains OWNER; `--cap-drop=ALL`
    blocks `chown`). Extraction runs AS ``vscode``, so Git's "dubious ownership" check (which
    inspects the working-tree ROOT's owner) passes -- the mountpoint stays root-owned for the
    container's lifetime; the devcontainer spec's `safe.directory` exemption covers that gap.
    The permission pass skips symlinks (`chmod` dereferences)."""
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
    container running or an orphaned volume. Each removal is guarded
    against ``subprocess.SubprocessError``/``OSError`` so one exception
    never skips the other. Every ``docker`` call runs with
    ``start_new_session=True``: without it, a terminal Ctrl-C's
    ``SIGINT`` reaches these children too (same process group), which
    retain the default handler -- `_cleanup_signals_deferred` only
    protects the Python PARENT."""
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
    """Best-effort cleanup when ``devcontainer up`` itself fails: a
    container may have been created under this instance's id-label even
    though ``_bring_up`` never returned an id. Finds and removes it by
    label, then removes the volume. Every call is individually guarded
    so one failing step never skips the rest, never raises itself, and
    runs with ``start_new_session=True`` (see `_tear_down`)."""
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


# `SIGINT` already becomes `KeyboardInterrupt` via Python's own default
# handling -- `SIGTERM` and `SIGHUP` (e.g. a closed SSH/terminal session,
# also terminating by default on Linux) both need `_raise_on_sigterm`
# instead. All three still need deferring during cleanup itself, so a
# REPEAT signal mid-cleanup can't interrupt it partway.
_CLEANUP_DEFERRED_SIGNALS = (signal.SIGINT, signal.SIGTERM, signal.SIGHUP)


@contextlib.contextmanager
def _cleanup_signals_deferred():
    """Defer every `_CLEANUP_DEFERRED_SIGNALS` signal for a cleanup step
    (``_tear_down``/``_cleanup_orphan``): RECORD receipt instead of acting
    immediately, restore the previous handlers once cleanup finishes, then
    raise `_TerminationRequested` if one was recorded AND no exception is
    propagating -- plain ``SIG_IGN`` would DISCARD a signal, letting
    `main` return 0 for a cancelled run; replaying unconditionally could
    REPLACE a genuine failure already propagating.

    Installing/restoring multiple handlers isn't atomic -- a signal
    mid-swap can hit whichever OLD handler is active for a not-yet-
    swapped one (confirmed live). ``pthread_sigmask`` blocks all of them
    for each swap, restoring the EXACT prior mask via ``SIG_SETMASK``
    (never ``SIG_UNBLOCK``, which would unblock a caller-pre-blocked
    signal, confirmed live). Such a pending signal would otherwise be
    delivered to the already-restored OLD handler at the final unmask,
    bypassing the replay decision
    (confirmed live); exit flushes it to `_record` first, then restores
    handlers in their own separately-masked swap."""
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
        # Flush any pending signal to `_record` (still active) first.
        caller_mask = signal.pthread_sigmask(signal.SIG_UNBLOCK, _CLEANUP_DEFERRED_SIGNALS)
        signal.pthread_sigmask(signal.SIG_SETMASK, caller_mask)
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
    # Converts SIGTERM/SIGHUP into a raised exception so this function's
    # own try/finally cleanup runs -- see `_TerminationRequested`. SIGINT
    # needs no handler (Python raises `KeyboardInterrupt`); previous
    # handlers are restored below, since `main` is invoked in-process by
    # this module's tests.
    previous_sigterm_handler = signal.signal(signal.SIGTERM, _raise_on_sigterm)
    previous_sighup_handler = signal.signal(signal.SIGHUP, _raise_on_sigterm)
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
        # "--" is argparse's own flags/positionals separator -- strip
        # every occurrence (not just a leading one), since it can appear
        # anywhere (e.g. ``--all -- -k some_filter``).
        passthrough = [arg for arg in passthrough if arg != "--"]
        # `--allow-host-state`'s contract (preserve the caller's real
        # HOME/config/credentials) can't be honored -- the container
        # always gets a fresh, credential-free tmpfs $HOME by design.
        if any(
            _canonicalize_flag(arg.partition("=")[0]) == "--allow-host-state"
            for arg in passthrough
        ):
            raise SystemExit(
                "--allow-host-state is not supported through tools/run_tests_in_devcontainer.py: its "
                "documented contract (preserve the caller's real HOME/config/credentials) cannot be "
                "honored here -- the container always gets a fresh, credential-free tmpfs $HOME by "
                "design. Run tools/run-plugin-tests.py directly (outside the devcontainer) for an "
                "--allow-host-state check instead."
            )
        # The second documented exception -- see the function's own
        # docstring.
        _reject_resource_overrides_exceeding_container_ceilings(passthrough)
        # Rewriting `--base` to its resolved SHA here means both the
        # snapshot and the in-container command see the SAME resolved
        # commit -- see `_rewrite_base_to_resolved_sha`.
        passthrough = _rewrite_base_to_resolved_sha(passthrough)

        # Acquire the host-wide lease `run-plugin-tests.py` itself uses, on the HOST, first -- its
        # in-container acquisition is uncontested. Own try/finally from the moment of acquisition: a
        # later failure (even as early as `_per_instance_config`) can never leak it. Resolve (validate)
        # `--admission-wait` UNCONDITIONALLY, even for `--list` (which skips acquisition): a malformed
        # or negative value must fail before any container is brought up, not only once `--list` does.
        admission_wait = _admission.resolve_admission_wait(passthrough, _canonicalize_flag)
        admission_lease = None
        try:
            if _admission.needs_admission(passthrough, _canonicalize_flag):
                admission_lease = _admission.acquire(admission_wait)

            instance_label = uuid.uuid4().hex[:12]
            config_path, volume_name = _per_instance_config(instance_label)
            # `container_id` doubles as the lifecycle marker the `finally`
            # below uses to pick cleanup: still `None` means `_bring_up`
            # never returned one, a real id means normal teardown. No
            # window where a signal could raise before cleanup is active.
            container_id: str | None = None
            result: int | None = None
            primary_failed = False
            try:
                try:
                    _create_bounded_volume(volume_name)
                    container_id = _bring_up(instance_label, config_path)
                    _populate_workspace(container_id, passthrough, include_untracked=ns.include_untracked)
                    # Phase 2 networking split -- skipped for `--list`.
                    if not _net_scope.is_list_only(passthrough, _canonicalize_flag):
                        _net_scope.prepare_dependencies(
                            _devcontainer_exe(), REPO, container_id, config_path, passthrough, _canonicalize_flag,
                        )
                        _net_scope.disconnect_container_networks(container_id)
                        # The prep pass rebuilt the venv(s); --reinstall here would rebuild
                        # again with no network left.
                        passthrough = _net_scope.strip_reinstall(passthrough, _canonicalize_flag)
                    result = _run_tests(container_id, config_path, passthrough)
                    primary_failed = result != 0
                except BaseException:
                    primary_failed = True
                    raise
                finally:
                    # The primary result/exception must win over a secondary cleanup failure -- a bare `finally` raising
                    # would otherwise silently discard it. `primary_failed` says which case this is: report (don't
                    # re-raise) a cleanup failure once the primary already failed; raise it directly only when it succeeded.
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
            if admission_lease is not None:
                admission_lease.release()
    finally:
        signal.signal(signal.SIGTERM, previous_sigterm_handler)
        signal.signal(signal.SIGHUP, previous_sighup_handler)



if __name__ == "__main__":
    raise SystemExit(main())
