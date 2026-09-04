#!/usr/bin/env python3
"""Emit repository-scoped agent-index usage guidance."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
if str(SCRIPT_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPT_DIR))

from resolve_effective_config import resolve


def render(cwd: str | None = None) -> dict[str, str]:
    effective = resolve(cwd)
    sources = effective.get("sources")
    if not effective.get("opted_in") or not isinstance(sources, list) or not sources:
        return {}
    rows = "\n".join(
        "  - {label} (source `{name}`){trust}".format(
            label=source.get("repo") or source["name"],
            name=source["name"],
            trust=(
                f" [{source['trust_domain']}]"
                if source.get("trust_domain")
                else ""
            ),
        )
        for source in sources
    )
    context = (
        "## agent-index retrieval — use the session command catalog\n\n"
        "A semantic + lexical index of this harness is available to **every agent**.\n"
        "Take `commands[id=agent-index].argv` from the injected command catalog and\n"
        "append the arguments below (no sub-agent, no MCP tool, no PATH lookup). "
        "It covers:\n\n"
        f"{rows}\n\n"
        "**How to search:**\n"
        '- `<catalog argv[0]> search "<natural-language or code query>" '
        "[--source <name>] [--language <lang>] [--repo <repo>] [--limit N] "
        "--json` — ranked hits; each has `chunk_id`, `source`, `file_path`, "
        "`line_start`/`line_end`, `content`.\n"
        "- `<catalog argv[0]> similar <chunk_id> [--source <name>] [--limit N]` "
        "— pivot 'more like this' from a hit.\n"
        "- `<catalog argv[0]> clusters [--source <name>] [--exact-dupes-only] "
        "[--limit N]` — near-duplicate groups.\n"
        "- `<catalog argv[0]> status` — index health + per-source coverage; "
        "probe once if results look sparse.\n\n"
        "**Prefer the catalog command's `search` subcommand** over a broad "
        "`grep`/`glob` sweep when searching **within these scopes** by "
        "meaning/behavior, for the most-relevant few results across a large "
        "corpus, or to pivot from a hit. Pass `--source` to scope to one corpus "
        "(and to respect trust-domain boundaries, not yet enforced at query "
        "time). Fall back to `grep`/`glob` for exact-string hunts, files outside "
        "these scopes, or if the index is unavailable. Read-only: never reindex "
        "from an agent — that is the operator flow (`<catalog argv[0]> index`)."
    )
    return {"additionalContext": context}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--cwd")
    args = parser.parse_args()
    print(json.dumps(render(args.cwd), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
