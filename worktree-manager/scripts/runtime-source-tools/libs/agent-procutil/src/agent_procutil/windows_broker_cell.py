"""Qualify an installed cell action through its retained canonical cell API."""

from __future__ import annotations

import importlib.util
import os
import stat
import sys
from pathlib import Path


def is_cell(root: Path) -> bool:
    return root.parent.name == "plugins" and root.parent.parent.parent.name == "marketplaces"


def validate(root: Path, slot: Path, *, require_active: bool = False) -> dict | None:
    if not is_cell(root):
        if os.path.lexists(slot / ".runtime-slot-completion.json"):
            raise ValueError("cell completion is outside its owning installation")
        return None
    if (
        root.resolve() != root or slot.resolve() != slot or slot.parent != root / "versions"
        or not root.is_absolute()
    ):
        raise ValueError("cell broker requires its exact installation and runtime")
    directory = slot / ".runtime-bootstrap" / "installation-context"
    source = directory / "installation_context.py"
    for path in (source, *source.parents):
        info = path.lstat()
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("cell broker helpers may not be indirect")
    if not source.is_file():
        raise ValueError("cell broker requires retained installation helpers")
    for fragment in directory.glob("_installation_context_*.py"):
        info = fragment.lstat()
        if not stat.S_ISREG(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            raise ValueError("cell broker fragments must be ordinary installed files")
    # This is installed code at a fixed slot-local location, not a metadata path.
    spec = importlib.util.spec_from_file_location("_broker_cell_context", source)
    if spec is None or spec.loader is None:
        raise ValueError("cell broker installation helpers are unavailable")
    api = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = api
    before = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        exec(compile(source.read_bytes(), str(source), "exec"), api.__dict__)
    finally:
        sys.dont_write_bytecode = before
    durable = root.parents[3]
    context = api.validate_context_receipt(
        root / "install.json", durable,
        expected_marketplace_id=root.parent.parent.name, expected_plugin_id=root.name,
        expected_cell_root=root.parent.parent, environment={},
    )
    if Path(context["versionsRoot"]) != root / "versions":
        raise ValueError("cell broker requires its canonical versions root")
    completion = api.read_json(slot / api.RUNTIME_SLOT_COMPLETION_FILE)
    snapshot_id = completion["snapshot"]["id"]
    api._assert_snapshot_id(snapshot_id)
    provenance = api.read_json(
        root / "snapshots" / snapshot_id / api.SNAPSHOT_PROVENANCE_FILE,
    )
    payload = provenance["payload"]
    result = api.validate_runtime_slot_completion(
        context=root / "install.json", durable_home=durable,
        expected_marketplace_id=context["marketplaceId"], expected_plugin_id=context["pluginId"],
        expected_payload_root=payload["root"], expected_payload_version=payload["version"],
        snapshot_id=snapshot_id, runtime_version=slot.name, environment={},
    )
    if Path(result["slotRoot"]) != slot:
        raise ValueError("cell completion names another broker action")
    namespace = api.validate_namespace_receipt(context["namespaceReceipt"], durable)
    if require_active and (
        context["state"] != "active" or namespace["receipt"]["state"] != "active"
    ):
        raise ValueError("cell broker requires active installation receipts")
    return {"context": context, "completion": result}
