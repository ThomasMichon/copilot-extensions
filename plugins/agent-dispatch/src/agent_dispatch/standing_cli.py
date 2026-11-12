"""Explicit standing-charter commands; each mutation names its exact revision."""

from __future__ import annotations

import argparse
import sys
from types import ModuleType

from .loop_commands import _resolve_cli_module

_DECLARATION_FIELDS = (
    "namespace", "charter_key", "lane_key", "effort_ref", "materialization_ref",
    "source_revision", "expected_materialization_revision",
)
_WORKSPACE_FIELDS = ("machine", "project", "worktree", "path", "creation_nonce")
_LIFECYCLE = {
    "retire": "close admission; retain any occupant and unresolved obligations",
    "settle-retirement": "settle a retiring charter once no occupant or obligation remains",
    "reenroll": "explicitly reopen the same retired charter in a new generation",
}


def _core() -> ModuleType:
    return _resolve_cli_module()


def _revision(value: str) -> int:
    try:
        number = int(value, 10)
    except ValueError:
        number = 0
    if number < 1 or number > 2**63 - 1:
        raise argparse.ArgumentTypeError("must be a positive integer revision")
    return number


def _flag(name: str) -> str:
    return "--" + name.replace("_", "-")


def _add_revision(parser: argparse.ArgumentParser, *names: str) -> None:
    for name in ("expected_revision", *names):
        parser.add_argument(_flag(name), dest=name, type=_revision, required=True)


def _add_workspace(parser: argparse.ArgumentParser) -> None:
    group = parser.add_argument_group("exact workspace identity")
    for name in _WORKSPACE_FIELDS:
        group.add_argument(_flag(name), dest=f"workspace_{name}", required=True)


def _workspace(args: argparse.Namespace) -> dict[str, str]:
    return {name: getattr(args, f"workspace_{name}") for name in _WORKSPACE_FIELDS}


def register_standing_commands(sub: argparse._SubParsersAction) -> None:
    parser = sub.add_parser(
        "standing", allow_abbrev=False,
        help="reconcile and govern standing charters (control authority required)",
    )
    commands = parser.add_subparsers(dest="standing_command", required=True)

    reconcile = commands.add_parser(
        "reconcile", allow_abbrev=False,
        help="upsert one declared lane; never retires, accepts, or launches",
    )
    reconcile.add_argument("--title", required=True)
    reconcile.add_argument("--repo", help="lane (local name or remote URL); default: calling repo")
    for name in _DECLARATION_FIELDS:
        reconcile.add_argument(_flag(name), dest=name, required=True)
    reconcile.add_argument(_flag("expected_revision"), dest="expected_revision", type=_revision)
    reconcile.set_defaults(func=_cmd_reconcile)

    reserve = commands.add_parser(
        "reserve", allow_abbrev=False, help="retain one workspace assignment (not acceptance)",
    )
    reserve.add_argument("task_id")
    _add_revision(reserve)
    _add_workspace(reserve)
    reserve.set_defaults(func=_cmd_reserve)

    accept = commands.add_parser(
        "accept", allow_abbrev=False, help="accept one existing native head; never launch it",
    )
    accept.add_argument("task_id")
    _add_revision(accept, "expected_generation", "expected_head_revision")
    accept.add_argument("--owner-session-id", required=True)
    accept.add_argument("--expected-owner-session-id")
    _add_workspace(accept)
    accept.set_defaults(func=_cmd_accept)

    cancel = commands.add_parser(
        "cancel", allow_abbrev=False,
        help="cancel one unaccepted reservation and settle its original relationship",
    )
    cancel.add_argument("task_id")
    _add_revision(cancel, "expected_generation")
    _add_workspace(cancel)
    cancel.set_defaults(func=_cmd_cancel)

    release = commands.add_parser(
        "release", allow_abbrev=False, help="detach an exactly concluded owner",
    )
    release.add_argument("task_id")
    _add_revision(release, "expected_generation", "expected_conclusion_revision")
    release.add_argument("--owner-session-id", required=True)
    _add_workspace(release)
    release.set_defaults(func=_cmd_release)

    grant = commands.add_parser(
        "recovery-grant", allow_abbrev=False,
        help="delegate bounded recovery of an accepted pairing; never staff or launch",
    )
    grant.add_argument("task_id")
    _add_revision(grant, "expected_generation")
    _add_workspace(grant)
    grant.add_argument("--owner-session-id", required=True)
    grant.add_argument("--grant-id", required=True)
    grant.add_argument("--authority-ref", required=True)
    grant.add_argument("--expires-at", type=float, required=True, help="absolute Unix expiry time")
    grant.add_argument("--max-attempts", type=_revision, required=True)
    grant.set_defaults(func=_cmd_recovery_grant)

    revoke = commands.add_parser(
        "recovery-revoke", allow_abbrev=False,
        help="revoke future recovery without discarding pending effects",
    )
    revoke.add_argument("task_id")
    _add_revision(revoke)
    revoke.add_argument("--grant-id", required=True)
    revoke.set_defaults(func=_cmd_recovery_revoke)

    for action, summary in _LIFECYCLE.items():
        lifecycle = commands.add_parser(action, allow_abbrev=False, help=summary)
        lifecycle.add_argument("task_id")
        _add_revision(lifecycle)
        lifecycle.set_defaults(func=_cmd_lifecycle, standing_action=action)


def _mutate(args: argparse.Namespace, method: str, *call_args, **kwargs) -> int:
    with _core()._client(args, ensure=False, allow_ssh_failover=False) as client:
        result = getattr(client, method)(*call_args, **kwargs)
    return _core()._emit(result)


def _cmd_reconcile(args: argparse.Namespace) -> int:
    repo = _core()._scope_repo(args)
    if not repo:
        print(_core()._REPO_UNRESOLVED, file=sys.stderr)
        return 2
    declaration = {name: getattr(args, name) for name in _DECLARATION_FIELDS}
    return _mutate(
        args, "reconcile_standing_charter", args.title, declaration,
        repo=repo, expected_revision=args.expected_revision,
    )


def _cmd_reserve(args: argparse.Namespace) -> int:
    return _mutate(
        args, "reserve_standing_charter", args.task_id, _workspace(args),
        expected_revision=args.expected_revision,
    )


def _cmd_accept(args: argparse.Namespace) -> int:
    return _mutate(
        args, "accept_standing_charter", args.task_id, _workspace(args),
        expected_revision=args.expected_revision,
        expected_generation=args.expected_generation,
        owner_session_id=args.owner_session_id,
        expected_head_revision=args.expected_head_revision,
        expected_owner_session_id=args.expected_owner_session_id,
    )


def _cmd_cancel(args: argparse.Namespace) -> int:
    return _mutate(
        args, "cancel_standing_reservation", args.task_id, _workspace(args),
        expected_revision=args.expected_revision,
        expected_generation=args.expected_generation,
    )


def _cmd_release(args: argparse.Namespace) -> int:
    return _mutate(
        args, "release_standing_attachment", args.task_id, _workspace(args),
        expected_revision=args.expected_revision,
        expected_generation=args.expected_generation,
        owner_session_id=args.owner_session_id,
        expected_conclusion_revision=args.expected_conclusion_revision,
    )


def _cmd_lifecycle(args: argparse.Namespace) -> int:
    return _mutate(
        args, "change_standing_lifecycle", args.task_id, args.standing_action,
        expected_revision=args.expected_revision,
    )


def _cmd_recovery_grant(args: argparse.Namespace) -> int:
    return _mutate(
        args, "grant_standing_recovery", args.task_id, _workspace(args),
        expected_revision=args.expected_revision, expected_generation=args.expected_generation,
        owner_session_id=args.owner_session_id, grant_id=args.grant_id,
        authority_ref=args.authority_ref, expires_at=args.expires_at, max_attempts=args.max_attempts,
    )


def _cmd_recovery_revoke(args: argparse.Namespace) -> int:
    return _mutate(
        args, "revoke_standing_recovery", args.task_id, grant_id=args.grant_id,
        expected_revision=args.expected_revision,
    )
