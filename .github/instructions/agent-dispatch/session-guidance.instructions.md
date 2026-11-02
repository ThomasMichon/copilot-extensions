---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","deliveryKind":"selector","destination":".github/instructions/agent-dispatch/session-guidance.instructions.md","plugin":"agent-dispatch@copilot-extensions","pluginVersion":"0.18.204-dev1","renderedBytes":1440,"schema":"copilot-extensions.instruction-projection","sourceId":"session-guidance","templateSha256":"8969f4b96dfa1febf513b11827a4e94d6f0db32ea775a8c7b3610474b4668dd4","version":2} -->

Guidance locator only; no instruction body is delivered here.
Managed candidate: `.github/instructions/agent-worktrees-generated/copilot-extensions/agent-dispatch/session-guidance.local.instructions.md`; reviewed fallback: `.github/copilot/context-fallbacks/agent-dispatch/session-guidance.md`.
Read the named files directly with repository file tools to acquire the source's guidance. These file reads do not require a skill, Python, or the resolver below.
Only if the source path is unclear, invoke `reviewing-customizations`; use its returned base and an absolute Python interpreter. Substitute the three placeholders:
["<python>","<skill-base>/scripts/manage-instruction-projections.py","resolve-source","<repository>",".github/instructions/agent-dispatch/session-guidance.instructions.md","--from-settings","--json"]
Acquire and follow the source's applicable guidance, including its delivery and full-read rules. This locator does not replace those rules.
