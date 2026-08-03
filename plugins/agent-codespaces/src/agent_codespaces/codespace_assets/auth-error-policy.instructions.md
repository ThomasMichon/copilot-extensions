---
applyTo: '**'
---
# CodeSpace Authentication Error Policy

When you are running as a dispatched agent inside a GitHub CodeSpace, any git,
Azure DevOps, or credential-related authentication error is a hard stop.

Mandatory behavior:

1. **Stop immediately.** Do not retry the failing operation, diagnose the
   credential stack, or try an alternate authentication path.
2. **Report and wait.** Report the exact error text to the orchestrator and wait
   for the host/orchestrator to repair authentication. Treat the work as a
   resumable step.
3. **Do not bypass auth hooks.** Never run `git push --no-verify` to work around
   authentication failure. The only exception is an explicitly directed
   emergency Abandon-CodeSpace flow.
4. **Do not self-repair credentials in the CodeSpace.** The credential relay is
   host-owned. Do not run `az login`, device-code login flows, `gh auth login`,
   or any other interactive sign-in on the CodeSpace. Interactive login can
   block the stdio/ACP channel. If the relay is unreachable, rely only on the
   existing short-TTL CodeSpace cache for transient drops; for a longer outage,
   report the error and defer to the orchestrator.
