"""Explicit, receipt-authorized Agent Machines attribution, retirement, deactivation, repair, and uninstall."""
from __future__ import annotations

import argparse
import json
import os
import sys
from collections.abc import Callable
from types import ModuleType
from typing import Any


def initialize_requested_output() -> None:
    if os.environ.get("COPILOT_EXTENSIONS_HUMAN_UTF8") == "1":
        if sys.stdout.isatty() or sys.stderr.isatty():
            raise RuntimeError("Human UTF-8 output requires redirected stdout and stderr.")
        sys.stdout.reconfigure(encoding="utf-8", errors=sys.stdout.errors)
        sys.stderr.reconfigure(encoding="utf-8", errors=sys.stderr.errors)


def main(
    argv: list[str] | None = None, *,
    lifecycle: Callable[[argparse.Namespace], dict[str, Any]], ic: ModuleType,
) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "action",
        choices=(
            "cell-repair",
            "cell-uninstall",
            "cell-attribute-legacy",
            "cell-deactivate",
            "cell-retire-legacy",
        ),
    )
    parser.add_argument("--maintenance-token")
    for name in ("context", "durable-home", "expected-marketplace-id", "expected-payload-root",
                 "expected-payload-version", "snapshot-id", "runtime-version"):
        parser.add_argument(f"--{name}")
    for name in ("expected-namespace-generation", "expected-install-generation", "expected-activation-generation",
                 "expected-tombstone-activation-generation"):
        parser.add_argument(f"--{name}", type=ic._parse_cli_generation)
    parser.add_argument("--expect-tombstone-absent", action="store_true")
    for marker in ("current", "last-known-good"):
        group = parser.add_mutually_exclusive_group(required=False)
        group.add_argument(f"--expected-{marker}-version")
        group.add_argument(f"--expect-{marker}-absent", action="store_true")
    args = parser.parse_args(argv)
    if args.action in {"cell-repair", "cell-uninstall", "cell-retire-legacy"}:
        required = (
            "context",
            "durable_home",
            "expected_marketplace_id",
            "expected_payload_root",
            "expected_payload_version",
            "snapshot_id",
            "runtime_version",
            "expected_namespace_generation",
            "expected_install_generation",
        )
        if args.action == "cell-retire-legacy":
            required += ("expected_activation_generation",)
        missing = [name for name in required if getattr(args, name) in (None, "")]
        if missing:
            parser.error(
                "the following arguments are required for "
                f"{args.action}: " + ", ".join(f"--{name.replace('_', '-')}" for name in missing)
            )
        if args.action != "cell-retire-legacy":
            for marker in ("current", "last_known_good"):
                if not (
                    getattr(args, f"expected_{marker}_version") or getattr(args, f"expect_{marker}_absent")
                ):
                    parser.error(
                        f"{args.action} requires either --expected-{marker.replace('_', '-')}-version "
                        f"or --expect-{marker.replace('_', '-')}-absent"
                    )
    elif args.action == "cell-deactivate":
        required = (
            "context",
            "durable_home",
            "expected_marketplace_id",
            "expected_namespace_generation",
            "expected_install_generation",
            "expected_activation_generation",
        )
        missing = [name for name in required if getattr(args, name) in (None, "")]
        if missing:
            parser.error(
                "the following arguments are required for "
                f"{args.action}: " + ", ".join(f"--{name.replace('_', '-')}" for name in missing)
            )
        if (
            (args.expected_tombstone_activation_generation is None)
            == (not args.expect_tombstone_absent)
        ):
            parser.error(
                f"{args.action} requires exactly one of "
                "--expected-tombstone-activation-generation or --expect-tombstone-absent"
            )
    else:
        for name in ("context", "durable_home", "expected_marketplace_id"):
            if getattr(args, name) in (None, ""):
                parser.error(
                    "the following arguments are required for "
                    f"{args.action}: --{name.replace('_', '-')}"
                )
    try:
        print(json.dumps(lifecycle(args), separators=(",", ":")))
        return 0
    except (ic.InstallationContextError, OSError) as error:
        print(f"agent-machines lifecycle: {error}", file=sys.stderr)
        return 1
