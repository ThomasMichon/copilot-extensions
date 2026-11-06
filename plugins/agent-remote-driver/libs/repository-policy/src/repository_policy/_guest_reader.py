"""Standalone guest reader sent with the canonical policy archive."""

from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile


def main() -> None:
    with tempfile.TemporaryDirectory(prefix="repository-policy-probe-") as directory:
        archive = Path(directory) / "policy.zip"
        payload = sys.stdin.buffer.read(2 * 1024 * 1024 + 1)
        if len(payload) > 2 * 1024 * 1024:
            raise SystemExit("Repository policy probe exceeds its transport limit")
        archive.write_bytes(payload)
        sys.path.insert(0, str(archive))
        from repository_policy import (
            AgentHostingError,
            RepositoryPolicyError,
            require_agent_hosting,
        )

        try:
            if os.environ.get("COPILOT_EXTENSIONS_CONTEXT"):
                raise RepositoryPolicyError(
                    "Fresh repository policy probe cannot validate an explicit installation context; "
                    "use its selected receiver"
                )
            policy = require_agent_hosting(sys.argv[1])
        except AgentHostingError as error:
            print(json.dumps({"version": 1, "code": error.code, "error": str(error)}))
            raise SystemExit(3)
        print(json.dumps({**policy.to_dict(), "allowed": True}))


if __name__ == "__main__":
    main()
