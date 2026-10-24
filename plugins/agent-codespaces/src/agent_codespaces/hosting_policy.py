"""Read-only repository admission inside the selected CodeSpace checkout."""

from __future__ import annotations

import json
import shlex
import asyncio
from argparse import Namespace
from typing import TYPE_CHECKING

from repository_policy import AgentHostingError, validate_hosting_result

if TYPE_CHECKING:
    from ssh_manager import ConnectionManager

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

def guest_check_command(workspace: str | None, *, operation: str = "launch") -> str:
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


def guarded_launch(command: str, workspace: str, *, provision: str = "") -> str:
    preparation = f"  {provision} || return $?\n" if provision else ""
    return (
        "repository_policy_launch() {\n"
        f"{preparation}"
        f"  {guest_check_command(workspace)} >&2 || return $?\n"
        f'  {command} "$@"\n'
        "}\nrepository_policy_launch"
    )


def admit_stdio_launch(args: Namespace) -> None:
    """Check a direct managed SSH launch before claims, relay or provisioning."""
    if not getattr(args, "stdio", False):
        return
    from .__main__ import _lookup_codespace_repo
    from .config import load_merged_config

    config = load_merged_config()
    repository = _lookup_codespace_repo(args.name)
    requested = getattr(args, "repo", None)
    prepared = getattr(args, "plugin_prepared_context", None) or {}
    if not isinstance(prepared, dict):
        raise RuntimeError("Repository hosting admission requires a prepared context mapping")
    workspace = prepared.get("workspace") or (
        config.workspace_folder_for_request(repository, requested)[0]
        if requested else config.resolved_workspace_folder_for(repository)
    )
    require_codespace_hosting(
        args.name, workspace, worktree_id=getattr(args, "plugin_worktree_id", None),
    )


async def require_guest_hosting(
    manager: ConnectionManager, name: str, workspace: str | None,
) -> None:
    from ._ssh_retry import exec_with_retry
    from .worktrees import validate_context

    validate_context()
    result = await exec_with_retry(
        manager, name, f"bash -lc {shlex.quote(guest_check_command(workspace))}", timeout=90,
    )
    if result.exit_code:
        raise RuntimeError(
            "Guest repository hosting refused or receiver unsupported: "
            + (result.stderr.strip() or result.stdout.strip() or f"exit {result.exit_code}")
        )
    try:
        policy = json.loads(result.stdout)
    except (TypeError, ValueError) as exc:
        raise RuntimeError("Guest repository hosting receiver returned invalid policy JSON") from exc
    try:
        validate_hosting_result(policy)
    except AgentHostingError as exc:
        raise RuntimeError(f"Guest repository hosting receiver returned invalid policy: {exc}") from exc


def require_codespace_hosting(
    name: str, workspace: str | None, *, worktree_id: str | None = None,
    live_attachment: bool = False,
) -> None:
    """Preflight without provisioning plugins or starting a Connection Owner."""
    from ssh_manager import ConnectionManager
    from plugin_resolve.reflection import parse_execution_checkout

    from ._ssh_retry import exec_with_retry
    from .codespace_config import CodespaceSource
    from .lifecycle import list_codespaces
    from .resolver import _find_codespace
    from .worktrees import validate_context

    validate_context()
    try:
        codespace = _find_codespace(list_codespaces(), name)
    except (KeyError, ValueError) as exc:
        raise RuntimeError(f"Cannot resolve the CodeSpace for read-only hosting admission: {exc}") from exc
    if codespace.state != "Available":
        raise RuntimeError(
            "Cannot inspect repository hosting policy without waking the CodeSpace; "
            "make the source checkout inspectable before managed agent launch"
        )
    actual_name = codespace.name

    async def check() -> None:
        manager = ConnectionManager()
        try:
            source = CodespaceSource(actual_name, account=codespace.account or None)
            await manager.ensure_connected(actual_name, source, [])
            if live_attachment:
                command = (
                    "WORKTREES=$(command -v agent-worktrees) && test -f \"$WORKTREES\" && "
                    '"$WORKTREES" agent-policy check --help >/dev/null'
                )
                result = await exec_with_retry(
                    manager, actual_name, f"bash -lc {shlex.quote(command)}", timeout=90,
                )
                if result.exit_code:
                    raise RuntimeError("Required guest repository hosting receiver is unsupported")
                return
            actual = workspace
            if worktree_id:
                command = (
                    (f"cd -- {shlex.quote(workspace)} && " if workspace else "") +
                    "WORKTREES=$(command -v agent-worktrees) && test -f \"$WORKTREES\" && "
                    '"$WORKTREES" resolve --json --dry-run --no-fast-forward --defer-projections '
                    f"--worktree-id {shlex.quote(worktree_id)}"
                )
                result = await exec_with_retry(
                    manager, actual_name, f"bash -lc {shlex.quote(command)}", timeout=90,
                )
                if result.exit_code:
                    raise RuntimeError("Guest could not resolve the actual execution worktree")
                actual = parse_execution_checkout(result.stdout)
            await require_guest_hosting(manager, actual_name, actual)
        finally:
            await manager.disconnect(actual_name)

    asyncio.run(check())


def admit_detached_launch(args: Namespace, plan: dict) -> bool:
    """Establish live-attachment intent and admit before detached state effects."""
    from venue_copilot import live_session_for

    rejoining = (live_session_for(plan["scope_id"]) or {}).get("status") == "live"
    args.hosting_live_attachment = rejoining
    require_codespace_hosting(
        args.name, plan["workspace_folder"], worktree_id=getattr(args, "worktree_id", None),
        live_attachment=rejoining,
    )
    return rejoining
