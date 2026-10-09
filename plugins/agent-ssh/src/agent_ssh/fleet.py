"""Read-only static driver over the same source records as the SSH profile emitter."""

from __future__ import annotations

import hashlib
from pathlib import Path

import yaml
from fleet_contracts import ContractError, DriverSnapshot, DriverTarget
from fleet_contracts.records import MAX_TARGETS

from . import ssh_profile

MAX_SOURCE_BYTES = 1024 * 1024


def _read(path: Path) -> bytes:
    with path.open("rb") as stream:
        content = stream.read(MAX_SOURCE_BYTES + 1)
    if len(content) > MAX_SOURCE_BYTES:
        raise ContractError("static driver source exceeds its byte limit")
    return content


def describe(
    registry: Path,
    module: Path,
    *,
    provider_instance: str,
    selected: list[str],
) -> DriverSnapshot:
    if not selected or len(selected) > MAX_TARGETS:
        raise ContractError("select between 1 and 256 explicit SSH targets")
    if any(not isinstance(name, str) or not ssh_profile.is_valid_alias(name) for name in selected):
        raise ContractError("selection must contain exact SSH target names")
    folded = [name.casefold() for name in selected]
    if len(set(folded)) != len(folded):
        raise ContractError("selection contains duplicate SSH target identities")
    registry_bytes, module_bytes = _read(registry), _read(module)
    cfg = yaml.safe_load(registry_bytes.decode("utf-8"))
    recipe = yaml.safe_load(module_bytes.decode("utf-8"))
    try:
        ssh_profile.validate_profile_inputs(cfg, recipe, require_transport_match=True)
    except (ValueError, TypeError, KeyError) as exc:
        raise ContractError("static driver registry/module failed profile validation") from exc
    records = {record["name"].casefold(): record["name"] for record in cfg["machines"]}
    missing = set(folded) - records.keys()
    if missing:
        raise ContractError("selected SSH target is absent from the authoritative registry")
    revision = hashlib.sha256()
    for content in (registry_bytes, module_bytes):
        revision.update(len(content).to_bytes(8, "big"))
        revision.update(content)
    return DriverSnapshot(
        driver="static-ssh",
        provider_instance=provider_instance,
        source_revision=f"sha256:{revision.hexdigest()}",
        targets=tuple(
            DriverTarget(target_id=records[name], state="configured", capabilities=("ssh",))
            for name in folded
        ),
    )
