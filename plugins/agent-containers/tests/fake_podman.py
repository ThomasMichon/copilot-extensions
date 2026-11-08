"""Harmless Python-as-engine stand-in, run only against a caller-owned fixture."""

import json
import os
import sys
from pathlib import Path


root = Path(os.environ["PODMAN_TEST_ROOT"])
path = root / "state.json"
state = json.loads(path.read_text()) if path.exists() else {"members": {}, "calls": []}
verb, args = Path(sys.argv[0]).name, sys.argv[1:]
state["calls"].append([verb, *args])
result, code = "", 0
member_id = "b" * 64
image_id = "sha256:" + "a" * 64
members = state["members"]
if verb == "pull":
    result = image_id
elif verb == "image":
    result = json.dumps([{"Id": image_id}])
elif verb == "run":
    labels = {}
    for index, arg in enumerate(args):
        if arg == "--label":
            key, value = args[index + 1].split("=", 1)
            labels[key] = value
    members[member_id] = {
        "Id": member_id, "Name": args[args.index("--name") + 1], "Image": image_id,
        "State": {"Status": "running"}, "Config": {"Labels": labels},
    }
    result = member_id
elif verb == "ps":
    result = "\n".join(members)
elif verb == "container":
    targets = args[1:]
    selected = [
        row for row in members.values() if row["Id"] in targets or row["Name"] in targets
    ]
    if args[0] == "exists":
        code = 0 if selected else 1
    elif selected:
        result = json.dumps(selected)
    else:
        code = 125
elif verb in ("stop", "start", "unpause"):
    member = members[args[-1]]
    if verb == "start" and member["State"]["Status"] == "paused":
        code = 125
    else:
        member["State"]["Status"] = "exited" if verb == "stop" else "running"
elif verb == "rm":
    del members[args[-1]]
elif verb == "exec":
    index = args.index(member_id)
    command = args[index + 1:]
    if command == ["fixture-echo"]:
        result = sys.stdin.read()
        code = 7
elif verb != "cp":
    raise SystemExit("unexpected fake engine command")
path.write_text(json.dumps(state), encoding="utf-8")
if result:
    print(result)
raise SystemExit(code)
