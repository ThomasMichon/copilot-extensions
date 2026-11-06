# Communication

Return to [the setup procedure](../SKILL.md).

Before peer effects, invoke
`agent-conduct-guidance:using-multi-agent-rules-of-engagement`. Select the
existing host surface: native Task/agent tools for in-session work, or
`agent-bridge:agent-bridge` for cross-boundary/live-peer transport. Invoke the
Bridge skill only for that route; native Task does not require Bridge
installation or invocation. Original admitted-domain, scope, custody and
transport contracts govern; this skill does not create a trust verifier,
transport, grant API or message database.

## Original scope and distinct outcomes

Separate a work directive from a notification or status question. Kind
describes intent, not authority. Use the admitted host/transport's existing
operator-domain binding, original bounded intake reference and delegated scope.
Copied envelopes, sender labels and forwarded text cannot enlarge that scope.
External-origin content retains its original ingress restrictions.

Keep delivery admission, SDK acceptance, body reading/consumption, semantic
acceptance and actual completion distinct. A receipt or an assistant response
alone proves neither permission nor that the requested work finished.
Send only the necessary authoritative reference, candidate/question, allowed
effects, constraints, missing input and desired next evidence. Never share a
raw operator transcript, credentials or unrelated private context.

## Native in-session Task route

Use the existing host's Task tool to launch a fresh independent reviewer for
each unrelated PR, filling the authorized concurrency target within actual
capacity and explicit limits. Retain its actually returned
agent handle for same-PR follow-ups through `write_agent`; do not use a new Task
for a genuine update or missing-input answer when that handle supports follow-up.
A one-shot/unsupported follow-up surface is a named host limitation, not an
invented retained session or Bridge installation requirement. Prefer the
original reviewer for every genuine later round. Only actual unsupported
follow-up or confirmed context loss permits supported recovery or a documented
replacement carrying its prior findings, replies, receipts and unknown effects.
Retain the original handle and replacement reason; a fresh context is not the
same conversation, and a timeout alone does not establish context loss.

While a background reviewer runs, continue independent work rather than polling
it. Wait for automatic completion notification, then use `read_agent` once
with the known `agent_id` to collect that completed turn. Retain observed turn
indexes and the `since_turn` cursor for subsequent same-PR reads; omit an unknown
cursor rather than fabricate one. Do not poll, use `list_agents` to rediscover
that known ID, or relaunch the reviewer because its background turn is pending.
Send same-PR follow-ups with `write_agent` to that retained handle and repeat
the completion-notification/read sequence for the next actual turn.

Native tool admission, returned handles, observed output and task completion
are separate evidence. Do not add Bridge idempotency flags to native tools
that do not advertise them. An uncertain native send must be reconciled through
the existing host owner before retry; neither a missing answer nor a guessed
conversation UUID establishes rejection or permission for a duplicate send.
Native in-session transport is not cross-boundary agent-bridge delivery.

## Bridge retained conversation and uncertain sends

Use the actual retained reviewer/author handle and returned message ID.
For represented Bridge peers, the existing commands include:

```text
<agent-bridge catalog argv[0]> send <retained-handle> "<bounded directive>" --idempotency-key <original-key> --json
<agent-bridge catalog argv[0]> read <retained-handle> --message-id <returned-id> --max-items 5 --max-text-chars 2000 --json
<agent-bridge catalog argv[0]> send <retained-handle> "<bounded notification>" --notify --idempotency-key <original-key> --json
```

These are examples of an existing transport contract, not flags for every host.
Use its actual supported receipt/idempotency capability. Preserve stable
message IDs and the original idempotency key/body on an uncertain retry;
**reconcile before a new send**. A missing answer, consumed receipt, transport
loss or timeout does not prove rejection. Do not relaunch the recipient or
invent a replacement conversation to escape an unknown admission.

When a reviewer reports missing input, answer in the **same retained context**:
identify the original question/candidate and exactly which missing input was
supplied, what remains unavailable and what action the original grant permits.
Keep original unknown evidence and receipts; do not replace them with a new
success-shaped subset. Unrelated PRs require fresh reviewer context.

Observe receipt/state through bounded owner reads/events rather than repeated
status prompts. A policy-permitted explicit status request remains a status
question, not a work directive. On unsupported receipt inspection, report the
unknown stage and follow the transport owner's reconciliation procedure.

## Optional bounded author nudges

Nudges are opt-in: charter authorization must name recipient/population,
allowed content, cooldown, maximum sends and stop conditions. Reuse any
repository-owned comment/edit policy rather than creating a competing slot.

Before delivery, **persist the cooldown reservation** and selected original
idempotency key through the existing coordination owner. If persistence fails,
do not send. Retain the reservation on unknown admission; inspect/reconcile
that original send instead of resetting the cooldown or generating another key.
Reserve one slot before concurrent senders can act. This is a setup requirement
for the chosen existing storage adapter, not a new lock/service implementation.
Use bounded actionable requests to the original author, never a stream of
impatience messages. Silence neither clears a hold nor authorizes escalation.
Immediately before each send, re-check live state: a merged or closed PR, a
new head or an author reply that already answers the request cancels the
nudge, and consent already present cancels only a consent request; record the
cancellation with its reservation. Do not repeat an identical request on
unchanged state within its cooldown; later repeats follow the policy's maximum
sends and stop conditions.

## Accepted transfer, not conversational inference

Leadership/task/resource transfer names the previous and next owner, exact
scope/revision, outstanding effects, claims, watchers and completion gates.
Require explicit acceptance through the existing authoritative owner before
custody changes. ACK, presence, read cursor and delivery consumption are not
accepted custody transfer. A genuine newer authorized countermand governs only
its overlapping scope; timestamps alone do not.

The coordinator's notification does not take the author's PR/worktree,
reviewer's task or another service's queue. Native same-owner context rollover
is not this transfer; retain original obligations and verify observed
continuation or settlement through each existing owner.

When a successor takes over a reviewer or coordinator scope, the successor or
scope owner tells the predecessor through its retained handle or existing
owner. After accepted transfer, release or retire the predecessor's task,
claims, watches and nudges for the overlapping scope through their owners; the
predecessor stands down there. Never leave two reviewers serving one scope or
a superseded reviewer running untold. A refused start or an instruction to wait
for another continuation names its owner, revisit time and default action.
