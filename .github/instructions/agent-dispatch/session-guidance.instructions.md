---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","customizationKind":"instructions","destination":".github/instructions/agent-dispatch/session-guidance.instructions.md","plugin":"agent-dispatch@copilot-extensions","pluginVersion":"0.1.2-dev84","renderedBytes":984,"schema":"copilot-extensions.instruction-projection","sourceId":"session-guidance","template":"instructions/session-guidance.instructions.md","templateBytes":444,"templateSha256":"6335aaf6994816c3bcab1ddc48a16208106e491fc33022317e377e9f40dd6696","version":1} -->

At the start of this session, resolve the current Copilot session-state folder
using the session identifier exposed as `COPILOT_AGENT_SESSION_ID`, or an
equivalent available mechanism. Read
`instructions/agent-dispatch/session-guidance.instructions.md` inside that
folder if it exists, and treat its contents as authoritative for this session.
If the file does not exist, proceed without it; its absence is not an error.
