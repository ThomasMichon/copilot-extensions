"""Transport two consecutive read-only SDK queries without reusing a decision."""
from __future__ import annotations

import argparse
from contextlib import redirect_stdout
import importlib.util
import io
import json
from pathlib import Path
import sys


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("context", "durable-home", "payload-root", "plugin-id", "legacy-root"):
        parser.add_argument("--" + name, required=True)
    arguments = parser.parse_args()
    helper = Path(__file__).with_name("installation-context") / "installation_context.py"
    spec = importlib.util.spec_from_file_location("_worktrees_context_reader", helper)
    if spec is None or spec.loader is None:
        raise ImportError(f"Installation-context helper unavailable: {helper}")
    sdk = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = sdk
    spec.loader.exec_module(sdk)
    common = [
        "--context", arguments.context, "--durable-home", arguments.durable_home,
    ]
    queries = (
        ("validate", [
            *common, "--expected-plugin-id", arguments.plugin_id,
            "--expected-payload-root", arguments.payload_root,
        ]),
        ("status", [
            *common, "--plugin-id", arguments.plugin_id,
            "--payload-root", arguments.payload_root, "--legacy-root", arguments.legacy_root,
        ]),
    )
    responses = {}
    for action, values in queries:
        output = io.StringIO()
        with redirect_stdout(output):
            code = sdk.main([action, *values])
        if type(code) is not int:
            raise RuntimeError("Installation-context helper returned an invalid exit status.")
        if code != 0:
            json.dump(
                {"failedAction": action, "output": output.getvalue()},
                sys.stdout, ensure_ascii=False,
            )
            sys.stdout.write("\n")
            return code
        responses[action] = output.getvalue()
    json.dump(responses, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
