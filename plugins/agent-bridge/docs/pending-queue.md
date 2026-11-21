# Inspect and reconcile a native session's pending queue

`attention` reports current input/permission/policy blockers. It is not an inbox
census. The separate `pending-queue` command reads the original backend inbox
without calling a claiming or consuming poll endpoint:

```text
<agent-bridge catalog argv[0]> pending-queue <native-session-id> --worktree-id <worktree> --json
```

Run it on the target's Bridge host. The target must be an exact native session
ID and its expected worktree binding, not a worktree alias or a cross-host
display label. `--limit` is 1-100 (default 50); pass `--after <next_after>` to
read the next page. Pages are fresh observations, not a frozen census. An
empty page means no unresolved backend records in that page's selection, not
an empty SDK queue or model consumption.

The version-1 JSON contract is `agent-bridge.pending-queue`. It reports original
message IDs, admission/handoff/SDK stages, registration freshness, turn state,
current consumer incarnation and an independently fresh delivery-attention
observation when available. It excludes message bodies and controls. Accepted
but unconsumed records remain visible; absence of legacy consumption tracking
does not prove they are still queued. `sending` and `unknown` preserve uncertain
native effects. The existing hook's last emitted count can appear separately
when this serving process observed its diagnostic event in the last 60 seconds.
It is not a current set or a queue census; an observed consumer incarnation
must still match, and legacy notices without one say their binding is unverified.
Missing/stale notices remain unavailable. This bounded in-memory projection
retains no message bodies and does not require the optional telemetry writer.
Receiver-local pending sets and the native SDK queue remain unavailable.

## Explicit reconciliation

After inspecting the target and current consumer incarnation, an authorized
operator can prepare missing, unstarted original offers:

```text
<agent-bridge catalog argv[0]> pending-queue <native-session-id> --worktree-id <worktree> --reconcile --consumer-incarnation <observed-incarnation> --json
```

This is **original-offer preparation**, not a force-delivery command. It uses
the existing compatible-inbox owner and the same bounded original-ID page.
It attaches missing retained handoff identities to safe, unclaimed ordinary
records, preserving their IDs, order, bodies and existing receipts. Repeating
it does not recreate an existing offer. Concurrent normal polling retains the
same handoff and the receiver's one-shot begin grant.

It does not submit or resend native input, wake/replace a session, reset a
cursor, unmute delivery, clear a hook's pending notice or mark input consumed.
Already started, uncertain, SDK-accepted, runtime-custodied, startup and
interrupt records are left unchanged. The ordinary receiver still owns all
delivery and final admission. A new/current incompatible receiver, changed
worktree, stale registration or non-serving owner refuses before preparation.
A muted receiver stays muted.

The command first reads the selected executing provider's
`GET /api/v1/live-sessions/pending-queue-contract` declaration, then pins its
request to that provider. An older or rolled-back provider returns an explicit
unsupported result before reconciliation, not an empty queue or a fallback
send. Old messaging and attention operations are unchanged; no synchronized
peer upgrade is required. Failures exit 3. Unknown arguments and page bounds
are parser errors. A failed mutation is never automatically replayed.

All routes retain the service's normal authentication. Queue operations also
use the retained-inbox local ingress boundary; proxied/network ingress is not
supported. No new daemon, inbox, message sender or receipt authority is added.
