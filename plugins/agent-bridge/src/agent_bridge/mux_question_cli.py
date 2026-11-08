"""Strict opt-in mux CLI selection; legacy/native forms retain their syntax."""

from __future__ import annotations

import argparse
import sys

from .client import BridgeClientError


def answer(args: argparse.Namespace) -> None:
    """Validate before resolving a client; mux never approves a permission."""
    from . import __main__ as core

    if (
        not args.inspection_id or (args.option is None) == (args.text is None)
        or args.fields or args.content_json is not None or args.tool_call_id
        or args.request_id or args.generation or args.option_id or args.decline or args.cancel
    ):
        print(
            "[FAIL] --via mux requires --inspection-id and exactly one --option/--text; "
            "native, permission and decline/cancel selectors cannot be combined.",
            file=sys.stderr,
        )
        raise SystemExit(2)
    try:
        result = core._get_client(ensure=False).answer_mux_question(
            args.session_id, args.inspection_id, sender=core._live_sender_label(args),
            option=args.option, text=args.text,
        )
    except BridgeClientError as exc:
        print(f"[FAIL] {exc.detail}", file=sys.stderr)
        raise SystemExit(1) from exc
    core._json_out(result)
    if result.get("state") != "answered":
        raise SystemExit(1)
