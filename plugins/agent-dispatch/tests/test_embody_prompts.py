"""Import guard for the split-out embody prompt builders.

The functions themselves are already thoroughly exercised through the
``agent_dispatch.embody`` facade (``test_embody.py``, ``test_fleet.py``) --
this file only guards that ``agent_dispatch.embody_prompts`` remains directly
importable with its own stable public names, independent of the facade.
"""

from __future__ import annotations

from agent_dispatch.embody_prompts import (
    autopilot_worker_prompt,
    fleet_autopilot_worker_prompt,
)


def test_autopilot_worker_prompt_is_directly_importable():
    prompt = autopilot_worker_prompt("t1", worker_id="w1")
    assert "t1" in prompt
    assert "w1" in prompt


def test_fleet_autopilot_worker_prompt_is_directly_importable():
    prompt = fleet_autopilot_worker_prompt(
        "t1", origin="origin-host", owner="owner-1", worker_id="w1"
    )
    assert "t1" in prompt
    assert "origin-host" in prompt
    assert "owner-1" in prompt
