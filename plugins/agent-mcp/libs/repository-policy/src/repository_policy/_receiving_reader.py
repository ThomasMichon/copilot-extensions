"""Isolated preflight entry for the explicitly selected Bridge policy receiver."""

from __future__ import annotations

import json
import base64
from pathlib import Path
import sys
import tempfile


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="bridge-receiving-policy-") as directory:
        payload = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise SystemExit("Bridge receiving policy exceeds its transport limit")
        if sys.argv[4] == "base64":
            payload = base64.b64decode(payload, validate=True)
        elif sys.argv[4] != "zip":
            raise SystemExit("Unsupported receiving policy archive encoding")
        archive = Path(directory) / "policy.zip"
        archive.write_bytes(payload)
        sys.path.insert(0, str(archive))
        from repository_policy import AgentHostingError
        from repository_policy.receiving import require_receiving_policy

        try:
            policy = require_receiving_policy(sys.argv[1], sys.argv[2], operation=sys.argv[3])
        except (AgentHostingError, ValueError) as error:
            print(json.dumps({
                "version": 1, "code": getattr(error, "code", "repository-agent-exposure-invalid"),
                "error": str(error),
            }))
            raise SystemExit(3)
        print(json.dumps({**policy.to_dict(), "allowed": True}))


if __name__ == "__main__":
    main()
