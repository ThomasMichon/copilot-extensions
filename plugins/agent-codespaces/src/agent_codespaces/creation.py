"""Headless Codespace creation fallback without credential rediscovery."""
from __future__ import annotations

import logging
import subprocess

from agent_procutil import no_window_flags

log = logging.getLogger("agent-codespaces")
_INTERACTIVE_PROMPT_MARKERS = ("failed to prompt", "no terminal")


def _is_interactive_prompt_failure(stderr: str) -> bool:
    """Whether ``gh codespace create`` stderr indicates a headless prompt abort."""
    low = (stderr or "").lower()
    return any(marker in low for marker in _INTERACTIVE_PROMPT_MARKERS)


def _create_codespace_via_rest(
    repo: str,
    machine: str,
    location: str,
    account: str | None,
    *,
    branch: str | None = None,
    display_name: str | None = None,
    devcontainer_path: str | None = None,
    environment: dict | None = None,
) -> str:
    """Create through the noninteractive REST endpoint, retaining admission credentials."""
    from .allocation import creation_environment

    args = [
        "gh", "api", "--method", "POST",
        f"repos/{repo}/codespaces",
        "-f", f"machine={machine}",
        "-f", f"location={location}",
        "-F", "multi_repo_permissions_opt_out=true",
        "--jq", ".name",
    ]
    if branch:
        args.extend(["-f", f"ref={branch}"])
    if display_name:
        args.extend(["-f", f"display_name={display_name}"])
    if devcontainer_path:
        args.extend(["-f", f"devcontainer_path={devcontainer_path}"])
    log.info("Creating codespace via REST API (#151 fallback): %s", " ".join(args))
    result = subprocess.run(
        args, capture_output=True, text=True, timeout=300,
        creationflags=no_window_flags(),
        env=environment if environment is not None else creation_environment(repo, account)[1],
    )
    if result.returncode != 0:
        raise RuntimeError(
            "gh codespace create hit an interactive prompt headlessly and the "
            f"REST API fallback also failed: {result.stderr.strip()}"
        )
    name = result.stdout.strip()
    if not name:
        raise RuntimeError(
            f"REST API codespace create returned no name (stdout={result.stdout!r})"
        )
    return name
