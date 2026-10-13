---
name: reading-session-quota
description: |
  Reads or inspects the requesting Copilot SDK session's cached quota without changing accounts or worker state.
  Trigger phrases include:
  - 'session quota'
  - 'current allowance'
  - 'next reset'
  - 'account-attributed quota capture'
---

# Reading Session Quota

1. Identify the requesting session and the host's already-held SDK session.
   Never create a fresh client, resume a different conversation, read a global
   default account, or switch authentication to stand in for it.
2. Read `docs/session-quota.md` beneath this skill's owning plugin root for the
   SDK helper and bounded capture contract. Use `acquire_session_quota` with the
   exact expected session ID when the host exposes an existing SDK session.
   When that surface is absent, report unsupported; do not invent a tool.
3. Inspect a minimal, credential-free capture with the exact payload command
   from the budget-guidance session command catalog:

   ```text
   <budget-guidance catalog argv[0]> quota --capture <capture.json> --session-id <requesting-id> --json
   ```

4. Report attribution, quantity kind, acquisition time, unknown provider
   freshness, and reset diagnostics. A matching capture ID is a caller claim,
   not independent account verification. Cached quota is not a fresh provider
   read and cannot supply confident run-rate or fleet concurrency advice.
   When the host exposes provider-owned quota evidence, read
   `docs/provider-quota.md` in the same plugin root and opt in only to the exact
   provider/quota IDs. Provider read time is not a charge-settlement watermark;
   a capture file cannot authenticate itself or enable live arithmetic.
   For compatible monthly evidence, read `docs/provider-pacing.md` in that root.
   Label cycle-average projections as estimates, never measured daily/weekly
   rates. Numeric worker counts remain unavailable without per-worker costs;
   advice does not authorize automatic sleeps, suspensions, or increased
   parallelism.
5. Preserve explicit unavailable/error states and finish with the actual result.
   Do not refresh provider state, consume warnings, change workers, or block an
   operator-directed launch. Never commit captures with real account data.
