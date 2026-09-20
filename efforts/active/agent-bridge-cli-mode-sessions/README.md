# agent-bridge CLI-Mode Sessions

- **Slug:** `agent-bridge-cli-mode-sessions`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase `pr/<slug>` worktrees → landed to `main`
- **Created:** 2026-09-19
- **Status:** Draft
- **Vision:** [`visions/remote-interactive-sessions`](../../../visions/remote-interactive-sessions/README.md)
  (leaf, child of [`visions/agent-fabric`](../../../visions/agent-fabric/README.md))
  — realizes *Session Host CLI mode*, *CWD-keyed discovery*, *symmetric venue
  launch*, and the *opt-in-not-ambient-default* / *bind-dont-self-register* /
  *one-host-per-cwd-lane* behaviors. Also advances
  [`visions/session-hosting`](../../../visions/session-hosting/README.md)'s
  existing `interchangeable-session-hosts` / `capability-honest-control`
  features by making a CLI-hosted session in a remote venue a fully
  interchangeable peer of a local one.
- **Related (independent, not a dependency):**
  [`visions/host-resource-providers`](../../../visions/host-resource-providers/README.md)
  — provisioning local capabilities to a coordinated session. Deliberately
  out of scope here (see *Guiding Intent* and *Request*).
- **Related effort (complementary, not overlapping):**
  [`agent-bridge Session Discovery`](../agent-bridge-session-discovery/README.md)
  (#2530) — that effort lets a caller **find an already-running** session
  across machines/repos. This effort is about **creating and binding** a new
  CLI-mode session; the two compose (a CLI-mode session, once running, is
  discoverable through #2530's primitives like any other) but neither depends
  on the other.

## Guiding Intent

Give agent-bridge a mode where it can **natively create and drive a standard,
muxed, interactive Copilot CLI session** — reusing its existing Session Host,
reattach, and multi-observer coordination mechanics as-is, rather than a
parallel execution protocol. This is deliberately an **explicit, per-request
capability an operator opts into for one session**, not a default shape
delegated/headless work should ever pick up automatically.

## Context

Two known, already-documented gaps currently block treating a CLI-driven
interactive session as a first-class coordinated peer
(`plugins/agent-bridge/docs/delegation-contract.md`):

- *"A registered CLI extension polls a durable inbox and may inject a
  `session.send` turn. Prompt control is experimental until single-stream
  admission is proven."* — a represented interactive peer's `send` can
  currently race/split-stream against other admission sources.
- *"`input_required` ... Boundary is durable; answerability is live and
  becomes `unknown_after_restart` when it cannot be recovered."* — a
  CLI-native `ask_user`/elicitation prompt is not reliably routable to
  whichever client is actually attached.

Separately, the coordination layer's own CLI extension currently resolves
which daemon to bind to **ambiently** rather than through an explicit
assignment — workable for the common case (one daemon, one machine) but with
no answer for "which Session Host does *this* muxed CLI process belong to"
once more than one is in play (a remote venue, a pre-allocated host waiting to
be claimed).

This effort was scoped after reviewing an alternative proposal (a
bridge-owned "native execution" PTY protocol with its own terminal-replay
format, writer/observer/takeover model, and host-resource RPC) against what
already exists: ACP's existing multi-observer coordination, the existing
Session Host's reattach/retirement mechanics, and the fabric's existing
[`single-current-session-per-worktree`](../../../visions/agent-fabric/README.md#single-current-session-per-worktree)
invariant (at most one current session per working directory, already
enforced) — which together make a cwd-keyed discovery assignment sound
without inventing new machinery. See
[`visions/remote-interactive-sessions`](../../../visions/remote-interactive-sessions/README.md)
for the full reconciliation and the resulting north star.

## Request

> Fix up remote-drive of a CLI session as a mode for regular agent-bridge —
> not something most agents should reach for by default, but available
> per-request by the operator. Decouple this entirely from providing more
> local resources/tools to the remote session, which is a separate concern.

## Plan

### Phase 1 — Fix the two blocking ACP gaps

Prerequisite correctness work, useful independent of CLI mode itself: a
represented interactive peer should already be reliable before it becomes the
mechanism CLI mode binds through.

- [ ] Fix `send` single-stream admission for a represented interactive peer so
      an injected `session.send` turn cannot race or split-stream against
      another admission source. Add the regression the delegation contract's
      "experimental until proven" note is waiting on.
- [ ] Make `ask_user_request`/elicitation answerability durable and correctly
      routed to whichever client is currently attached, including after a
      reconnect — resolving the `unknown_after_restart` gap for this path.
- [ ] Land both as ordinary agent-bridge fixes; no CLI-mode-specific code
      depends on them existing yet, but CLI mode is not attempted until they
      do.

### Phase 2 — Session Host CLI mode + cwd-keyed discovery

- [ ] Add a CLI mode to Session Host allocation: the coordination layer
      allocates and prepares a host **without** spawning a `copilot --acp`
      child, leaving it waiting to be claimed.
- [ ] Add a durable, host-local mapping from a working directory to its
      currently assigned Session Host address, populated at allocation time.
- [ ] Update the coordination layer's CLI extension to resolve its own cwd
      against that mapping at startup and bind to the assigned host if one
      exists, rather than defaulting to ambient self-registration. Decide and
      document the explicit fallback behavior (decline vs. an honestly-labeled
      compatibility path) for a cwd with no assignment.
- [ ] Enforce allocate-before-launch ordering and one-host-per-cwd-lane,
      reusing the existing single-current-session-per-worktree gate rather
      than a new one.
- [ ] Confirm a CLI-mode-bound session is indistinguishable from a
      directly-spawned one to every consumer above the host boundary (same
      reattach, retirement, and observation code paths; no forked protocol).

### Phase 3 — Opt-in local launch surface

- [ ] Add an explicit, per-request CLI/API surface to start a CLI-mode
      session locally first — proves the mechanism end-to-end before adding
      any remote venue.
- [ ] Confirm this is purely additive: no existing headless/ACP-driven default
      path changes behavior when CLI mode is never requested.
- [ ] Mark a CLI-mode-bound session with its durable human-attended marker
      (for later recovery/observation guidance honesty).

### Phase 4 — Symmetric venue launch

- [ ] Extend `agent-codespaces` and `agent-containers` to offer the same
      CLI-mode launch shape — prepare the venue, allocate the paired CLI-mode
      Session Host, start a standard muxed CLI process bound to it — over the
      existing venue-parity SSH transport and auth-relay back-channel. No new
      venue-specific transport.

### Phase 5 — Docs and vision closure

- [ ] Update `plugins/agent-bridge`, `agent-codespaces`, and
      `agent-containers` docs/architecture for the new mode and discovery
      mapping.
- [ ] Confirm the realized behavior against
      `visions/remote-interactive-sessions`; no vision revision expected
      (this closes existing intent) unless implementation surfaces a genuine
      should-be gap.

## Validation Plan

- [ ] A represented interactive peer under concurrent `send` pressure never
      produces a split-stream turn (regression covering the Phase 1 fix).
- [ ] A CLI-native `ask_user`/elicitation prompt reaches whichever client is
      currently attached, including across a reconnect.
- [ ] A locally-launched CLI-mode session reattaches, is observed by a second
      client, and is retired using the exact same code paths as an ordinary
      directly-spawned Session Host session — no CLI-mode-specific behavior
      divergence.
- [ ] Two concurrent CLI-mode allocation attempts for the same cwd resolve
      through the existing single-current-session-per-worktree gate (reuse,
      hand-off, or sunset) rather than racing.
- [ ] With CLI mode never requested, an existing headless/ACP-driven session's
      behavior is provably unchanged (no regression from the Phase 2/3
      additions).
- [ ] A CLI-mode session launched via `agent-codespaces`/`agent-containers`
      binds, reattaches, and is observed identically to the local case, over
      the existing venue-parity transport.

## Proposal

_Pending — the exact discovery-mapping wire/storage format and the CLI-mode
launch verb's surface need a short design pass in Phase 2/3 before
implementation._

## Journal

### 2026-09-19 — Kickoff

Effort created from a PR-triage session that compared an alternative
bridge-owned "native execution" PTY proposal against reusing agent-bridge's
existing ACP/Session Host mechanics. Split the resulting vision into
`remote-interactive-sessions` (this effort's target) and the independent
`host-resource-providers` (local-capability provisioning, explicitly out of
scope here) at operator direction, since driving a CLI-mode session and
provisioning local resources to a coordinated session are separable concerns
that should not gate each other. Noted the complementary (not overlapping)
relationship to the existing `agent-bridge-session-discovery` effort (#2530):
that effort finds already-running sessions; this one creates and binds new
CLI-mode ones.
