"""A fake ``agent-worktrees`` engine that emits the Aperture Labs demo fixture.

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

from . import demo


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    # Parse the client's argument surface: the engine-global ``--project <name>``
    # the client prepends, the verb, and (for resolve) the launch selectors.
    verb = None
    worktree_id = None
    new = False
    bare_resume = False
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--project":
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
        json.dump(demo.list_envelope(), sys.stdout)
        sys.stdout.write("\n")
        return 0
    if verb == "resolve":
        json.dump(demo.resolve_plan(worktree_id, new=new, bare_resume=bare_resume),
                  sys.stdout)
        sys.stdout.write("\n")
        return 0
    # Unknown verb: mimic the engine's JSON error envelope + non-zero exit.
    json.dump({"version": 1, "error": f"demo engine has no verb {verb!r}"}, sys.stdout)
    sys.stdout.write("\n")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
