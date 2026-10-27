"""Read the optional Worktrees owner's existing state-root API in its own runtime."""

from __future__ import annotations

import json


def main() -> int:
    try:
        from agent_worktrees.state_root_request import StateRootRequestRefused, resolve
    except ImportError:
        return 127
    try:
        value = resolve()
    except StateRootRequestRefused:
        return 126
    print(json.dumps(value.as_dict()))
    return 0 if value.path else 3


if __name__ == "__main__":
    raise SystemExit(main())
