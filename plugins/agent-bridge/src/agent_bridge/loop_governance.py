"""Installation-governance rechecks for long-running agent-bridge loops."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .config import _load_installation_context
from .install_paths import install_dir, legacy_install_dir

PLUGIN_ID = "agent-bridge"
_CONTEXT_ENV = "COPILOT_EXTENSIONS_CONTEXT"


def _context_receipt_path(context: dict[str, object]) -> str | None:
    value = context.get("installReceipt")
    if isinstance(value, str) and value.strip():
        return value.strip()
    raw = os.environ.get(_CONTEXT_ENV, "").strip()
    if raw and not raw.startswith("{"):
        return raw
    return None


def _load_governance_module() -> dict[str, Any] | None:
    context = _load_installation_context()
    if context is None:
        return None
    context_path = _context_receipt_path(context)
    marketplace_id = context.get("marketplaceId")
    if not isinstance(marketplace_id, str) or not marketplace_id.strip() or not context_path:
        return {
            "error": {
                "status": "backoff",
                "reason": "governance-unavailable",
                "detail": "installation context omitted marketplace or receipt identity",
            }
        }
    try:
        root = install_dir().expanduser()
        manifest_path = root / "deploy-manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        source = manifest.get("source")
        payload_root_value = source.get("path") if isinstance(source, dict) else None
        if not isinstance(payload_root_value, str) or not payload_root_value.strip():
            raise ValueError("deploy manifest source.path is missing")
        payload_root = Path(payload_root_value).expanduser().resolve(strict=True)
        script = payload_root / "scripts" / "installation-context" / "installation_context.py"
        if not script.is_file():
            raise FileNotFoundError(script)
        module_name = (
            "agent_bridge_installation_context_"
            + hashlib.sha256(os.fsencode(script)).hexdigest()[:16]
        )
        spec = importlib.util.spec_from_file_location(module_name, script)
        if spec is None or spec.loader is None:
            raise ImportError("installation-context module cannot be loaded")
        module = importlib.util.module_from_spec(spec)
        prior = sys.modules.get(module_name)
        sys.modules[module_name] = module
        try:
            spec.loader.exec_module(module)
        except Exception:
            if prior is None:
                sys.modules.pop(module_name, None)
            else:
                sys.modules[module_name] = prior
            raise
        return {
            "module": module,
            "context": context_path,
            "marketplace_id": marketplace_id.strip(),
            "plugin_id": PLUGIN_ID,
            "legacy_root": str(legacy_install_dir()),
        }
    except Exception as exc:
        return {
            "error": {
                "status": "backoff",
                "reason": "governance-unavailable",
                "detail": str(exc),
            }
        }


class LoopGovernance:
    """Stateful wrapper around ``recheck_loop_governance`` for one process."""

    def __init__(self, helper: Any | None = None) -> None:
        self._helper = _load_governance_module() if helper is None else helper
        self._baseline: dict[str, Any] | None = None
        self.state: dict[str, Any] | None = None

    def set_recheck_fn(self, fn: Callable[[str], dict[str, Any]] | None) -> None:
        """Override the installed helper (tests)."""
        self._helper = fn
        self._baseline = None
        self.state = None

    def recheck(self, checkpoint: str) -> dict[str, Any] | None:
        """Return the current governance verdict for ``checkpoint``."""
        helper = self._helper
        if helper is None:
            self.state = None
            return None
        if callable(helper):
            result = helper(checkpoint)
            if "checkpoint" not in result:
                result = dict(result)
                result["checkpoint"] = checkpoint
        elif "error" in helper:
            result = {"checkpoint": checkpoint, **helper["error"]}
        else:
            module = helper["module"]
            result = module.recheck_loop_governance(
                context=helper["context"],
                expected_marketplace_id=helper["marketplace_id"],
                expected_plugin_id=helper["plugin_id"],
                legacy_root=helper["legacy_root"],
                baseline=self._baseline,
                environment=os.environ,
            )
            result["checkpoint"] = checkpoint
        self.state = result
        if result.get("status") == "ready":
            baseline = result.get("baseline")
            self._baseline = baseline if isinstance(baseline, dict) else None
            self.state = None
        return result
