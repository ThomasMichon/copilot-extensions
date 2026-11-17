"""A fake ``agent-worktrees`` engine that emits the Example Labs demo fixture.

Run as ``python -m worktree_manager.demo_engine <args>``. It accepts the same
argument surface the Manager sends the real engine (``[--project P] list --json
[--classify] [--mux-details] …``) and prints the demo ``list --json`` envelope on
stdout. The Picker's ``--demo`` mode points ``engine_client`` at this module via
``WORKTREE_MANAGER_ENGINE_CMD``, so the whole render path — subprocess spawn, JSON
parse, dataclass mapping — is exercised faithfully, just with mock data.

It is a *test/demo* double, not shipped behaviour: nothing in the normal Manager
flow invokes it unless the demo override is set.
"""

from __future__ import annotations

import json
import sys
import time
from pathlib import Path

try:
    from . import demo
except ImportError:  # pragma: no cover - direct-script fallback
    src_root = Path(__file__).resolve().parents[1]
    if str(src_root) not in sys.path:
        sys.path.insert(0, str(src_root))
    from worktree_manager import demo


def command_argv() -> list[str]:
    """Run the bundled fake engine without depending on ambient PYTHONPATH."""
    return [sys.executable, "-B", str(Path(__file__).resolve())]


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # Parse the client's argument surface: the engine-global ``--project <name>``
    # the client prepends, the verb, and (for resolve) the launch selectors.
    project = demo.DEMO_PROJECT
    verb = None
    worktree_id = None
    new = False
    bare_resume = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--project":
            if i + 1 < len(args):
                project = args[i + 1] or project
            i += 2
            continue
        if a == "--worktree-id":
            worktree_id = args[i + 1] if i + 1 < len(args) else None
            i += 2
            continue
        if a == "--new":
            new = True
        elif a == "--bare-resume":
            bare_resume = True
        elif not a.startswith("-") and verb is None:
            verb = a
        i += 1

    if verb == "list":
        envelope = demo.list_envelope()
        rows = envelope["worktrees"]
        if worktree_id is not None:
            rows = [row for row in rows if row["id"].endswith(worktree_id)]
        head = {
            "schema_version": 1, "quality": "current", "generation": "d" * 32,
            "revision": 1, "observed_at": 1, "roster_count": len(envelope["worktrees"]),
            "selected_count": len(rows),
        }
        if "--stream" in args or "--follow" in args:
            def emit(frame):
                print(json.dumps(frame), flush=True)

            emit({
                "type": "begin", "contract_version": 1, "projection": head,
                "selected_count": len(rows), "cursor": 1, "base_cursor": None,
            })
            for row in rows:
                emit({"type": "worktree", "phase": "current", "wt": row, "projection": head})
            emit({"type": "done", "projection": head, "cursor": 1})
            while "--follow" in args:
                time.sleep(2)
                emit({"type": "heartbeat", "generation": head["generation"], "cursor": 1})
        else:
            json.dump({
                **envelope, "ok": True, "contract_version": 1,
                "worktrees": rows, "projection": head,
            }, sys.stdout)
            sys.stdout.write("\n")
        return 0
    if verb == "resolve":
        json.dump(demo.resolve_plan(worktree_id, new=new, bare_resume=bare_resume),
                  sys.stdout)
        sys.stdout.write("\n")
        return 0
    if verb == "picker-bootstrap":
        json.dump(
            {
                "version": 1,
                "project": project,
                "should_switch_cwd": False,
                "cwd": None,
                "default_live": False,
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 0
    if verb == "repair-stale-anchor":
        json.dump(
            {
                "version": 1,
                "project": project,
                "status": "unchanged",
                "self_present_before": True,
                "self_present_after": True,
            },
            sys.stdout,
        )
        sys.stdout.write("\n")
        return 0
    # Unknown verb: mimic the engine's JSON error envelope + non-zero exit.
    json.dump({"version": 1, "error": f"demo engine has no verb {verb!r}"}, sys.stdout)
    sys.stdout.write("\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
