"""Disposable legacy command-contract fixture; never a deployment entrypoint."""

import argparse
import json
from pathlib import Path
import sys


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--state", required=True, type=Path)
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export-client")
    export.add_argument("--machine", required=True)
    for name in ("export-directory", "status", "import-peers"):
        commands.add_parser(name)
    args = parser.parse_args()
    state = json.loads(args.state.read_text(encoding="ascii"))
    if args.command == "export-client":
        if args.machine != state["descriptor"]["machine"]:
            parser.error("machine is not configured")
        result = state["descriptor"]
    elif args.command == "export-directory":
        result = {"peers": state["peers"]}
    else:
        if args.command == "import-peers":
            incoming = json.load(sys.stdin)
            assert set(incoming) == {"peers"} and len(incoming["peers"]) == 1
            peer = incoming["peers"][0]
            prior = next((p for p in state["peers"] if p["machine"] == peer["machine"]), None)
            assert prior is None or prior == peer, "implicit rotation refused"
            if prior is None:
                state["peers"].append(peer)
                args.state.write_text(json.dumps(state), encoding="ascii")
        result = {
            "installed": True, "enabled": state["enabled"],
            "enrollment_enabled": bool(state["enabled"] and state["peers"]),
        }
    print(json.dumps(result))


if __name__ == "__main__":
    main()
