"""Material changes after the last authority read, inside original publication."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from fleet_manager import komodo_repair
from fleet_manager.komodo_profile import KomodoMaterialization
from fleet_manager.node_journal import NodeJournal


def late_material_drift(
    monkeypatch: Any, material: KomodoMaterialization, mode: str,
) -> list[bool]:
    armed: list[bool] = []
    fired: list[bool] = []
    append = NodeJournal._append
    retained = komodo_repair.validate_retained_outcomes

    def after_append(instance: Any, *args: Any, **kwargs: Any) -> Any:
        result = append(*args, **kwargs)
        if args[2].to_dict()["sequence"] == 3:
            armed.append(True)
        return result

    def after_evidence(root: Path, record: dict[str, Any]) -> dict[str, str]:
        result = retained(root, record)
        if armed and not fired:
            fired.append(True)
            if mode == "publication-late-credential-value":
                material.admin_password.write_text("changed-fixture-credential")
            elif mode == "publication-late-credential-inode":
                path = material.admin_password
                previous = path.with_name(path.name + ".fixture-retained")
                path.rename(previous)
                path.touch(mode=0o600)
                path.write_bytes(previous.read_bytes())
            elif mode == "publication-late-data-inode":
                path = material.mongo_data
                previous = path.with_name(path.name + ".fixture-retained")
                path.rename(previous)
                path.mkdir(mode=0o700)
                (path / "retained-fixture").write_bytes((previous / "retained-fixture").read_bytes())
            else:
                raise AssertionError("unexpected material drift control")
        return result

    monkeypatch.setattr(NodeJournal, "_append", after_append)
    monkeypatch.setattr(komodo_repair, "validate_retained_outcomes", after_evidence)
    return fired
