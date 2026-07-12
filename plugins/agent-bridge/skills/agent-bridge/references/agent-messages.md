# Agent messages — recognizing and answering peer traffic

The fabric can deliver a message **into a live interactive session** (yours, or a
peer's) via agent-bridge. When that happens, the message arrives as a normal user
turn, but wrapped in a structured envelope so you can tell it apart from what the
operator typed and answer it if you choose.

## Recognizing an inbound agent message

A delivered message looks like this (a marker in the same family as the runtime's
`<system_reminder>` / `<system_notification>`):

```
<agent-message from="cjohnson@orchestrator" reply-to="81ec1b77-…" msg-id="2">
…the actual message body…
</agent-message>
```

- `from` — a human-readable label for who sent it (attribution, not routing).
- `reply-to` — the **routable session id** to answer. May be absent (then the
  sender is not itself a live session and cannot receive a reply).
- `msg-id` — the sender's message id, useful as a correlation reference.

When you see an `<agent-message>` turn: it came from **another agent via the
bridge**, not from the operator at the keyboard. Treat the inside as the request;
treat the attributes as trusted routing metadata (the mesh is single-operator, so
the transport — not a signature — is the trust boundary).

## Replying over the bridge

To answer, send back to the `reply-to` address with the **same verb you use for
any agent** — no special tool:

```bash
agent-bridge send <reply-to> "your reply text"
```

`agent-bridge send` recognizes that `<reply-to>` is a live interactive session and
delivers your message into it (rather than treating it as a spawned agent). Your
own identity and session are attached automatically as the new envelope's `from`
and `reply-to`, so the other agent can answer you back — a full peer conversation
over warm context, no cold sub-agent, no operator relay.

Optional flags:

- `--from "<label>"` — override the attribution label.
- `--reply-to <session-id>` — override the address a reply should target
  (defaults to your own session).

## Notes

- Delivery is **on by default** (single-operator mesh); `/peer` mutes a session
  that should not be interrupted.
- Attribution is **legibility**, not authentication: it tells you who a message
  claims to be from. That is sufficient because only the operator's own tooling
  can reach the local bridge (localhost bind + operator-secured SSH + bearer).
