---
name: capturing-client-requests
description: >
  Captures and locally interprets an operator-owned Copilot CLI request through
  a temporary localhost-only fake provider, using current model settings.
  Reports instruction/metadata/tool presence and byte accounting without
  logging a shared inference service.
  Trigger phrases include:
  - 'capture the raw prompt'
  - 'inspect the client request'
  - 'what instructions actually get sent'
  - 'analyze a snapshot'
  - 'debug harness prompt composition'
---

# Capturing Client Requests

Use the bundled standard-library Python script to inspect one request emitted
by the operator's own client. This is intentionally **raw local persistence**,
not a counts-only report and not a proxy for other users' traffic.

1. Establish that the operator wants their own request captured. Select the
   harness directory, exact native Copilot executable and a **new output file**
   in an existing private local directory outside repositories and synced folders.
   The file can contain secrets, instructions, messages and tool schemas.
2. Read [usage and boundaries](references/usage.md). Preserve the selected
   harness's plugins/instructions; do not silently replace it with an empty
   fixture or disable custom instructions. Only run trusted harness code.
3. Use an absolute Python 3.11+ interpreter and this skill's returned base.
   The command resolves the operator's current model/effort/context settings
   and uses a 300-second timeout. Do not select a smaller fixed model merely
   because the endpoint is fake: that can prevent a large harness from ever
   issuing its request. Explicit `--model`, `--reasoning-effort` and `--context`
   override individual preferences when the normal launcher uses a profile or
   a session-only selection not represented in persisted settings.

   ```text
   <python> <skill-base>/scripts/capture-client-request.py --copilot-command <native-copilot> --working-directory <harness> --output <private-directory>/request.json
   ```

4. Require exit zero and `status=captured`. The command also writes a private
   `request.analysis.json` companion beside `request.json`. It lists observed
   instructions, skills, agents, MCP/builtin tool-name correlations, discovery
   surfaces and byte accounting. Read its evidence/coverage fields: names-only
   advertisements, source matches and unknown origins are different results.
   Exact memory totals and builtin-prompt bytes remain unknown when the client
   request provides no supported attribution boundary.
   On failure, use the result's error
   and capture status; do not report an empty or partial file as success.
   Inspect the local artifact only to the extent the operator requested.
   Do not automatically ingest its contents into an online agent conversation.
5. Leave retention to the operator. Never attach raw captures to issues,
   PRs, logs or commits; publish only deliberately minimized synthetic evidence.

The script owns an ephemeral `127.0.0.1` listener and one fresh CLI invocation,
returns a terminal fake completion and closes the listener. It cannot attach
to an existing/shared inference endpoint, forward inference, bind a public
interface or run as an always-on logger. No runtime, service registration,
tokenizer, harness adoption or global settings edit is required.

For a previously captured request, use the offline command without launching
another client:

```text
<python> <skill-base>/scripts/analyze-client-request.py --request <private-directory>/request.json --working-directory <harness> --output <private-directory>/request.analysis.json
```

The report contains private paths and metadata identifiers, but no echoed
instruction, description or memory bodies. Keep it local too. See the usage
reference for inventory limits, source correlations and command-catalog options.

This captures the **custom-provider client request**, not additions made by
GitHub's hosted provider. Loopback routing is not a network sandbox for the
harness's hooks or MCP servers. Detailed launch differences, errors and
platform limitations are in the usage reference.
