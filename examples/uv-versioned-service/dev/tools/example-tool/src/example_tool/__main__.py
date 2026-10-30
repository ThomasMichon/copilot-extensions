"""A tool invocation never starts the service."""

import json
import argparse
import sys
from pathlib import Path

from example_message import message

if sys.argv[1:2] == ["install"]:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fail", action="store_true")
    args = parser.parse_args(sys.argv[2:])
    if args.fail:
        raise SystemExit(17)
    print(json.dumps({"installed": True, "service_started": False, "message": message()}))
    raise SystemExit(0)

print(json.dumps({
    "message": message(), "service_started": False,
    "cwd": str(Path.cwd()), "args": sys.argv[1:], "module": str(Path(__file__).resolve()),
}))
