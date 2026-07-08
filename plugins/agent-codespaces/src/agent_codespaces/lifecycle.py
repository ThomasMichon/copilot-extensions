"""CodeSpace lifecycle management -- create, delete, list, status.

Wraps ``gh codespace`` commands with configuration from codespaces.yaml.
"""

from __future__ import annotations

import json
import logging
import subprocess
import sys
from dataclasses import dataclass
from enum import Enum

from .config import RUNTIME_DIR, CodespacesConfig, RepoConfig

log = logging.getLogger("agent-codespaces")


class WaitOutcome(str, Enum):
    """Result of waiting for a CodeSpace to become usable."""

    AVAILABLE = "available"
    FAILED = "failed"
    TIMEOUT = "timeout"


# States (from ``gh codespace list``) that mean the CodeSpace will NOT reach
# ``Available`` on its own -- waiting longer is pointless, so a waiter returns
# FAILED immediately instead of burning its whole budget. Everything else
# (Provisioning/Queued/Starting/Awaiting/Rebuilding/Updating/Shutdown/Unknown/...)
# is treated as *pending*: still in progress, keep waiting patiently. This is
# what lets a slow boot never be mistaken for a dead CodeSpace.
_TERMINAL_FAILED_STATES = frozenset({
    "Failed", "Unavailable", "Deleted", "Moved", "Archived",
})
_AVAILABLE_STATE = "Available"
_SHUTDOWN_STATE = "Shutdown"


def classify_state(state: str) -> str:
    """Bucket a raw ``gh`` state into ``available`` | ``failed`` | ``pending``."""
    if state == _AVAILABLE_STATE:
        return "available"
    if state in _TERMINAL_FAILED_STATES:
        return "failed"
    return "pending"


@dataclass
class CodespaceInfo:
    """Summary of a CodeSpace from ``gh codespace list``."""

    name: str
    display_name: str
    repository: str
    branch: str
    state: str
    machine: str


def _creation_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW
    return 0


def list_codespaces() -> list[CodespaceInfo]:
    """List active CodeSpaces via ``gh codespace list``."""
    args = [
        "gh", "codespace", "list",
        "--json", "name,displayName,repository,gitStatus,state,machineName",
        "--limit", "50",
    ]

    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=30,
            creationflags=_creation_flags(),
        )
    except FileNotFoundError:
        raise RuntimeError("gh CLI not found") from None

    if result.returncode != 0:
        raise RuntimeError(f"gh codespace list failed: {result.stderr.strip()}")

    entries = json.loads(result.stdout) if result.stdout.strip() else []
    codespaces = []
    for e in entries:
        git_status = e.get("gitStatus", {})
        codespaces.append(CodespaceInfo(
            name=e.get("name", ""),
            display_name=e.get("displayName", ""),
            repository=e.get("repository", ""),
            branch=git_status.get("ref", "") if isinstance(git_status, dict) else "",
            state=e.get("state", ""),
            machine=e.get("machineName", ""),
        ))
    return codespaces


def list_devcontainers(repo: str) -> list[str]:
    """Return the discoverable devcontainer config paths for a repo.

    Queries ``gh api repos/{repo}/codespaces/devcontainers`` -- the same set
    ``gh codespace create`` would otherwise prompt over. Returns an empty list
    on any failure (missing API, auth, network) so callers degrade gracefully
    to "don't pass ``--devcontainer-path``" (today's behavior).
    """
    args = [
        "gh", "api", f"repos/{repo}/codespaces/devcontainers",
        "--jq", ".devcontainers[].path",
    ]
    try:
        result = subprocess.run(
            args, capture_output=True, text=True, timeout=30,
            creationflags=_creation_flags(),
        )
    except (FileNotFoundError, subprocess.SubprocessError):
        return []
    if result.returncode != 0:
        log.debug(
            "Could not enumerate devcontainers for %s: %s",
            repo, result.stderr.strip(),
        )
        return []
    return [line.strip() for line in result.stdout.splitlines() if line.strip()]


def resolve_devcontainer_path(
    repo: str,
    config: CodespacesConfig,
    override: str | None = None,
) -> str | None:
    """Pick the ``--devcontainer-path`` to pass ``gh codespace create``, or None.

    ``gh codespace create`` prompts (and hard-fails headless with ``failed to
    prompt: no terminal``) when a repo exposes MORE THAN ONE discoverable
    ``devcontainer.json``. This resolves which config to build from so creation
    stays non-interactive, and is self-healing: a repo (or a newly-added
    devcontainer) never re-breaks headless create without any config change.

    Returns ``None`` -- meaning *don't pass the flag* -- when the repo has 0 or
    1 devcontainer (the common case; no prompt, no risk for other repos) or when
    enumeration is unavailable. When there are multiple, the config to use is
    chosen by precedence (most specific first):

    1. ``override`` -- an explicit ``--devcontainer-path`` from the caller/agent
       (the "do the right thing" escape hatch, e.g. to pick an alternate config).
    2. ``repos.<repo>.devcontainer_path`` -- the operator's per-repo choice.
    3. ``defaults.devcontainer_path`` -- the global fallback, if it is actually
       one of the repo's configs.
    4. The canonical ``.devcontainer/devcontainer.json`` if present.
    5. The first config reported (deterministic last resort).

    The chosen path and how to override it are logged so a headless caller can
    see (and change) what was picked.
    """
    if override:
        log.info("Using devcontainer path (explicit override): %s", override)
        return override

    paths = list_devcontainers(repo)
    if len(paths) <= 1:
        # 0 or 1 config -> gh won't prompt; passing the flag would risk naming a
        # path that doesn't exist for repos whose sole config lives elsewhere.
        return None

    repo_cfg = config.repos.get(repo, RepoConfig())
    candidates = [
        repo_cfg.devcontainer_path,
        config.default_devcontainer_path
        if config.default_devcontainer_path in paths
        else None,
        ".devcontainer/devcontainer.json"
        if ".devcontainer/devcontainer.json" in paths
        else None,
        paths[0],
    ]
    chosen = next(c for c in candidates if c)
    log.info(
        "Repo %s has %d devcontainer configs %s; building from %r "
        "(override with --devcontainer-path or repos.%s.devcontainer_path)",
        repo, len(paths), paths, chosen, repo,
    )
    return chosen


def create_codespace(
    repo: str,
    config: CodespacesConfig,
    branch: str | None = None,
    display_name: str | None = None,
    devcontainer_path: str | None = None,
) -> CodespaceInfo:
    """Create a CodeSpace for the given repo using config defaults.

    Dotfiles are applied automatically by GitHub from the account-level
    dotfiles setting -- there is no ``--dotfiles`` flag on ``gh codespace
    create``. ``--default-permissions`` avoids an interactive prompt.

    ``--devcontainer-path`` is passed when the repo has multiple devcontainer
    configs (see ``resolve_devcontainer_path``) so creation stays headless.
    """
    repo_config = config.repos.get(repo, RepoConfig())
    machine_type = repo_config.machine_type or config.default_machine_type
    location = repo_config.location or config.default_location
    resolved_devcontainer = resolve_devcontainer_path(
        repo, config, override=devcontainer_path,
    )

    args = [
        "gh", "codespace", "create",
        "--repo", repo,
        "--machine", machine_type,
        "--location", location,
        "--default-permissions",
    ]
    if resolved_devcontainer:
        args.extend(["--devcontainer-path", resolved_devcontainer])
    if branch:
        args.extend(["--branch", branch])
    if display_name:
        args.extend(["--display-name", display_name])

    log.info("Creating codespace: %s", " ".join(args))

    result = subprocess.run(
        args, capture_output=True, text=True, timeout=300,
        creationflags=_creation_flags(),
    )

    if result.returncode != 0:
        raise RuntimeError(f"gh codespace create failed: {result.stderr.strip()}")

    # gh codespace create prints the name on stdout
    name = result.stdout.strip()
    return CodespaceInfo(
        name=name,
        display_name=display_name or name,
        repository=repo,
        branch=branch or "",
        state="Available",
        machine=machine_type,
    )


def wait_for_codespace(
    name: str,
    timeout: float = 1200.0,
    interval: float = 10.0,
    on_progress=None,
) -> tuple[WaitOutcome, str]:
    """Patiently poll a CodeSpace until it is usable, fails, or times out.

    Returns ``(outcome, last_state)``. Unlike a naive fixed-timeout poll, a
    genuinely-failed state (Failed/Unavailable/Deleted/Moved/Archived) returns
    ``FAILED`` right away rather than waiting out the whole budget -- so callers
    can distinguish "still provisioning" from "genuinely dead" and never mistake
    a slow boot for a redundant-create trigger.

    The default ``timeout`` is generous (20 min) because CodeSpace
    create/provision is finicky; the caller supplies a finite ceiling and (for a
    background wait) an ``on_progress(last_state, remaining_s)`` callback. Transient
    ``gh codespace list`` errors are tolerated (logged, retried).
    """
    import time

    deadline = time.monotonic() + timeout
    last_state = ""
    while True:
        try:
            found = None
            for cs in list_codespaces():
                if cs.name == name:
                    found = cs
                    break
            if found is not None:
                last_state = found.state
                bucket = classify_state(found.state)
                if bucket == "available":
                    return WaitOutcome.AVAILABLE, found.state
                if bucket == "failed":
                    return WaitOutcome.FAILED, found.state
            else:
                last_state = "not-listed"
        except RuntimeError as exc:
            log.debug("list_codespaces during wait failed: %s", exc)
        if on_progress is not None:
            on_progress(last_state, max(0.0, deadline - time.monotonic()))
        if time.monotonic() + interval >= deadline:
            return WaitOutcome.TIMEOUT, last_state
        time.sleep(interval)


def wait_for_available(name: str, timeout: float = 300.0, interval: float = 10.0) -> bool:
    """Poll until a CodeSpace reaches the ``Available`` state.

    Returns True once Available, or False on timeout OR a terminal-failed state.
    Backward-compatible boolean shim over :func:`wait_for_codespace` (which now
    also fails fast on genuinely-dead states instead of waiting out the timeout).
    Used after ``create_codespace`` before provisioning over SSH.
    """
    outcome, _ = wait_for_codespace(name, timeout=timeout, interval=interval)
    return outcome == WaitOutcome.AVAILABLE


def delete_codespace(name: str, force: bool = False) -> None:
    """Delete a CodeSpace by name."""
    args = ["gh", "codespace", "delete", "-c", name]
    if force:
        args.append("--force")

    log.info("Deleting codespace: %s", name)

    result = subprocess.run(
        args, capture_output=True, text=True, timeout=60,
        creationflags=_creation_flags(),
    )

    if result.returncode != 0:
        raise RuntimeError(f"gh codespace delete failed: {result.stderr.strip()}")


def stop_codespace(name: str) -> bool:
    """Gracefully stop (shut down) a CodeSpace, PRESERVING it for later resume.

    The pause-and-keep counterpart to ``delete_codespace`` -- the compute is
    released but the CodeSpace (and its volume) is kept, so it boots again on
    the next connect. Returns ``True`` when a stop was issued, ``False`` when
    the CodeSpace was already ``Shutdown`` (idempotent no-op). Raises
    ``RuntimeError`` on an unexpected failure.
    """
    # Idempotency: skip the call if the CodeSpace is already shut down.
    try:
        for cs in list_codespaces():
            if cs.name == name and cs.state == _SHUTDOWN_STATE:
                log.info("CodeSpace %s already Shutdown; nothing to stop", name)
                return False
    except RuntimeError:
        # Can't list (auth/network) -- fall through and let `gh` decide.
        pass

    args = ["gh", "codespace", "stop", "-c", name]
    log.info("Stopping codespace: %s", name)

    result = subprocess.run(
        args, capture_output=True, text=True, timeout=120,
        creationflags=_creation_flags(),
    )

    if result.returncode != 0:
        stderr = result.stderr.strip()
        # Tolerate an "already stopped" race as a no-op rather than an error.
        if "not running" in stderr.lower() or "already" in stderr.lower():
            log.info("CodeSpace %s already stopped: %s", name, stderr)
            return False
        raise RuntimeError(f"gh codespace stop failed: {stderr}")
    return True


def cleanup_stale(
    *,
    dry_run: bool = False,
) -> dict[str, list[str]]:
    """Remove local state for codespaces that no longer exist.

    Prunes:
    - SSH config files in ``~/.agent-codespaces/ssh/`` for codespaces
      not in the current ``gh codespace list`` output
    - Socket files in ``~/.agent-codespaces/sockets/``

    Returns a dict of ``{"ssh_configs": [...], "sockets": [...]}``
    listing what was (or would be) removed.
    """
    ssh_dir = RUNTIME_DIR / "ssh"
    socket_dir = RUNTIME_DIR / "sockets"

    # Get live codespace names
    try:
        live = list_codespaces()
    except RuntimeError:
        log.warning("Cannot list codespaces; skipping cleanup")
        return {"ssh_configs": [], "sockets": []}

    live_names = {cs.name for cs in live}

    removed: dict[str, list[str]] = {"ssh_configs": [], "sockets": []}

    # Prune SSH config files
    if ssh_dir.exists():
        import re

        for config_file in ssh_dir.glob("*.config"):
            # Reverse the sanitization: underscores may have replaced
            # non-word chars, so we can't perfectly reverse. Instead,
            # check if any live codespace's sanitized name matches.
            stem = config_file.stem
            matched = any(
                re.sub(r"[^\w\-.]", "_", cs.name) == stem
                for cs in live
            )
            if not matched:
                log.info(
                    "%s stale SSH config: %s",
                    "Would remove" if dry_run else "Removing",
                    config_file.name,
                )
                removed["ssh_configs"].append(str(config_file))
                if not dry_run:
                    config_file.unlink(missing_ok=True)

    # Prune socket files
    if socket_dir.exists():
        for socket_file in socket_dir.iterdir():
            if socket_file.is_file() or socket_file.is_socket():
                # Socket names typically contain the codespace name
                stem = socket_file.stem
                matched = any(name in stem for name in live_names)
                if not matched:
                    log.info(
                        "%s stale socket: %s",
                        "Would remove" if dry_run else "Removing",
                        socket_file.name,
                    )
                    removed["sockets"].append(str(socket_file))
                    if not dry_run:
                        try:
                            socket_file.unlink(missing_ok=True)
                        except OSError:
                            log.warning(
                                "Could not remove socket: %s", socket_file,
                            )

    return removed
