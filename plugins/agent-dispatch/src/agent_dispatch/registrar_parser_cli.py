"""Parser registration for the registrar CLI family."""

from __future__ import annotations

from .loop_commands import _resolve_cli_module


def _core():
    return _resolve_cli_module()


def register_registrar_commands(sub) -> None:
    p = sub.add_parser(
        "registrar",
        help="declarative-supervision registrar: manage discovery pointers and read the declared profile set (declarations are the one source of truth; the CLI is a thin writer/reader over them)",
    )
    reg_sub = p.add_subparsers(dest="registrar_command", required=True)
    rp = reg_sub.add_parser("add-pointer", help="record (or replace) a pointer to a location of declaration documents")
    rp.add_argument("name", help="unique pointer name (letters, digits, '-', '_')")
    rp.add_argument("location", help="directory of declaration docs, or (with --kind repo) a repo root whose .copilot-extensions/agent-dispatch/registrar/ (legacy .agent-dispatch/registrar/) is read")
    rp.add_argument("--kind", choices=["dir", "repo"], default="dir")
    rp.add_argument("--owner", help="provenance stamped on declarations read here")
    rp.add_argument(
        "--alias",
        dest="aliases",
        action="append",
        help="canonical lane this 'repo'-kind pointer backs (repeatable; a task's "
        "repo lane must match one of these exactly to be agent-backed -- see "
        "agent_backed_enforcement_enabled). Omitted on a brand-new pointer (or one "
        "being retargeted to a different location/--kind): auto-derived from the "
        "repo root's own 'origin' remote when --kind repo, else empty. Omitted on "
        "an existing pointer re-registered at the SAME location/--kind: its "
        "current aliases are preserved unchanged -- pass --alias explicitly to "
        "add one to a pre-existing alias-less pointer, or to replace its aliases.",
    )
    rp.set_defaults(func=_core()._cmd_registrar)
    rp = reg_sub.add_parser("list", help="list the recorded discovery pointers")
    rp.set_defaults(func=_core()._cmd_registrar)
    rp = reg_sub.add_parser("doctor", help="audit trusted pointers and attributed registrar.d candidates")
    rp.add_argument("--json", action="store_true", help="emit exhaustive structured registrar findings")
    rp.set_defaults(func=_core()._cmd_registrar)
    rp = reg_sub.add_parser("remove", help="remove a discovery pointer by name")
    rp.add_argument("name", help="the pointer name to remove")
    rp.set_defaults(func=_core()._cmd_registrar)
    rp = reg_sub.add_parser("discover", help="read + aggregate the declared profile set across all pointers (rejects duplicate profile names across sources)")
    rp.set_defaults(func=_core()._cmd_registrar)
    rp = reg_sub.add_parser("discover-repo", help="read a single synced repo's in-repo declarations")
    rp.add_argument("repo_root", help="path to the repo root to read declarations from")
    rp.add_argument("--owner", help="provenance override (default: repo:<name>)")
    rp.set_defaults(func=_core()._cmd_registrar)
    rp = reg_sub.add_parser(
        "vocabulary",
        help="aggregate the active declaration set's effective permit values per "
        "filter dimension -- the known-value vocabulary a task could target to "
        "be eligible for at least one active pool (e.g. for a picker's "
        "dynamically-sourced criteria dropdown)",
    )
    rp.add_argument(
        "--dim",
        choices=sorted(["repo", "machine", "env", "role", "worktree", "task-type", "capabilities"]),
        help="emit only this dimension's values (a flat array) instead of the "
        "full per-dimension mapping",
    )
    rp.add_argument("--json", action="store_true", help="emit machine-readable JSON")
    rp.set_defaults(func=_core()._cmd_registrar)
