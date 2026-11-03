"""Passive rich-history commands through an explicitly admitted agenda locator."""

from __future__ import annotations

import argparse
from pathlib import Path
import sys

from agenda import AgendaError
from agenda_history import history_page, reader_info
import agenda_history_contract as wire

COMMANDS = ("reader-info", "history-page")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("command", choices=COMMANDS)
    parser.add_argument("--state-home", type=Path, required=True,
                        help="explicit adopter-owned locator; never guessed from a publication")
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--store-id", help="expected store identity; required for history pages")
    parser.add_argument("--contract-version", type=int, default=1)
    parser.add_argument("--before-cursor")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--json", action="store_true", help="output is always bounded JSON")
    args = parser.parse_args(argv)
    try:
        if args.command == "reader-info":
            if args.before_cursor is not None or args.limit is not None:
                raise wire.HistoryError("before-cursor/limit require history-page")
            output = reader_info(args.state_home, args.worktree,
                                 expected_store_id=args.store_id,
                                 contract_version=args.contract_version)
        else:
            if args.store_id is None:
                raise wire.HistoryError("history-page requires an expected store-id")
            output = history_page(args.state_home, args.worktree, args.store_id,
                                  before_cursor=args.before_cursor,
                                  limit=64 if args.limit is None else args.limit,
                                  contract_version=args.contract_version)
        code = 0
    except AgendaError as error:
        output, code = wire.failure(error), 2
    try:
        raw = wire.encoded(output)
        sys.stdout.buffer.write(raw)
        sys.stdout.buffer.flush()
        return code
    except BrokenPipeError:
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
