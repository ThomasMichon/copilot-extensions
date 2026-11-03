---
name: defining-subagents
description: >
  Access the checkout's canonical sub-agent definition guidance when changing
  this repository without installed plugins.
  Trigger phrases include:
  - 'repository sub-agent definitions'
---

# Repository sub-agent definitions

Read [the canonical skill](../../../plugins/customizing-copilot/skills/defining-subagents/SKILL.md)
in full and follow it for the requested task.

The canonical skill base is `plugins/customizing-copilot/skills/defining-subagents/`,
relative to the repository root. Resolve its references, scripts, assets,
`<skill-base>` and `<skill-dir>` from that directory, not this adapter.
Do not install or activate a plugin merely to use this source.
