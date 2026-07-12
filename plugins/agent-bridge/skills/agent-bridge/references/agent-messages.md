# Agent messages — recognizing and answering peer traffic

The fabric can deliver a message **into a live interactive session** (yours, or a
peer's) via agent-bridge. When that happens, the message arrives as a normal user
turn, but wrapped in a structured envelope so you can tell it apart from what the
operator typed and answer it if you choose.

## Recognizing an inbound agent message

A delivered message looks like this (a marker in the same family as the runtime's
`<system_reminder>` / `<system_notification>`):

```
<agent-message from="cjohnson@orchestrator" reply-to="lambda-core-wsl-20260710-200009-ffc8" msg-id="2">
…the actual message body…
</agent-message>
```

- `from` — a human-readable label for who sent it (attribution, not routing).
- `reply-to` — the **routable handle** to answer. Usually a **worktree handle**
  (an agent is a series of sessions in one worktree, so the handle survives a
  handoff — the bridge resolves it to whichever session is live now); it may
  instead be a bare session id. May be absent (then the sender is not itself a
  live session and cannot receive a reply).
- `msg-id` — the sender's message id, useful as a correlation reference.

When you see an `<agent-message>` turn: it came from **another agent via the
bridge**, not from the operator at the keyboard. Treat the inside as the request;
treat the attributes as trusted routing metadata (the mesh is single-operator, so
the transport — not a signature — is the trust boundary).

## Asking a peer and reading the answer (`send` waits by default)

When you `send` to a live session, agent-bridge **waits for the receiver's reply
turn** and prints its assistant output — the reply is just the receiver's
*ordinary* next turn, read back off its represented stream, so there is no extra
protocol to learn:

```bash
agent-bridge send <worktree-handle> "what's the status of the rebase?"
# [>] Delivered to live session <id> (message 12, from you)
# [<] Reply from <id>:
# rebase is done; tests pass. pushing now.
```

- `--reply-timeout <seconds>` bounds the wait (default 120). On timeout the
  message stays queued and is still delivered — you just didn't get the turn.
- `--no-wait` returns as soon as the message is enqueued (fire-and-forget) — use
  it for a `notify`-style poke you don't need an answer to.

This is the everyday way to interrogate a peer: one `send`, read the reply, no
cold sub-agent and no operator relay.

## Replying over the bridge (asynchronous side-channel)

The waited reply above covers most exchanges. Send an **explicit** reply only for
an *asynchronous* answer that must not become your operator-facing turn (a
mid-work status aside). Send back to the `reply-to` address with the **same verb
you use for any agent** — no special tool:

```bash
agent-bridge send <reply-to> "your reply text"
```

`agent-bridge send` recognizes that `<reply-to>` is a live interactive session
(by session id or worktree handle) and delivers your message into it (rather than
treating it as a spawned agent). Your own identity and **worktree handle** are
attached automatically as the new envelope's `from` and `reply-to`, so the other
agent can answer you back — even after you hand off to a fresh session in the same
worktree — a full peer conversation over warm context, no cold sub-agent, no
operator relay.

Optional flags:

- `--from "<label>"` — override the attribution label.
- `--reply-to <handle>` — override the address a reply should target (a worktree
  handle or a session id; defaults to your own worktree handle).

## Notes

- Delivery is **on by default** (single-operator mesh); `/peer` mutes a session
  that should not be interrupted.
- Attribution is **legibility**, not authentication: it tells you who a message
  claims to be from. That is sufficient because only the operator's own tooling
  can reach the local bridge (localhost bind + operator-secured SSH + bearer).
