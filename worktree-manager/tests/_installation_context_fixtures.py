"""Shared test fixtures for building real, schema-valid installation-context
receipts (namespace.json + install.json), rather than minimal JSON stand-ins.

``validate_context_receipt`` enforces the full schema -- canonical
marketplace-id format, exact canonical path derivation, a matching
namespace.json, a payload/roots shape -- so a bare ``{"pluginId": ...}`` blob
is correctly rejected by the real resolver. These helpers build a receipt
that actually satisfies that contract, using the same
``libs/installation-context/fixtures/source-identities.json`` vectors
``libs/installation-context``'s own governance tests use.
"""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import ModuleType


def source_vector(index: int = 0) -> dict:
    fixtures = (
        Path(__file__).resolve().parents[2]
        / "libs" / "installation-context" / "fixtures" / "source-identities.json"
    )
    return json.loads(fixtures.read_text(encoding="utf-8"))["vectors"][index]


def patch_profile(monkeypatch, apr_module: ModuleType, home: Path) -> None:
    """Anchor both the policy-check and receipt-durable-home resolution at
    ``home`` on every platform. Production code deliberately ignores ``HOME``
    on POSIX (letting the vendored resolver derive the real passwd-database
    account home instead, to avoid trusting a possibly-unset/spoofed
    variable) -- so a test cannot control the profile through environment
    variables alone without also depending on the real test-runner account's
    home directory. Patching the two profile-resolution seams directly keeps
    tests deterministic and platform-portable.
    """
    monkeypatch.setattr(apr_module, "_canonical_os_profile", lambda environment: home)
    monkeypatch.setattr(
        apr_module, "_durable_home",
        lambda ic, environment: home / ".copilot-extensions",
    )



def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def write_slot(slot: Path, *, windows: bool) -> Path:
    python = slot / ("Scripts/python.exe" if windows else "bin/python")
    python.parent.mkdir(parents=True, exist_ok=True)
    python.write_text("", encoding="utf-8")
    (slot / ".install-complete.json").write_text("{}", encoding="utf-8")
    return python


def _environment_record(home: Path) -> dict:
    platform = "windows" if os.name == "nt" else "posix"
    return {
        "platform": platform,
        "homeRealPath": str(home.resolve()),
        "wslDistro": None if platform == "windows" else os.environ.get("WSL_DISTRO_NAME") or None,
    }


def write_activation(
    home: Path, plugin_root: Path, marketplace_id: str, install: Path, *,
    mode: str = "namespaced", state: str = "active",
) -> Path:
    """Write a valid ``installation-activation.json`` pinning this receipt as
    the actual, currently-active install -- required for
    ``resolve_installation_mode`` to report ``actualMode: "namespaced"`` and
    ``reason: "namespaced-active"``. A receipt with no activation resolves
    to ``actualMode: "legacy"`` even though the install.json itself is
    genuine, exactly the gap ``libs/peer-launch``'s governance gate (and now
    ``agent_plugin_runtime``'s) exists to catch.
    """
    activation = plugin_root / "installation-activation.json"
    write_json(activation, {
        "schema": "copilot-extensions.installation-activation",
        "version": 1,
        "marketplaceId": marketplace_id,
        "pluginId": plugin_root.name,
        "mode": mode,
        "state": state,
        "environment": _environment_record(home),
        "context": str(install.resolve()),
        "namespaceGeneration": 1,
        "installGeneration": 1,
        "generation": 1,
        "legacy": {
            "disposition": "absent" if mode == "namespaced" else "restored",
            "probe": {
                "declared": True,
                "result": "absent",
                "checkedAt": "2026-01-01T00:00:00Z",
            },
        },
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    })
    return activation


def namespaced_fixture(
    home: Path, plugin_id: str = "agent-worktrees", *, windows: bool,
    activated: bool = True,
) -> tuple[Path, Path]:
    """Build a valid namespace.json + install.json pair under
    ``<home>/.copilot-extensions/marketplaces/<id>/plugins/<plugin_id>/``,
    plus a marker-selected runtime slot. Writes a matching
    ``installation-activation.json`` too (unless ``activated=False``, for
    tests that specifically want a genuine, un-activated receipt). Returns
    (install.json path, python).
    """
    vector = source_vector(0)
    marketplace_id = str(vector["marketplaceId"])
    normalized = vector["normalized"]
    durable = home / ".copilot-extensions"
    cell = durable / "marketplaces" / marketplace_id
    plugin_root = cell / "plugins" / plugin_id
    payload = home / "payload"
    payload.mkdir(parents=True, exist_ok=True)
    namespace = cell / "namespace.json"
    install = plugin_root / "install.json"
    write_json(namespace, {
        "schema": "copilot-extensions.marketplace-namespace",
        "version": 1,
        "marketplaceId": marketplace_id,
        "source": {
            "kind": normalized["kind"],
            "canonical": normalized["canonical"],
            "ref": normalized["ref"],
            "fingerprint": f"sha256:{vector['sha256']}",
        },
        "locators": [],
        "generation": 1,
        "state": "active",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    })
    write_json(install, {
        "schema": "copilot-extensions.plugin-installation",
        "version": 1,
        "marketplaceId": marketplace_id,
        "pluginId": plugin_id,
        "pluginRoot": str(plugin_root.resolve()),
        "namespaceReceipt": str(namespace.resolve()),
        "payload": {
            "root": str(payload.resolve()),
            "version": "1.0.0",
            "origin": "explicit",
        },
        "roots": {
            "versions": "versions",
            "snapshots": "snapshots",
            "state": "state",
            "run": "run",
            "logs": "logs",
            "cache": "cache",
            "launchers": "launchers",
        },
        "generation": 1,
        "state": "active",
        "createdAt": "2026-01-01T00:00:00Z",
        "updatedAt": "2026-01-01T00:00:00Z",
    })
    slot = plugin_root / "versions" / "1.2.3"
    python = write_slot(slot, windows=windows)
    (plugin_root / "current-version").write_text("1.2.3", encoding="utf-8")
    if activated:
        write_activation(home, plugin_root, marketplace_id, install)
    return install, python
