"""Pinned remote argv with an execution-time installation receipt check."""

from __future__ import annotations

import json
import re
import shlex
from pathlib import PurePosixPath


def validate_descriptor(value: dict) -> dict:
    if not isinstance(value, dict) or set(value) != {"schema", "version", "argv", "receipt"}:
        raise ValueError("remote command descriptor has unexpected fields")
    if value["schema"] != "copilot-extensions.remote-command" or type(value["version"]) is not int or value["version"] != 1:
        raise ValueError("unsupported remote command descriptor")
    argv, receipt = value["argv"], value["receipt"]
    if (
        not isinstance(argv, list) or not 1 <= len(argv) <= 16
        or any(not isinstance(arg, str) or not arg or "\0" in arg or len(arg) > 4096 for arg in argv)
        or not PurePosixPath(argv[0]).is_absolute()
    ):
        raise ValueError("remote command requires bounded argv and an absolute executable")
    if (
        not isinstance(receipt, dict) or set(receipt) != {"path", "sha256"}
        or not isinstance(receipt["path"], str) or "\0" in receipt["path"] or len(receipt["path"]) > 4096
        or not PurePosixPath(receipt["path"]).is_absolute()
        or not isinstance(receipt["sha256"], str) or not re.fullmatch(r"[0-9a-f]{64}", receipt["sha256"])
    ):
        raise ValueError("remote command requires an absolute receipt path and SHA-256")
    return value


def render_command(descriptor: dict | None, arguments: list[str]) -> str:
    if descriptor is None:
        return "bash -lc " + shlex.quote(shlex.join(["agent-bridge", *arguments]))
    value = validate_descriptor(descriptor)
    program = (
        "import hashlib,json,os,stat,sys\n"
        "d=json.loads(sys.argv[1]);p=d['receipt']['path']\n"
        "fd=os.open(p,os.O_RDONLY|os.O_NOFOLLOW)\n"
        "with os.fdopen(fd,'rb') as f:\n"
        " s=os.fstat(f.fileno())\n"
        " if not stat.S_ISREG(s.st_mode) or s.st_size>1048576: raise SystemExit('unsafe installation receipt')\n"
        " digest=hashlib.sha256(f.read(1048577)).hexdigest()\n"
        "if digest!=d['receipt']['sha256']: raise SystemExit('remote installation receipt changed')\n"
        "argv=d['argv']+sys.argv[2:]\n"
        "os.execv(argv[0],argv)\n"
    )
    return shlex.join(["python3", "-c", program, json.dumps(value), *arguments])
