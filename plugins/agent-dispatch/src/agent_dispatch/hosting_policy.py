"""Read-only admission of the selected execution harness, never the task lane."""

from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

from repository_policy import AgentHostingError, require_agent_hosting, validate_hosting_result

from .procutil import (
    agent_worktrees_environment, agent_worktrees_launch_prefix, no_window_kwargs,
)


def selected_marketplace_id() -> str:
    """Use the existing receipt validator; explicit invalid context is a veto."""
    from . import peer_launch
    from .bridge import BridgeUnavailable
    from .install_paths import install_dir

    raw = os.environ.get(peer_launch.CONTEXT_ENV, "")
    if not raw:
        root = install_dir()
        if root.parent.name == "plugins" and root.parent.parent.parent.name == "marketplaces":
            raise BridgeUnavailable("Repository hosting namespaced installation context is missing")
        return ""
    try:
        context = peer_launch.validate_owner("agent-dispatch", install_dir(), raw)
        marketplace = context["marketplaceId"]
        if not isinstance(marketplace, str) or not marketplace.strip():
            raise ValueError("Validated installation context omitted its marketplace identity")
    except (OSError, ValueError, TypeError, KeyError, ImportError) as exc:
        raise BridgeUnavailable(f"Repository hosting installation context refused: {exc}") from exc
    return marketplace


def require_execution_harness(
    *, path: str | None = None, project: str | None = None,
    worktree_id: str | None = None, timeout: float | None = 30,
) -> dict:
    """Resolve actual ground before any managed creation, resume or seed."""
    from .bridge import BridgeUnavailable
    from .embody import resolve_worktree

    marketplace = selected_marketplace_id()
    timeout = timeout if timeout is not None else 30

    def query(argv: list[str]) -> dict:
        prefix = agent_worktrees_launch_prefix()
        if not prefix:
            raise BridgeUnavailable("Execution metadata resolution requires agent-worktrees")
        try:
            result = subprocess.run(
                [*prefix, *argv], capture_output=True, text=True, check=False,
                timeout=timeout, env=agent_worktrees_environment(), **no_window_kwargs(),
            )
            if result.returncode:
                raise BridgeUnavailable(
                    result.stderr.strip() or result.stdout.strip()
                    or "Required repository hosting receiver refused or is unsupported",
                )
            data = json.loads(result.stdout)
            if not isinstance(data, dict):
                raise ValueError("expected a JSON object")
            return data
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            raise BridgeUnavailable(f"Repository hosting admission is unavailable: {exc}") from exc

    if worktree_id:
        path = resolve_worktree(worktree_id, project=project, timeout=timeout)["path"]
    elif path is None and project:
        path = query(["repos", "find", project, "--registered-only", "--json"]).get("path")
    if not isinstance(path, str) or not path:
        raise BridgeUnavailable("Selected execution harness has no authoritative checkout path")
    try:
        policy = {
            **require_agent_hosting(
                path, marketplace_id=marketplace, operation="dispatch",
            ).to_dict(),
            "allowed": True,
        }
        validate_hosting_result(policy)
    except AgentHostingError as exc:
        raise BridgeUnavailable(str(exc)) from exc
    return policy


def require_embodiment_harness(
    *, project: str | None, worktree_id: str | None, timeout: float | None,
) -> dict:
    """Select the local embodiment ground without substituting a task's repository."""
    return require_execution_harness(
        path=str(Path.cwd()) if not project and not worktree_id else None,
        project=project, worktree_id=worktree_id, timeout=timeout,
    )


def remote_harness_check(project: str | None) -> str:
    """Require the remote selected Worktrees receiver before an SSH launch."""
    if not project:
        from .bridge import BridgeUnavailable

        raise BridgeUnavailable("Remote hosting admission requires its selected harness project")
    extract = "import json,sys; value=json.load(sys.stdin); print(value['path'])"
    validate = (
        "import json,sys\n"
        "from pathlib import PurePosixPath,PureWindowsPath\n"
        "validpath=lambda v:isinstance(v,str) and bool(v.strip()) "
        "and not any(ord(c)<32 or ord(c)==127 for c in v) "
        "and (PurePosixPath(v).is_absolute() or PureWindowsPath(v).is_absolute())\n"
        "p=json.load(sys.stdin)\n"
        "if not (isinstance(p,dict) and type(p.get('version')) is int and p['version']==1 "
        "and p.get('allowed') is True and validpath(p.get('repository')) "
        "and 'expose_agent' in p and (p['expose_agent'] is None or p['expose_agent'] is True) "
        "and isinstance(p.get('sources'),list) and all(validpath(v) for v in p['sources']) "
        "and p.get('denied_by')==[] and 'error' not in p and 'code' not in p):\n"
        " raise SystemExit('Required repository hosting receiver returned invalid policy')\n"
    )
    return (
        "WORKTREES=$(command -v agent-worktrees) && test -f \"$WORKTREES\" && "
        'POLICY_WORKSPACE=$("$WORKTREES" repos find '
        f"{shlex.quote(project)} --registered-only --json | python3 -c {shlex.quote(extract)}) && "
        'test -n "$POLICY_WORKSPACE" && (POLICY_RESULT=$("$WORKTREES" agent-policy check '
        '--path "$POLICY_WORKSPACE" --operation dispatch --json) || '
        "{ POLICY_STATUS=$?; printf '%s\\n' \"$POLICY_RESULT\" >&2; exit \"$POLICY_STATUS\"; }; "
        f"printf '%s\\n' \"$POLICY_RESULT\" | python3 -c {shlex.quote(validate)})"
    )
