---
name: capturing-client-requests
description: Captures the exact raw request an operator's own Copilot CLI sends to a temporary localhost-only fake provider for debugging harness instructions and tools. Use for "capture the raw prompt", "inspect the client request", "what instructions actually get sent", or "debug harness prompt composition". Never logs an existing or shared inference service.
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
3. Use an absolute Python 3.11+ interpreter and this skill's returned base:

   ```text
   <python> <skill-base>/scripts/capture-client-request.py --copilot-command <native-copilot> --working-directory <harness> --output <private-directory>/request.json
   ```

4. Require exit zero and `status=captured`. On failure, use the result's error
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

This captures the **custom-provider client request**, not additions made by
GitHub's hosted provider. Loopback routing is not a network sandbox for the
harness's hooks or MCP servers. Detailed launch differences, errors and
platform limitations are in the usage reference.
