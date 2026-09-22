"""Parser registration for declarative reviewer/issue loop families."""

from __future__ import annotations

from .loop_commands import _cmd_repository_issue_loop
from .reviewer_loop_commands import _cmd_reviewer_loop


def register_declaration_loop_commands(sub) -> None:
    p = sub.add_parser(
        "reviewer-loop",
        help="inspect and operate one repository-owned reviewer-loop declaration",
    )
    loop_sub = p.add_subparsers(dest="reviewer_loop_command", required=True)
    lp = loop_sub.add_parser("setup", help="register the declaration's repository with the existing registrar")
    lp.add_argument("declaration", help="path to the reviewer-loop JSON/YAML file")
    lp.add_argument("--name", help="pointer name (default: repository directory name)")
    lp.add_argument("--owner", help="declaration owner override")
    lp.set_defaults(func=_cmd_reviewer_loop)
    for command, help_text in (("inspect", "expand the declaration and show its effective supervised units"), ("status", "join declaration, service, task, and recovery status"), ("doctor", "diagnose inactive or unhealthy reviewer-loop state"), ("enable", "clear local overrides for every unit in the reviewer loop")):
        lp = loop_sub.add_parser(command, help=help_text)
        lp.add_argument("declaration", help="path to the reviewer-loop JSON/YAML file")
        lp.add_argument("--owner", help="declaration owner override")
        if command in {"status", "doctor"}:
            lp.add_argument("--limit", type=int, default=200, help="maximum associated tasks and failed reservations to inspect")
        lp.set_defaults(func=_cmd_reviewer_loop)
    lp = loop_sub.add_parser("disable", help="locally override every unit in the reviewer loop off")
    lp.add_argument("declaration", help="path to the reviewer-loop JSON/YAML file")
    lp.add_argument("--reason", help="why the loop is disabled")
    lp.add_argument("--owner", help="declaration owner override")
    lp.set_defaults(func=_cmd_reviewer_loop)
    lp = loop_sub.add_parser("side-load", help="send one change through the declaration's emitter-owned path")
    lp.add_argument("declaration", help="path to the reviewer-loop JSON/YAML file")
    lp.add_argument("change_ref", help="target change reference")
    lp.add_argument("--owner", help="declaration owner override")
    lp.set_defaults(func=_cmd_reviewer_loop)

    p = sub.add_parser(
        "repository-issue-loop",
        help="inspect and operate a declarative repository issue backlog loop",
    )
    issue_loop_sub = p.add_subparsers(dest="repository_issue_loop_command", required=True)
    lp = issue_loop_sub.add_parser("setup", help="register the declaration's repository with the existing registrar")
    lp.add_argument("declaration", help="path to the repository-issue-loop JSON/YAML file")
    lp.add_argument("--name", help="pointer name")
    lp.add_argument("--owner", help="declaration owner override")
    lp.set_defaults(func=_cmd_repository_issue_loop)
    for command, help_text in (("inspect", "expand the declaration and show its supervised units"), ("status", "join source, reservation, task, pool, and service status"), ("doctor", "diagnose unhealthy repository issue-loop state"), ("enable", "clear local overrides for the whole loop"), ("discover", "dry-run the current occurrence and eligible issue set")):
        lp = issue_loop_sub.add_parser(command, help=help_text)
        lp.add_argument("declaration", help="path to the repository-issue-loop JSON/YAML file")
        lp.add_argument("--owner", help="declaration owner override")
        if command in {"status", "doctor"}:
            lp.add_argument("--limit", type=int, default=200)
        lp.set_defaults(func=_cmd_repository_issue_loop)
    lp = issue_loop_sub.add_parser("disable", help="locally override the whole loop off")
    lp.add_argument("declaration", help="path to the repository-issue-loop JSON/YAML file")
    lp.add_argument("--reason", help="why the loop is disabled")
    lp.add_argument("--owner", help="declaration owner override")
    lp.set_defaults(func=_cmd_repository_issue_loop)
