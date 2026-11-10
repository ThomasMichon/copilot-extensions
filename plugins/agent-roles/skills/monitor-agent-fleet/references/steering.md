# Steering protocol

Back to [Monitor agent fleet](../SKILL.md).

Use this only under the charter's explicit steering scope. Without it, report
the proposed steer and **messages sent: none**.

## Before sending

1. **Scope.** Confirm the recipient and purpose are inside the recorded
   steering scope and its validity; reconcile any later hold, narrowing or
   revocation. An all-work hold still means hold.
2. **Current head.** Resolve the target workspace's current head session
   through its existing owner. A stale, ambiguous or changed head requires
   reconciliation; never fall back to a machine default, an alternate session
   or a replacement worker.
3. **Still applies.** Re-read the target's latest substantive result. If the
   issue was already corrected or the evidence changed, update or drop the
   steer.
4. **Deduplicate.** Derive a stable key from target owner plus diagnosed issue.
   Keep at most one outstanding steer per key. An unchanged issue with a
   pending or unknown steer is reobserved, not re-sent.
5. **Persist first.** Record the pending steer (key, recipient, evidence,
   requested next action, expected exit, time) in the supervisory record before
   the send. If persistence fails, do not send.

## Sending

Use the existing messaging adapter selected for the charter (for example
`agent-bridge:agent-bridge` for a cross-boundary live peer, or the host's
native follow-up to a retained in-session agent), with a prompt file or
supported stdin to preserve multiline text. Follow
`agent-conduct-guidance:using-multi-agent-rules-of-engagement`; the steer
carries the original operator source reference and stays within its scope.
Use the bounded steer shape in [interventions](interventions.md#a-bounded-nudge).

## After sending

Record the actual admission/result returned by the adapter. Keep separate:

| State | Meaning | Not |
|-------|---------|-----|
| Delivery | The transport admitted or delivered the message | Acceptance |
| Consumption | The recipient read or acknowledged it | Agreement or progress |
| Acceptance | The recipient adopted the changed next action | Progress |
| Progress | A new result advances the root criterion | — |

Unknown admission is reconciled through the messaging owner's receipts, never
by blind resend. Reobserve at the agreed event or checkpoint. Close the pending
steer when progress or a recorded disagreement is observed; send another only
for materially new evidence. If the recipient disputes the steer, return the
evidence and decision to its owner/operator rather than alternating commands.

For a keyed live send, keep the exact binding with the pending record: expected
session, idempotency key, sender and the original body file. A receipt-only
re-observation must repeat that binding byte for byte, including any trailing
newline, so pass the saved file rather than retyped or normalized text. A
binding-conflict refusal on that read means the observation differs from the
original request, not that delivery failed; correct the binding, never resend.

Receipt stages `admitted` and `sdk_accepted` are delivery; `consumed` is
consumption, proving the message reached the recipient's agent loop, not that
the requested action ran. Confirm acceptance from the recipient's own report or
tool history, such as the requested tool appearing in a later turn. A message
to a recipient whose turn is running or stalled can stay `admitted` or
`sdk_accepted` for hours: record the stage and observation time, reobserve,
never replay.

## Never

- Answer the target's permission prompts, approve its scope change or merge.
  A routine parked question is answered only under the separate answer scope
  in [parked questions](pending-input.md).
- Start, stop, restart or replace the target's session or venue.
- Edit the target's effort, agenda, claims, branch or checkpoint. Correcting
  an inactive owner's records is the separate
  [dormant correction](dormant-correction.md) mode with its own grant.
- Steer a reviewer's verdict or an author's consent.
- Treat a steer, acknowledgement or successful send as completion evidence.
