#!/usr/bin/env python3
"""Optional provider-v2 adapter, evaluated in the explicitly resolved interpreter."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path
import sys

from agenda import AgendaError, decode
from agenda_publish import _invoke

# The selected interpreter owns both declaration and parser. Never enter main
# or attempt provisioning when either v2 support surface is missing.
PROVIDER = r'''
import json, re, sys

def failure(code, message):
    print(json.dumps({"ok":False,"contract_version":2,
        "error":{"code":code,"message":str(message)[:1024] or code}}, separators=(",",":")))

try:
    from agent_worktrees import agenda_contract
    versions = getattr(agenda_contract, "CONTRACT_VERSIONS", None)
    if not isinstance(versions, (tuple,list)) or 2 not in versions:
        failure("unsupported", "selected interpreter declares no agenda contract v2")
        sys.exit(0)
    from agent_worktrees.worktrees_client import read_health, _control_client
    health = read_health()
    if not isinstance(health, dict):
        raise ValueError("provider health must be an object")
    capabilities = health.get("capabilities")
    if not isinstance(capabilities, list) or "worktree-agendas-v2" not in capabilities:
        failure("unsupported", "actual resident lacks worktree-agendas-v2")
        sys.exit(0)
    authority = health.get("worktrees")
    if not isinstance(authority, dict) or authority.get("status") != "ready":
        failure("unavailable", "actual resident agenda authority is not ready")
        sys.exit(0)
    generation = authority.get("generation")
    if not isinstance(generation, str) or re.fullmatch(r"[0-9a-f]{32}", generation) is None:
        raise ValueError("actual resident generation is invalid")
    from agent_worktrees.__main__ import build_parser
    try:
        parsed = build_parser().parse_args([
            "agenda-publish","--contract-version","2","--json"])
    except SystemExit:
        failure("unsupported", "selected CLI parser does not support contract version 2")
        sys.exit(0)
    if getattr(parsed, "contract_version", None) != 2:
        failure("unsupported", "selected CLI did not admit agenda contract version 2")
        sys.exit(0)
    body = json.load(sys.stdin)
    if body.get("schema") != "efforts.agenda-observation" or body.get("version") != 2:
        raise ValueError("adapter requires an observation-v2 body")
    source = body.get("source")
    if not isinstance(source, dict) or not isinstance(source.get("session"), str) or not source["session"]:
        raise ValueError("publication requires an explicit nonempty canonical head session")
    payload = {"contract_version":2,"project":sys.argv[1],
               "expected_generation":generation,"observation":body}
    result = _control_client()._request("agenda-publish", payload,
                                      max_response_bytes=35*1024,total_timeout=1.0)
    if not isinstance(result, dict) or result.get("contract_version") != 2:
        raise ValueError("provider reply has an invalid agenda contract version")
    if result.get("ok") is False:
        if set(result) != {"ok","contract_version","error"}:
            raise ValueError("provider failure fields are invalid")
        error = result["error"]
        if not isinstance(error, dict) or set(error) != {"code","message"} or any(
                not isinstance(error[k], str) or not error[k] for k in ("code","message")):
            raise ValueError("provider failure omitted its structured reason")
    else:
        expected = {"ok","contract_version","generation","sequence","project","worktree_id",
                    "session_id","availability","observation"}
        if set(result) != expected or result["ok"] is not True:
            raise ValueError("provider success fields are invalid")
        if (result["generation"] != generation or result["project"] != sys.argv[1]
                or result["worktree_id"] != source["worktree"]
                or result["session_id"] != source["session"]):
            raise ValueError("provider reply changed selected source or generation")
        if type(result["sequence"]) is not int or not 0 <= result["sequence"] < 2**63:
            raise ValueError("provider sequence is invalid")
        if result["availability"] != "observed" or json.dumps(
                result["observation"],sort_keys=True,allow_nan=False) != json.dumps(body,sort_keys=True,allow_nan=False):
            raise ValueError("provider did not acknowledge this identical committed observation")
    encoded = json.dumps(result, ensure_ascii=False, separators=(",",":"), allow_nan=False)
    if len(encoded.encode())+1 > 35*1024:
        raise ValueError("provider reply exceeds 35 KiB")
    print(encoded)
except (ImportError, AttributeError, OSError, ValueError, TypeError, RuntimeError) as exc:
    failure("provider-unavailable", exc)
'''


def deliver(provider_python: str, project: str, body: bytes) -> dict:
    interpreter = Path(provider_python)
    if not interpreter.is_absolute():
        raise AgendaError("provider interpreter must be explicitly resolved and absolute")
    if not project.strip() or "\x00" in project:
        raise AgendaError("publisher project must be nonempty text")
    decode(body, "committed observation", limit=32 * 1024)
    # Leave settlement time inside the outer publisher's ten-second deadline.
    status = asyncio.run(_invoke([str(interpreter), "-I", "-B", "-c", PROVIDER, project], body, timeout=7))
    if status["state"] == "delivered":
        return status["reply"]
    return {"ok": False, "contract_version": 2, "error": status["error"]}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--provider-python", required=True)
    parser.add_argument("--project", required=True)
    args = parser.parse_args(argv)
    try:
        reply = deliver(args.provider_python, args.project, sys.stdin.buffer.read(32769))
    except (AgendaError, OSError, asyncio.TimeoutError, TimeoutError) as exc:
        reply = {"ok": False, "contract_version": 2, "error": {
            "code": "provider-unavailable", "message": str(exc) or "Provider timed out."}}
    print(json.dumps(reply, ensure_ascii=False, allow_nan=False, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
