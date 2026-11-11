---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","deliveryKind":"selector","destination":".github/instructions/customizing-copilot/session-guidance-writers.instructions.md","plugin":"customizing-copilot@copilot-extensions","pluginVersion":"0.2.71-dev1","renderedBytes":1503,"schema":"copilot-extensions.instruction-projection","sourceId":"session-guidance-writers","templateSha256":"e0c5f42329ebdb53000e1fed2b66f447f89ac0419d1847d952f566cf02aa9b0c","version":2} -->

Guidance locator only; no instruction body is delivered here.
Managed candidate: `.github/instructions/agent-worktrees-generated/copilot-extensions/customizing-copilot/session-guidance-writers.local.instructions.md`; reviewed fallback: `.github/copilot/context-fallbacks/customizing-copilot/session-guidance-writers.md`.
Read the named files directly with repository file tools to acquire the source's guidance. These file reads do not require a skill, Python, or the resolver below.
Only if the source path is unclear, invoke `reviewing-customizations`; use its returned base and an absolute Python interpreter. Substitute the three placeholders:
["<python>","<skill-base>/scripts/manage-instruction-projections.py","resolve-source","<repository>",".github/instructions/customizing-copilot/session-guidance-writers.instructions.md","--from-settings","--json"]
Acquire and follow the source's applicable guidance, including its delivery and full-read rules. This locator does not replace those rules.
