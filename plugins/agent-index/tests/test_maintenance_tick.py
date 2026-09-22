from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT = (
    Path(__file__).resolve().parents[1]
    / "scripts"
    / "maintenance_tick.py"
)


def _load_module():
    spec = importlib.util.spec_from_file_location("agent_index_maintenance_tick", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_plan_maintenance_actions_only_heals_unhealthy_components():
    module = _load_module()

    healthy = module.plan_maintenance_actions(
        {"role": "host", "state": "ready", "running": True},
        {"healthy": True},
    )
    assert healthy == {"recover_service": False, "start_engine": False}

    service_down = module.plan_maintenance_actions(
        {"role": "host", "state": "unreachable", "running": False},
        {"healthy": True},
    )
    assert service_down == {"recover_service": True, "start_engine": False}

    engine_down = module.plan_maintenance_actions(
        {"role": "host", "state": "ready", "running": True},
        {"healthy": False},
    )
    assert engine_down == {"recover_service": False, "start_engine": True}
