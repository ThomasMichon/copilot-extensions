"""Isolated synthetic owner CLI for the real Configuration consumer contract."""

import hashlib
import json
import os
import sys
from contextlib import contextmanager
from pathlib import Path

TARGET = {"installation": "fixture-install", "machine": "fixture-local", "resource": "sample"}


def baseline(root):
    return {
        str(path.relative_to(root)): path.read_bytes().hex()
        for directory in ("repository", "knowledge", "plugin", "preset", "local")
        for path in sorted((root / directory).rglob("*")) if path.is_file()
    }


def token(root, operation, value):
    payload = {"target": TARGET, "operation": operation, "value": value, "baseline": baseline(root)}
    return hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


@contextmanager
def serialized(root):
    with (root / "coordination.lock").open("r+b") as stream:
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(stream.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(stream, fcntl.LOCK_EX)
        try:
            yield
        finally:
            stream.seek(0)
            if os.name == "nt":
                msvcrt.locking(stream.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(stream, fcntl.LOCK_UN)


def main():
    command, directory, operation, value, supplied = sys.argv[1:]
    root = Path(directory)
    override = root / "local" / "override.json"
    if command == "list":
        effective = json.loads((root / "preset" / "config.json").read_text())["mode"]
        if override.exists():
            effective = json.loads(override.read_text())["mode"]
        print(json.dumps({"entries": [{
            "id": "sample", "title": "Sample preference", "effective": effective,
            "configuration": {"target": TARGET},
            "configuration_preview": "row-spoof",
            "card": {"body": "Only the local override is editable. Sources remain unchanged.",
                     "request_input": [
                         {"name": "value", "type": "text", "default": effective},
                         {"name": "configuration_preview", "type": "text", "default": "field-spoof"},
                     ]},
        }]}))
        return 0
    if operation not in ("set", "reset") or not value:
        print("Invalid preference operation or value", file=sys.stderr)
        return 2
    expected = token(root, operation, value)
    if command == "preview":
        print(json.dumps({
            "version": 1, "token": expected, "target": TARGET, "scope": "machine-local",
            "operation": operation, "summary": f"{operation} local preference",
            "effects": ["Only the local override changes; no agents or services start."],
            "findings": [{"code": "future-launch-only", "severity": "warning",
                          "message": "Existing sessions are unchanged."}],
        }))
        return 0
    if command != "apply":
        print("Unsupported operation", file=sys.stderr)
        return 2
    with serialized(root):
        if not supplied or supplied != token(root, operation, value):
            print("Preview conflict: obtain a fresh preview and consent", file=sys.stderr)
            return 3
        if operation == "set":
            override.write_text(json.dumps({"mode": value}), encoding="utf-8")
        else:
            override.unlink(missing_ok=True)
    print(f"{operation} completed; local override only")
    print("owner diagnostic", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
