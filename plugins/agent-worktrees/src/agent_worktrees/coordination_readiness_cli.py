"""Read-only coordination readiness, including configuration acquisition errors."""

import argparse
import json

from configuration_layers import ConfigurationError

from . import config as cfg, output, state_root


def dispatch(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(
        prog="agent-worktrees coordination-readiness",
        description="Report whether resource-claim coordination has a usable durable state root.",
    )
    try:
        parser.parse_args(argv)
    except SystemExit as exc:
        return int(exc.code or 0)
    try:
        readiness = state_root.coordination_readiness(cfg.load_config())
    except ConfigurationError as exc:
        return output._json_error(str(exc), exit_code=3)
    payload = readiness.as_dict()
    payload["capabilities"] = ["lease-acquire-released-only-v1", "lease-return-proof-v1"]
    print(json.dumps(payload, indent=2))
    return 0 if readiness.ready else 3
