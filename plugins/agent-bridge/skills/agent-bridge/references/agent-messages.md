# Agent messages — recognizing and answering peer traffic

Use the exact `argv[0]` from the agent-bridge session command catalog for the
interactive `send` operations below. Replace
`<agent-bridge catalog argv[0]>` with that path and never search `PATH`.

The fabric can deliver a message **into a live interactive session** (yours, or a
peer's) via agent-bridge. When that happens, the message arrives as a normal user
turn, but wrapped in a structured envelope so you can tell it apart from what the
operator typed and answer it if you choose.

## Recognizing an inbound agent message

A delivered message looks like this (a marker in the same family as the runtime's
`<system_reminder>` / `<system_notification>`):

```
<agent-message from="peer-worktree" reply-to="live:host-a:project-a:peer-worktree" msg-id="2">
…the actual message body…
</agent-message>
```

- `from` — a human-readable label for who sent it (attribution, not routing).
- `reply-to` — the **routable address** to answer. Registered worktree callers
  advertise `live:<machine>:<project>:<worktree>`, not a conversation id. The
  address selects the hosting bridge and its current fresh session for that
  exact project/worktree, so it survives session turnover and bridge restart.
  The project may be slash-separated (for example `owner/repo`); machine and
  worktree remain single identity components.
  Windows and WSL are separate logical machines: `host-a` and `host-a-wsl`.
  The machine/SSH owners project a legacy Windows/WSL topology into those
  independent keys and exact profiles. Use `live:host-a-wsl:project-a:peer-worktree`
  for the WSL peer; there is no extra environment component in a peer address.
  Legacy local worktree handles and exact session ids remain supported. It may
  be absent when the sender cannot receive a live-session reply.
- `msg-id` — the sender's message id, useful as a correlation reference.
- `kind` — the message's **intent** (absent means `prompt`). `prompt` is a work
  directive — act on it. `notify` is informational — no reply or new work
  expected. `status-check` asks only for a **terse status answer** via the
  side-channel — reply briefly and do **not** start a task. When `kind` is not
  `prompt`, a short guidance line inside the envelope restates this.

When you see an `<agent-message>` turn: it came from **another agent via the
bridge**, not from the operator at the keyboard. Treat the inside as the request;
treat the attributes as trusted routing metadata (the mesh is single-operator, so
the transport — not a signature — is the trust boundary).

## Send a receipt, then observe the busy peer

Sending to a represented live session returns immediately after durable inbox
admission. The receipt identifies the message and target; it does not imply that
the recipient has answered. Read the peer's busy state, pending messages, and
recent output separately:

```bash
<agent-bridge catalog argv[0]> send <worktree-handle> "what's the status of the rebase?" --json
<agent-bridge catalog argv[0]> read <worktree-handle> --message-id 12 --max-items 5 --max-text-chars 2000 --json
```

- `receipt.stage` distinguishes `admitted` (stored in the bridge inbox),
  `sdk_accepted` (the extension's SDK send returned), and `consumed` (the
  recipient recorded the attributed root `user.message`). SDK acceptance is
  not consumption. Receipt timestamps are durable within the existing inbox's
  retention lifetime; retirement may remove them. A same-process session rename
  preserves the message ID through the existing alias mapping.
- `read` is bounded and does not acknowledge inbox messages or move a delivery
  cursor. `state.pending_messages` includes both inbox-stored and SDK-accepted
  messages whose consumption has not been observed. Only metadata is returned,
  not their bodies; missing consumption evidence is not proof of delivery failure.
- `--wait-reply` explicitly opts into a post-consumption turn wait.
  `--reply-timeout <seconds>` bounds it (default 120, maximum 600). Nested output
  and empty tool-only completions do not settle it. The result names
  `reply_correlation: post_consumption_turn`: assistant text after consumption
  is evidence, not semantic proof that it answered this particular message.
  Timeout does not revoke admission or prove that the message remains queued.
- `--no-wait` is still available, including for bridge-owned ACP sends.
- On an old daemon, missing receipt support is explicit: `delivered: null`,
  and `read --message-id` refuses with an unsupported-protocol error.
  Current receipt inspection requires HTTP protocol 39. If retention disappears
  after admission, the response preserves the known admission evidence with
  `receipt_current: false` and `receipt_error`; an unavailable duplicate's
  historical receipt is explicitly `null`, never fabricated.
- Never resend merely because a busy peer has not answered. If transport loss
  requires a retry, reuse the original `--idempotency-key` and identical request.
- `--kind` sets intent: `--notify` (informational, no answer expected) or
  `--status-check` (asks the receiver for a terse status ack, not new work), vs
  the default `prompt` (a work directive). Prefer readback for observation rather
  than sending repeated status checks.
- `--delivery` sets live-session urgency without changing `kind`: `queue`
  explicitly waits until the receiver's current turn ends, `steer` (default) injects at the
  running turn's next step without cancelling it, and `interrupt` aborts the
  current turn before sending. Shorthands: `--steer`, `--interrupt`. Aborting
  a running turn also cancels the receiver's background sub-agents; when its
  main turn is idle (only background sub-agents running), `interrupt` sends
  immediately without aborting, so those sub-agents keep running.

Ordinary peer sends use SDK immediate admission without aborting the current
main turn or its background agents, with explicit `agent-bridge` provenance
rather than impersonating operator input. Missing delivery fields use steering;
unknown urgency is logged and left unacknowledged instead of silently queueing.

For another machine, use its qualified address with the same command:
`send live:host-b:project-a:peer-worktree "status?"`. The existing authenticated
carrier reaches the hosting bridge; both reply metadata and the waited reply
turn return through it. Remote waits are bounded to 0-600 seconds; zero
returns the admission receipt without waiting for a reply, just like a local send.
Client, HTTP and carrier operation deadlines cover the requested reply wait
plus transport margin, including when their defaults are used without the CLI.
Qualified addressing requires HTTP protocol 35 and remote carrier operation
version 4. Receipt-backed remote reply waits require HTTP protocol 39 and
carrier operation version 5; they are opt-in with `--wait-reply`, just like
local represented sends. An older hosting bridge/carrier refuses explicitly rather than
silently dropping the reply fields. An unavailable qualified peer is never
interpreted as an agent to create: restore its registration or resume its
existing session through the normal owner flow. An offline address does not
promise that a message was queued for a future successor.
If the caller's qualified identity is still unresolved, the remote call can
return its waited reply, but omits an automatic local-only side-channel address
rather than letting another bridge interpret the caller's worktree handle as
its own. Resolve the caller's registration before relying on asynchronous
cross-machine replies.

The carrier distinguishes explicit qualified addressing with
`qualified_peer: true`, which the qualified CLI send supplies. Raw HTTP
and client APIs default to legacy/exact addressing; an exact ID beginning
with `live:` remains an exact ID even through an older carrier or daemon.
Default lookup queries the daemon before applying a new-protocol gate.
Explicit qualified mode requires the new protocol and selects the complete
machine/project/worktree tuple even if a legacy exact ID has the same string.

## Replying over the bridge (asynchronous side-channel)

An explicit reply can arrive asynchronously while the sender does other work.
Send back to the `reply-to` address with the **same verb
you use for any agent** — no special tool:

```bash
<agent-bridge catalog argv[0]> send <reply-to> "your reply text"
```

The payload-local `send` operation recognizes the qualified peer address
(or a legacy local session/worktree handle) and delivers your message into it
rather than treating it as a spawned agent. Your readable sender label and
qualified reply address are attached as the new envelope's `from` and
`reply-to`, so the other
agent can answer you back — even after you hand off to a fresh session in the same
worktree — a full peer conversation over warm context, no cold sub-agent, no
operator relay.

Optional flags:

- `--from "<label>"` — override the attribution label.
- `--reply-to <handle>` — override the address a reply should target; defaults
  to the qualified address from your caller's live registration when available.
  `from` is only a display label: always answer the exact `reply-to`.

## Notes

- Delivery is **on by default** (single-operator mesh); `/peer` mutes a session
  that should not be interrupted.
- Attribution is **legibility**, not authentication: it tells you who a message
  claims to be from. That is sufficient because only the operator's own tooling
  can reach the local bridge (localhost bind + operator-secured SSH + bearer).
- A session an agent **embodied** (`agent-worktrees embody --driver <agent>`) <!-- marketplace-isolation: allow agent-worktrees-management -->
  registers a `driven_by` marker with the bridge — the "driven by `<agent>`"
  banner — so a human dropping into it (e.g. via Neuron Forge takeover) sees who
  is steering. An operator-launched session has no driver.
