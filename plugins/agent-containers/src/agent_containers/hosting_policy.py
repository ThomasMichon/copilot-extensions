"""Guest-side repository admission, independent of host discovery caches."""

from __future__ import annotations

import json
import shlex
import subprocess
from types import SimpleNamespace
from typing import TYPE_CHECKING, Protocol

from agent_procutil import no_window_flags
from repository_policy import AgentHostingError, validate_hosting_result

if TYPE_CHECKING:
    from .session_host_context import TrustedContext

_OWNER_POLICY_JSON = (
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
    "print(json.dumps(p))\n"
)

class ExecutionCheckout(Protocol):
    container_id: str
    user: str
    workspace_folder: str


def guest_check_command(workspace: str, *, operation: str = "launch") -> str:
    """Use the venue's own attributed worktrees command and actual checkout."""
    if not isinstance(workspace, str) or not workspace.startswith("/") or "\0" in workspace:
        raise RuntimeError("Repository hosting admission requires an absolute guest checkout")
    return (
        f"cd -- {shlex.quote(workspace)} && "
        "WORKTREES=$(command -v agent-worktrees) && test -f \"$WORKTREES\" && "
        '(POLICY_RESULT=$("$WORKTREES" agent-policy check --path "$PWD" '
        f"--operation {shlex.quote(operation)} --json) || "
        "{ POLICY_STATUS=$?; printf '%s\\n' \"$POLICY_RESULT\" >&2; exit \"$POLICY_STATUS\"; }; "
        f"printf '%s\\n' \"$POLICY_RESULT\" | python3 -c {shlex.quote(_OWNER_POLICY_JSON)})"
    )


def guarded_launch(command: str, workspace: str) -> str:
    return (
        "repository_policy_launch() {\n"
        f"  {guest_check_command(workspace)} >&2 || return $?\n"
        f'  {command} "$@"\n'
        "}\nrepository_policy_launch"
    )


def require_container_hosting(
    target: ExecutionCheckout, *, worktree_id: str | None = None,
    live_attachment: bool = False,
) -> None:
    """Refuse before credentials, native launch or first-seed effects."""
    from .config import _installation_owner

    _installation_owner()
    if live_attachment:
        _run(target, (
            "WORKTREES=$(command -v agent-worktrees) && test -f \"$WORKTREES\" && "
            '"$WORKTREES" agent-policy check --help >/dev/null'
        ))
        return
    workspace = target.workspace_folder
    command = guest_check_command(workspace)
    if worktree_id:
        from plugin_resolve.reflection import parse_execution_checkout

        resolved = _run(target, (
            f"cd -- {shlex.quote(workspace)} && "
            "WORKTREES=$(command -v agent-worktrees) && test -f \"$WORKTREES\" && "
            '"$WORKTREES" resolve --json --dry-run --no-fast-forward --defer-projections '
            f"--worktree-id {shlex.quote(worktree_id)}"
        ))
        command = guest_check_command(parse_execution_checkout(resolved.stdout))
    result = _run(target, command)
    try:
        policy = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Guest repository hosting receiver returned invalid policy JSON") from exc
    try:
        validate_hosting_result(policy)
    except AgentHostingError as exc:
        raise RuntimeError(f"Guest repository hosting receiver returned invalid policy: {exc}") from exc


def require_container_checkout(container_id: str, user: str, workspace: str) -> None:
    require_container_hosting(SimpleNamespace(
        container_id=container_id, user=user, workspace_folder=workspace,
    ))


def session_host_launch_inputs(
    context: TrustedContext, expected_instance: str | None,
) -> tuple[str, str, int, str]:
    """Admit the selected checkout and preference launcher before projection."""
    require_container_checkout(context.instance_id, context.user, context.workspace)
    command = context.config.acp_command_for(context.fleet)
    launcher = "{{target_preference_launcher}}"
    count = command.count(launcher)
    if count > 1:
        raise ValueError("target preference template requires exactly one launcher token")
    if count and (not expected_instance or context.instance_id != expected_instance):
        raise ValueError("target preference launcher requires its selected execution instance")
    return command, launcher, count, context.instance_id


def _run(target: ExecutionCheckout, command: str) -> subprocess.CompletedProcess[str]:
    try:
        result = subprocess.run(
            ["docker", "exec", "-u", target.user, target.container_id,
             "bash", "-lc", command],
            capture_output=True, text=True, timeout=90, check=False,
            creationflags=no_window_flags(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise RuntimeError(f"Required guest repository hosting check is unavailable: {exc}") from exc
    if result.returncode:
        raise RuntimeError(
            "Guest repository hosting refused or receiver unsupported: "
            + (result.stderr.strip() or result.stdout.strip() or f"exit {result.returncode}")
        )
    return result
