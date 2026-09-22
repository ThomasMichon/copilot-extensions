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
        reason = effective.get("reason") or "not-configured"
        context = (
            "## agent-index: not enabled for this repository\n\n"
            f"agent-index is **not enabled** here (reason: `{reason}`). No "
            "search/index capability is available in this session -- do not "
            "try `agent-index` commands, and do not install/configure/start "
            "it from an agent turn. Use `grep`/`glob` instead. Enabling "
            "agent-index is an operator/config decision "
            "(`.agent-index/config.yaml`), not something to do here."
        )
        return {"additionalContext": context}
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
        "## agent-index: enabled for this repository\n\n"
        "A semantic + lexical index is available to **every agent** in this "
        "session. Sources:\n\n"
        f"{rows}\n\n"
        "Use the `searching-the-harness-index` skill for how to call it "
        "(take `commands[id=agent-index].argv` from the injected command "
        "catalog)."
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
