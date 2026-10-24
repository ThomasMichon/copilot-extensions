"""Installation-governance rechecks for long-running agent-bridge loops."""

from __future__ import annotations

import hashlib
import importlib.util
import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .config import _load_installation_context
from .install_paths import install_dir, legacy_install_dir
from .runtime_custody import RuntimeCustodyError, RuntimeInstallation

if TYPE_CHECKING:
    from .db import Database

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


def _durable_home_from_context(context_path: str) -> Path:
    try:
        return Path(context_path).expanduser().resolve(strict=False).parents[4]
    except IndexError as exc:
        raise ValueError("installation context receipt layout is invalid") from exc


def _deployment_payload_root(root: Path) -> Path:
    manifest = json.loads((root / "deploy-manifest.json").read_text(encoding="utf-8"))
    source = manifest.get("source") if isinstance(manifest, dict) else None
    value = source.get("path") if isinstance(source, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError("deploy manifest source.path is missing")
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise ValueError("deploy manifest source.path must be absolute")
    return path.resolve(strict=True)


def _load_governance_module() -> dict[str, Any] | None:
    try:
        raw_context = os.environ.get(_CONTEXT_ENV, "").strip()
        if not raw_context:
            return None
        context = _load_installation_context()
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
        root = install_dir().expanduser()
        payload_root = _deployment_payload_root(root)
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
            "durable_home": str(_durable_home_from_context(context_path)),
            "payload_root": str(payload_root),
            "runtime_root": str(root.resolve(strict=True)),
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
        self._installation: RuntimeInstallation | None = None
        self.state: dict[str, Any] | None = None
        self._lock = threading.RLock()

    def set_recheck_fn(self, fn: Callable[[str], dict[str, Any]] | None) -> None:
        """Override the installed helper (tests)."""
        with self._lock:
            self._helper = fn
            self._baseline = None
            self._installation = None
            self.state = None

    def runtime_installation(self, database: Database) -> RuntimeInstallation:
        """Bind custody observations to this daemon's installation and open DB.

        Resolution is read-only. It neither initializes an epoch nor enrolls a
        producer, authenticates a consumer or establishes SDK/native custody.
        """
        with self._lock:
            helper = self._helper
            if not isinstance(helper, dict) or "error" in helper:
                raise RuntimeCustodyError("Installation governance is unavailable for inbox custody")
            try:
                before = self.recheck("inbox-installation:before")
                if before is None or before.get("status") != "ready":
                    raise RuntimeCustodyError("Inbox installation governance is not current")
                module = helper["module"]
                context = module.validate_context_receipt(
                    helper["context"], helper["durable_home"],
                    expected_marketplace_id=helper["marketplace_id"],
                    expected_plugin_id=helper["plugin_id"],
                    expected_payload_root=helper["payload_root"],
                    environment={},
                )
                namespace = module.validate_namespace_receipt(
                    context["namespaceReceipt"], helper["durable_home"],
                )
                mode = module.probe_legacy_entrypoint(
                    context=helper["context"], durable_home=helper["durable_home"],
                    expected_marketplace_id=helper["marketplace_id"],
                    expected_plugin_id=helper["plugin_id"],
                    legacy_root=helper["legacy_root"], environment=os.environ,
                )
                if (
                    mode["status"] != "ready"
                    or (mode["actualMode"] == "legacy" and not mode["allowMutation"])
                    or context["state"] != "active"
                    or namespace["receipt"]["state"] != "active"
                    or mode["actualMode"] != before["actualMode"]
                    or (mode["context"] is not None
                        and mode["context"] != context["installReceipt"])
                    or context["namespaceGeneration"] != before["namespaceGeneration"]
                    or context["generation"] != before["installGeneration"]
                    or not isinstance(mode["runtimeRoot"], str)
                    or Path(mode["runtimeRoot"]).resolve(strict=True)
                    != Path(helper["runtime_root"]).resolve(strict=True)
                    or install_dir().resolve(strict=True)
                    != Path(helper["runtime_root"]).resolve(strict=True)
                    or _deployment_payload_root(Path(helper["runtime_root"]))
                    != Path(helper["payload_root"])
                ):
                    raise RuntimeCustodyError("Inbox installation does not own this daemon runtime")
                installation = RuntimeInstallation(
                    identity=context["installReceipt"],
                    source_fingerprint=context["sourceFingerprint"],
                    runtime_root=helper["runtime_root"],
                    database=str(database.db_path.absolute()),
                )
                after = self.recheck("inbox-installation:after")
                if (
                    after is None or after.get("status") != "ready"
                    or _deployment_payload_root(Path(helper["runtime_root"]))
                    != Path(helper["payload_root"])
                ):
                    raise RuntimeCustodyError("Inbox installation changed during resolution")
                if self._installation is not None and self._installation != installation:
                    raise RuntimeCustodyError("Inbox installation or database binding changed")
                self._installation = installation
                return installation
            except (OSError, ValueError) as exc:
                if isinstance(exc, RuntimeCustodyError):
                    raise
                raise RuntimeCustodyError(f"Inbox installation resolution failed: {exc}") from exc

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
