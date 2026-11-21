"""Cheap legacy-cache observations shared by status, update and doctor."""
from pathlib import Path
import sys
from typing import Any, TextIO

from agent_procutil import python_cache_status

from . import config as cfg


def report() -> dict[str, Any]:
    root = Path(cfg.install_dir()).absolute()
    return python_cache_status.describe(root, argv=[
        "agent-worktrees", "python-cache", "collect", "--root", str(root), "--drain", "--json",
    ])


def render(value: dict[str, Any] | None = None, *, stream: TextIO | None = None) -> None:
    value = report() if value is None else value
    stream = sys.stdout if stream is None else stream
    summary = value.get("summary")
    if not summary:
        print(f"Legacy bytecode: {value['observation']}; size unknown.", file=stream)
    else:
        unmeasured = summary.get("unmeasured_prefixes")
        print(
            "Legacy bytecode (cached): "
            f"{summary.get('measured_bytes', 'unknown')} measured bytes; "
            f"{summary.get('eligible_bytes', 'unknown')} snapshot-eligible; "
            f"{summary.get('held_bytes', 'unknown')} held; "
            f"{unmeasured if unmeasured is not None else 'unknown'} unmeasured prefixes; "
            f"observation age {value['observation_age_seconds']:.0f}s.",
            file=stream,
        )
        for reason, count in sorted(value.get("reasons", {}).items()):
            print(f"  {reason}: {count}", file=stream)
    print(f"  Inspect (dry run): {value['inspect_command']}", file=stream)
    print("  Observations do not authorize apply; collection rechecks current proof.", file=stream)
