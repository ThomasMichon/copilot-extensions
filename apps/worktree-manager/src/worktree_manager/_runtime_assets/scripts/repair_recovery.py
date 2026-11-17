"""Recovery-only source adapter; no installed application or construction."""

from __future__ import annotations

import importlib
import json
import sys
import tomllib
from pathlib import Path

import installer_engine as engine


def recover(payload: Path, *, as_json: bool) -> int:
    metadata = tomllib.loads((payload / "pyproject.toml").read_text(encoding="utf-8"))
    declared = metadata["tool"]["uv"]["sources"]["agent-update-driver"]["path"]
    if not isinstance(declared, str) or not declared:
        raise ValueError("Manager recovery requires its declared local update driver")
    driver = payload / declared
    packages = engine._package_roots(driver)
    modules = {
        "agent-update-driver": "update_driver",
        "agent-procutil": "agent_procutil",
        "agent-single-instance-lease": "single_instance_lease",
        "agent-zdd": "zdd",
    }
    sources: dict[str, Path] = {}
    for package in packages:
        project = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
        name = project["project"]["name"]
        if name not in modules:
            raise ValueError(f"Unexpected repair-recovery dependency: {name}")
        sources[modules[name]] = package / "src"
    if sources.keys() != set(modules.values()) or sources["update_driver"].parent != driver.resolve():
        raise ValueError("Manager recovery requires the complete local driver closure")
    sys.path[:0] = [str(source) for source in sources.values()]
    for name, source in sources.items():
        module = importlib.import_module(name)
        origin = module.__file__
        if not isinstance(origin, str) or not Path(origin).resolve().is_relative_to(source):
            raise RuntimeError(f"Repair recovery imported a different {name} source")
    import update_driver

    try:
        released = update_driver.recover_for_repair(authorized=True)
    except update_driver.DriverError as error:
        print(f"Repair recovery refused: {error}", file=sys.stderr)
        return 1
    result = {
        "schema": "copilot-extensions.repair-recovery", "version": 1,
        "cleanup_confirmed": False,
        "released": [{
            "request_id": item.id, "state": item.state,
            "error": item.error, "exit_code": item.exit_code,
            "recovery_authority": item.recovery_authority,
            "settlement_kind": item.settlement_kind,
        } for item in released],
    }
    if as_json:
        print(json.dumps(result))
    else:
        print(f"Operator-authorized repair release: {len(released)} request(s).")
        print("Cleanup remains unconfirmed. No installation or queued work was executed.")
    return 0
