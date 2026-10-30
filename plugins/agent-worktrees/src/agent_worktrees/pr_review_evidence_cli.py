"""Explicit source-selected, read-only PR evidence entry point."""

from __future__ import annotations

import argparse
import json
import sys
import time


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Finite ADO evidence acquisition; no cache, source attachment or review effects.",
        allow_abbrev=False,
    )
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("capabilities", allow_abbrev=False)
    read = commands.add_parser("read", allow_abbrev=False)
    read.add_argument("--repo", required=True, help="explicit credential-bound project/repository")
    read.add_argument("--number", required=True, type=int)
    read.add_argument("--endpoint", required=True)
    read.add_argument("--credential-command", required=True, help="trusted non-secret JSON argv")
    read.add_argument("--budget", required=True, type=float, help="finite positive operation seconds")
    read.add_argument("--expected-principal", default=None)
    args = parser.parse_args(argv)
    if args.command == "capabilities":
        print(json.dumps({
            "schema": "agent-worktrees.review-evidence-capabilities", "version": 1,
            "evidence_versions": [1], "providers": ["azure-devops"],
            "capabilities": [
                "finite_acquisition", "renewable_observe_credentials",
                "principal_binding", "credential_budget",
            ],
            "effects": "none", "submission": False, "source_attachment": False,
        }))
        return 0
    try:
        from .pr_credential_command import command_source
        from .providers.azure_review_evidence import acquire_evidence
        from .providers.base import ProviderError
        from .providers.observation_credentials import ObservationCredentialError
    except ImportError:
        print(json.dumps({
            "error": "selected source interpreter lacks the owning provider dependencies",
            "effects": "none",
        }))
        return 1
    started = time.monotonic()
    try:
        result = acquire_evidence(
            args.repo, args.number, args.endpoint,
            credential_source_factory=lambda remaining: command_source(
                args.credential_command, remaining=remaining,
            ),
            budget=args.budget, expected_principal=args.expected_principal,
        )
        print(json.dumps(result, sort_keys=True, separators=(",", ":"), allow_nan=False))
        return 0
    except (ProviderError, ValueError, OSError, UnicodeError, RecursionError) as exc:
        known_reasons = {
            "ADO review credential principal changed": "principal_changed",
            "ADO review acquisition budget exhausted": "budget_exhausted",
            "ADO review evidence exceeds its byte budget": "byte_budget_exhausted",
        }
        print(json.dumps({
            "error": "ADO evidence acquisition failed",
            "category": "credential" if isinstance(exc, ObservationCredentialError) else "provider_or_input",
            "reason": known_reasons.get(str(exc), "credential_or_read_unavailable"),
            "transient": bool(getattr(exc, "transient", False)), "effects": "none",
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }))
        return 1


if __name__ == "__main__":
    sys.exit(main())
