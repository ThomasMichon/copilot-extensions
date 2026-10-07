"""``agent-codespaces dial-log <name>`` -- what every connection attempt to a CodeSpace did.

Reads the bounded, structured dial log the shared ssh-manager writes (one line
per ``gh codespace ssh --config`` fetch, ControlMaster start, direct-mode exec
and health reconnect; see ``ssh_manager.dial_log``): dial counts per outcome over
the last 10 minutes and hour, the last failure, and the newest entries.
Observe-only: it reads the log and never dials.
"""

from __future__ import annotations

import json


def add_dial_log_parser(sub) -> None:
    p = sub.add_parser(
        "dial-log",
        help="Show recent connection attempts to a CodeSpace (config fetches, connects, "
             "direct execs, reconnects) with counts per outcome; reads the log, never dials",
    )
    p.add_argument("name", help="CodeSpace name (the dial log's target)")
    p.add_argument("--last", type=int, default=20, help="Newest entries to show (default 20)")
    p.add_argument("--json", action="store_true", help="Emit the summary and entries as JSON")
    p.set_defaults(func=cmd_dial_log)


def _safe(value) -> str:
    """A log field for the terminal: control characters (an ESC/OSC sequence in a
    remote's stderr, say) shown escaped, never interpreted."""
    return "".join(c if c.isprintable() else repr(c)[1:-1] for c in str(value if value is not None else ""))


def _secs(value) -> float:
    return float(value) if isinstance(value, (int, float)) else 0.0


def cmd_dial_log(args) -> int:
    from ssh_manager import dial_log

    summary = dial_log.summary(args.name)
    entries = dial_log.read(args.name, last=max(args.last, 0))
    if args.json:
        print(json.dumps({**summary, "recent": entries}, indent=2))
        return 0
    print(f"{_safe(args.name)}: {summary['entries']} logged attempt(s)  [{_safe(summary['log'])}]")
    for window in ("last_10m", "last_1h"):
        counts = summary[window]
        by = ", ".join(f"{_safe(k)} {v}" for k, v in sorted(counts["by_outcome"].items())) or "none"
        print(f"  {window}: {counts['dials']} dial(s) ({by})")
    failure = summary.get("last_failure")
    if failure:
        print(f"  last failure: {_safe(failure.get('at'))} {_safe(failure.get('kind'))} "
              f"{_safe(failure.get('outcome'))} {_safe(failure.get('reason'))}".rstrip())
    for e in entries:
        tail = f"  {_safe(e.get('stderr'))[:120]}" if e.get("stderr") else ""
        print(f"    {_safe(e.get('at', '?'))[:23]}  {_safe(e.get('kind', '?')):<14} "
              f"{_safe(e.get('outcome', '?')):<10} {_secs(e.get('elapsed_s')):>7.2f}s  "
              f"#{_safe(e.get('attempt') or '-')} {_safe(e.get('account'))}{tail}")
    return 0
