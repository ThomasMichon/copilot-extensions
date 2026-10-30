"""Explicit recovery command, independent of ordinary registration and binding."""

from __future__ import annotations

import argparse
from dataclasses import asdict, fields
import json
import sys

from . import conditional_head_replacement as replacement, output


CONTRACT = "agent-worktrees/conditional-head-replacement"


def add_parsers(sub) -> None:
    sp = sub.add_parser(
        "replace-dead-head",
        help="Explicitly promote an already registered replacement for a stopped native head",
    )
    actions = sp.add_subparsers(dest="replacement_action", required=True)
    actions.add_parser("capabilities", help="Read this runtime's replacement contract")
    observe = actions.add_parser("observe", help="Read a registered candidate's current native proof")
    observe.add_argument("--worktree-id", required=True)
    observe.add_argument("--session-id", required=True)
    observe.add_argument("--pid", type=int, required=True, help="Lookup hint; never proof by itself")
    check = actions.add_parser("check", help="Read whether the exact old head has no live holders")
    check.add_argument("--stdin", action="store_true", required=True)
    apply = actions.add_parser("apply", help="Apply one exact authorized replacement request")
    apply.add_argument("--permit", action="store_true", help="Confirm authorization for this request")
    apply.add_argument("--stdin", action="store_true", required=True, help="Read the exact JSON request")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate request field: {key}")
        result[key] = value
    return result


def cmd_replace_dead_head(args: argparse.Namespace) -> int:
    if args.replacement_action == "capabilities":
        output._json_output({
            "version": 1, "contract": CONTRACT, "versions": [replacement.CONTRACT_VERSION],
            "candidate_observation": 1, "preflight": 1,
        })
        return 0
    if args.replacement_action == "observe":
        try:
            candidate = replacement.observe_candidate(args.worktree_id, args.session_id, args.pid)
        except (ValueError, OSError, TimeoutError) as exc:
            return output._json_error(f"Candidate unconfirmed: {exc}", exit_code=3)
        output._json_output({"contract": CONTRACT, "version": 1, "candidate": candidate})
        return 0
    if (
        args.replacement_action not in {"apply", "check"}
        or args.replacement_action == "apply" and getattr(args, "permit", False) is not True
        or getattr(args, "stdin", False) is not True
    ):
        return output._json_error("Replacement requires apply --permit --stdin", exit_code=2)

    try:
        text = sys.stdin.read(65537)
        if len(text) > 65536:
            raise ValueError("Replacement request exceeds 65536 characters")
        data = json.loads(text, object_pairs_hook=_unique_object)
        target_type = (
            replacement.ReplacementTarget if args.replacement_action == "check"
            else replacement.HeadReplacement
        )
        names = {field.name for field in fields(target_type)}
        if not isinstance(data, dict) or data.keys() != names | {"contract", "version"}:
            raise ValueError("Replacement request must contain exactly the declared contract fields")
        if (
            data.pop("contract") != CONTRACT
            or type(data["version"]) is not int
            or data.pop("version") != replacement.CONTRACT_VERSION
        ):
            raise ValueError("Unsupported conditional-head-replacement contract")
        for name in names - {"native_source"}:
            expected_type = int if name in {
                "expected_head_revision", "expected_lifecycle_revision",
            } else str
            if type(data[name]) is not expected_type:
                raise ValueError(f"Invalid replacement field: {name}")
        if args.replacement_action == "check":
            checked = replacement.check_dead_head(target_type(**data))
            output._json_output({"contract": CONTRACT, "version": 1, "eligible": True, "target": checked})
            return 0
        if not isinstance(data["native_source"], dict):
            raise ValueError("Replacement requires an original native source witness")
        result = replacement.replace_dead_head(replacement.HeadReplacement(**data))
    except (ValueError, OSError, TimeoutError) as exc:
        return output._json_error(f"Replacement refused or unconfirmed: {exc}", exit_code=3)
    output._json_output({
        "contract": CONTRACT, "version": replacement.CONTRACT_VERSION,
        "replaced": True, **asdict(result),
    })
    return 0
