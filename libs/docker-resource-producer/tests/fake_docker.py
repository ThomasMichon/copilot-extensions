"""Fixture-only Docker process; no network or external command execution."""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path


def main() -> int:
    state_path = Path(os.environ["FAKE_DOCKER_STATE"])
    state = json.loads(state_path.read_text(encoding="utf-8"))
    args = sys.argv[1:]
    tls_files = {}
    defaults = Path(os.environ.get("DOCKER_CERT_PATH") or os.environ.get("DOCKER_CONFIG") or Path.home() / ".docker")
    for option, name in (("--tlscacert", "ca.pem"), ("--tlscert", "cert.pem"), ("--tlskey", "key.pem")):
        if option in args:
            tls_files[name] = args[args.index(option) + 1]
        else:
            default = defaults / name
            tls_files[name] = str(default) if name == "ca.pem" or default.exists() else ""
    with state_path.with_suffix(".log").open("a", encoding="utf-8") as log:
        log.write(json.dumps({
            "args": args,
            "tls_files": tls_files,
            "routing": {name: os.environ.get(name) for name in (
                "DOCKER_CONTEXT", "DOCKER_HOST", "DOCKER_TLS", "DOCKER_TLS_VERIFY", "DOCKER_CERT_PATH",
            )},
        }) + "\n")
    while args and args[0].startswith("--"):
        flag = args.pop(0)
        if "=" not in flag:
            args.pop(0)
    operation = " ".join(args[:2]) if args[0] != "info" else "info"
    if state.get("fail") == operation:
        print("permission denied", file=sys.stderr)
        return 7
    if state.get("malformed") == operation:
        print("not-json")
        return 0
    if operation == "info":
        print(json.dumps(state["daemon_id"]))
        return 0
    if operation == "context show":
        print(state["context"]["Name"])
        return 0
    if operation == "context inspect":
        context = state["context"]
        if args[-1] == "default" and context["Name"] != "default":
            enabled = bool(os.environ.get("DOCKER_TLS") or os.environ.get("DOCKER_TLS_VERIFY"))
            context = state.get("default_context", {
                "Name": "default",
                "Endpoints": {"docker": {"Host": os.environ["DOCKER_HOST"],
                                        "SkipTLSVerify": enabled and not bool(os.environ.get("DOCKER_TLS_VERIFY"))}},
                "TLSMaterial": {"docker": ["ca.pem"] if enabled else []},
                "Storage": {"TLSPath": "<IN MEMORY>"},
            })
        print(json.dumps([context]))
        return 0
    kind, action = args[:2]
    resources = state[kind]
    if action == "ls":
        resources = state.get("inventory_on_other_daemon", state)[kind]
        selected = list(resources)
        if "--filter" in args:
            expected = args[args.index("--filter") + 1].split("=", 2)[2]
            selected = [
                name for name in selected if (
                    resources[name].get("Config", resources[name]).get("Labels", {})
                    .get("copilot-extensions.disposable.reservation") == expected
                )
            ]
        for name in selected:
            print(json.dumps(name))
        if state.get("change_daemon_after") == operation:
            state["daemon_id"] = "replacement-daemon"
            state_path.write_text(json.dumps(state), encoding="utf-8")
        return 0
    name = args[-1]
    if action == "inspect":
        if name not in resources:
            print("No such resource", file=sys.stderr)
            return 1
        print(json.dumps([resources[name]]))
        return 0
    if action == "rm":
        if state.get("retain"):
            return 0
        del resources[name]
        state_path.write_text(json.dumps(state), encoding="utf-8")
        print(name)
        return 0
    raise AssertionError(f"Unexpected fake Docker operation: {operation}")


if __name__ == "__main__":
    raise SystemExit(main())
