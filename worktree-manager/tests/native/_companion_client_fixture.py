"""Wire-valid private owner for actual Companion/native-client controls."""

import os
from pathlib import Path
import venv

from _worktree_timeline_fixtures import peer


def fixture_engine(root: Path, checkout: Path) -> tuple[Path, Path]:
    """Keep the real app and passive probe; substitute only owner responses."""
    interpreter, calls = peer(root)
    module = root / "modules/agent_worktrees/__main__.py"
    original = module.read_text(encoding="utf-8")
    status = {
        "repo": "example", "id": "selected", "path": str(checkout),
        "branch": "worktree/selected", "state": "active",
        "summary": "Native client source fixture",
    }
    dispatch = f'''
    with (ROOT/"invocations.jsonl").open("a") as out:
        out.write(json.dumps(sys.argv[1:])+"\\n")
    args=sys.argv[1:]
    responses={{
        "status-segment":{status!r},
        "list-sessions":{{"sessions":[{{"id":"head-a","is_head":True}}]}},
        "worktree-status-bundle":{{"worktree_id":"selected","facts":{{}}}},
        "head-session":{{"worktree_id":"selected","tracked":True,"active":True,"head_session":"head-a"}},
    }}
    for command,value in responses.items():
        if command in args:
            emit(value)
            return
    if not any(command in args for command in ("worktree-observations","worktree-observation-history")):
        print("unsupported isolated fixture command",file=sys.stderr)
        raise SystemExit(2)
'''
    module.write_text(original.replace("def main():\n", "def main():\n" + dispatch), encoding="utf-8")
    if os.name == "nt":
        runtime = root / "engine"
        venv.EnvBuilder(with_pip=False).create(runtime)
        (runtime / "Lib/site-packages/fixture-owner.pth").write_text(
            str(root / "modules") + "\n", encoding="utf-8",
        )
        interpreter = runtime / "Scripts/python.exe"
    return interpreter, calls
