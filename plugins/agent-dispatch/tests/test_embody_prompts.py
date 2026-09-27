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
    interactive_worker_prompt,
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


def test_interactive_worker_prompt_requires_a_tool_call_before_pausing():
    # An operator watching a live (mux, non-ACP) pane may not be attached at
    # the moment the agent needs input, so the block must be durably
    # recorded via a tool call before the agent pauses in the pane -- never
    # a bare in-pane question left as the only signal.
    prompt = interactive_worker_prompt("t1")
    assert "t1" in prompt
    assert "card set t1" in prompt
    assert "--request-input" in prompt
    assert "progress t1" in prompt and "--blocker" in prompt
    assert "Only THEN pause" in prompt
