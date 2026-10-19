"""Optional bounded ``pr-watch reply`` and ``requeue`` CLI dispatch."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

from .pr_ado_remediation import RemediationError
from .providers.base import ProviderError


def _file(path):
    file = Path(path)
    if file.stat().st_size > 65536:
        raise RemediationError("Input file exceeds 64 KiB")
    return file.read_text(encoding="utf-8")


def dispatch(argv: list[str], *, state_dir=None, remediator_factory=None) -> int | None:
    if not argv or argv[0] not in ("reply", "requeue"):
        return None
    verb = argv[0]
    parser = argparse.ArgumentParser(prog="pr-watch " + verb)
    parser.add_argument("repo")
    parser.add_argument("pr", type=int)
    parser.add_argument("--config")
    parser.add_argument("--json", action="store_true")
    if verb == "requeue":
        parser.add_argument("--policy", required=True)
        parser.add_argument("--reason", choices=("expired", "flaky"), required=True)
        parser.add_argument("--expected-head", required=True)
        parser.add_argument("--evidence-file")
    else:
        parser.add_argument("--thread", required=True, type=int)
        parser.add_argument("--text-file", required=True)
        parser.add_argument("--expected-thread", required=True)
        parser.add_argument("--idempotency-key", required=True)
        parser.add_argument("--status", choices=("fixed", "wontFix", "byDesign", "closed", "pending"))
    try:
        args = parser.parse_args(argv[1:])
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        from .pr_read_context import load_read_config
        from .pr_config import resolve_repo_config_for_slug
        from .providers import account_token_for_slug, get_provider
        from .providers.azure_remediation import Remediator

        config = load_read_config(Path(args.config) if args.config else None)
        resolution = resolve_repo_config_for_slug(config, args.repo)
        if not resolution.resolved:
            raise RemediationError(resolution.error or "Target PR binding is unresolved")
        binding = resolution.repo_config.pr
        if binding.provider != "azure-devops":
            raise RemediationError("Bounded remediation is unsupported for this provider")
        provider = get_provider(binding.provider)
        token = account_token_for_slug(args.repo, binding)
        operation = (remediator_factory or Remediator)(
            provider, args.repo, args.pr, api_base=binding.api_base, token=token,
            state_dir=state_dir)
        if verb == "requeue":
            result = operation.requeue(args.policy, expected_head=args.expected_head,
                                       reason=args.reason,
                                       evidence=_file(args.evidence_file) if args.evidence_file else "")
        else:
            result = operation.reply(args.thread, text=_file(args.text_file),
                                     expected_thread=args.expected_thread,
                                     idempotency_key=args.idempotency_key, status=args.status)
        print(json.dumps(result))
        return 0 if result["state"] == "verified" else 1
    except (RemediationError, ProviderError, ValueError, OSError) as exc:
        message = str(exc) if isinstance(exc, (ValueError, ProviderError)) else "Local remediation state/input is unavailable"
        if args.json:
            print(json.dumps({"state": "refused_or_unknown", "error": message}))
        else:
            print("pr-watch " + verb + ": " + message, file=sys.stderr)
        return 2
