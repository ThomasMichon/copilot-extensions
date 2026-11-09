---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","deliveryKind":"selector","destination":".github/instructions/customizing-copilot/session-guidance-writers.instructions.md","plugin":"customizing-copilot@copilot-extensions","pluginVersion":"0.1.0-dev97","renderedBytes":1503,"schema":"copilot-extensions.instruction-projection","sourceId":"session-guidance-writers","templateSha256":"bb31036d7da5f914a3cc3c55dc32d273b947737b321d53f19f26b24d1b8bda83","version":2} -->

Guidance locator only; no instruction body is delivered here.
Managed candidate: `.github/instructions/agent-worktrees-generated/copilot-extensions/customizing-copilot/session-guidance-writers.local.instructions.md`; reviewed fallback: `.github/copilot/context-fallbacks/customizing-copilot/session-guidance-writers.md`.
Read the named files directly with repository file tools to acquire the source's guidance. These file reads do not require a skill, Python, or the resolver below.
Only if the source path is unclear, invoke `reviewing-customizations`; use its returned base and an absolute Python interpreter. Substitute the three placeholders:
["<python>","<skill-base>/scripts/manage-instruction-projections.py","resolve-source","<repository>",".github/instructions/customizing-copilot/session-guidance-writers.instructions.md","--from-settings","--json"]
Acquire and follow the source's applicable guidance, including its delivery and full-read rules. This locator does not replace those rules.
