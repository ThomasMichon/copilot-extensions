"""Installation-governance rechecks for the resident status monitor."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import platform
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import config as cfg
from . import registry_paths

PLUGIN_ID = "agent-worktrees"
_CONTEXT_ENV = "COPILOT_EXTENSIONS_CONTEXT"


def _legacy_root() -> Path:
    override = os.environ.get("AGENT_HOME", "").strip()
    if override:
        return Path(override).expanduser() / ".agent-worktrees"
    if platform.system() == "Windows":
        home = Path(os.environ.get("USERPROFILE") or Path.home())
    else:
        home = Path.home()
    return home / ".agent-worktrees"


def _context_receipt_path(context: dict[str, object]) -> str | None:
    value = context.get("installReceipt")
    if isinstance(value, str) and value.strip():
        return value.strip()
    raw = os.environ.get(_CONTEXT_ENV, "").strip()
    if raw and not raw.startswith("{"):
        return raw
    return None


def _durable_home_from_context(context_path: str) -> Path:
    try:
        return Path(context_path).expanduser().resolve(strict=False).parents[4]
    except IndexError as exc:
        raise ValueError("installation context receipt layout is invalid") from exc


def _load_governance_module() -> dict[str, Any] | None:
    try:
        raw_context = os.environ.get(_CONTEXT_ENV, "").strip()
        if not raw_context:
            return None
        context = registry_paths.installation_context()
        if context is None:
            raise ValueError("installation context could not be resolved")
        context_path = _context_receipt_path(context)
        marketplace_id = context.get("marketplaceId")
        if (
            not isinstance(marketplace_id, str)
            or not marketplace_id.strip()
            or not context_path
        ):
            raise ValueError("installation context omitted marketplace or receipt identity")
        root = cfg.install_dir().expanduser()
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
            "agent_worktrees_installation_context_"
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
            "legacy_root": str(_legacy_root()),
            "durable_home": str(_durable_home_from_context(context_path)),
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
        self._lock = threading.Lock()

    def set_recheck_fn(self, fn: Callable[[str], dict[str, Any]] | None) -> None:
        """Override the installed helper (tests)."""
        with self._lock:
            self._helper = fn
            self._baseline = None
            self.state = None

    def recheck(self, checkpoint: str) -> dict[str, Any] | None:
        """Return the current governance verdict for ``checkpoint``."""
        with self._lock:
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
                    durable_home=helper["durable_home"],
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
