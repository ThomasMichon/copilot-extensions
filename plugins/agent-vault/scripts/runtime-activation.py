"""Read Vault activation from the payload, without requiring its runtime."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import sys
import traceback


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cwd", required=True)
    parser.add_argument("--runtime-root")
    parser.add_argument("--context")
    parser.add_argument("--invocation-cwd")
    parser.add_argument("--retain-native-selector", action="store_true")
    args = parser.parse_args()
    payload = Path(__file__).resolve().parents[1]
    sys.path[:0] = [
        str(payload / "src"),
        str(payload / "scripts" / "configuration-layers" / "src"),
    ]
    reason = None
    missing_context_errors = ()
    try:
        from configuration_context import RepositoryContextRequired

        missing_context_errors = (RepositoryContextRequired,)
        from agent_vault.config import _global_config_path, home_dir
        from agent_vault.configuration import runtime_enabled

        context = args.context if args.context is not None else os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
        if args.context is not None:
            if context:
                os.environ["COPILOT_EXTENSIONS_CONTEXT"] = context
            else:
                os.environ.pop("COPILOT_EXTENSIONS_CONTEXT", None)
        runtime = args.runtime_root or os.environ.get("AGENT_VAULT_HOME") or (
            str(Path(context).parent) if context else str(home_dir())
        )
        os.environ["AGENT_RT_ROOT"] = runtime
        os.environ["AGENT_VAULT_HOME"] = runtime
        selected = _global_config_path(invocation_directory=args.invocation_cwd or args.cwd)
        os.environ["AGENT_VAULT_CONFIG"] = str(selected)
        enabled = runtime_enabled(args.cwd)
        state = "configured" if enabled else "configuration-empty"
        detail = (
            "Agent Vault runtime participation is explicitly configured."
            if enabled else
            'Agent Vault is inactive. Set "enabled": true in .agent-vault.json '
            "in the repository or its qualified bound knowledge repository, "
            "or in the native machine configuration. No runtime, service or "
            "KeePassXC installation is required while inactive."
        )
        code = 0 if enabled else 2
    except missing_context_errors:
        state = "failed"
        reason = "repository-context-missing"
        detail = (
            "Agent Vault repository context is missing. Run from an adopted "
            "repository or use '<repo> vault unlock' from any directory. "
            "No installation or unlock was attempted."
        )
        code = 3
    except (ImportError, OSError, RuntimeError, TypeError, ValueError) as error:
        state = "failed"
        frames = ", ".join(
            f"{frame.name}:{frame.lineno}"
            for frame in traceback.extract_tb(error.__traceback__, limit=-4)
        )
        detail = (
            "Agent Vault activation configuration could not be resolved. "
            "Correct the owning configuration, binding or payload; this is "
            f"not an unconfigured result. Reader failure: {type(error).__name__} ({frames})."
        )
        code = 3
    result = {
        "schema": "copilot-extensions.module-readiness",
        "version": 1, "module": "agent-vault/runtime",
        "state": state, "detail": detail,
    }
    if reason:
        result["reason"] = reason
    if args.retain_native_selector and state == "configured":
        result["native_configuration_file"] = str(selected)
    print(json.dumps(result))
    return code


if __name__ == "__main__":
    raise SystemExit(main())
