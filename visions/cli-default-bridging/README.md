# CLI-Default Bridging — Vision

- **Subject:** Whether a **user-global, launch-time-loaded remote-driver
  extension riding an ordinary muxed `copilot` CLI session** should become the
  **default** presentation agent-bridge and agent-dispatch drive
  agent-to-agent work and delegation through, with both presentations using
  the Copilot Driver's common Session Host broker, once controlled validation
  establishes lifecycle, input and ownership parity.
- **Scope:** leaf (cross-cutting capability within the agent fabric; contests
  one stance of a sibling Active vision — see Non-Goals and Provenance)
- **Status:** Draft — pre-validation. Nothing here authorizes changing any
  default today; see *default-promotion-requires-proof* in Behaviors.
- **Last revised:** 2026-11-05
- **Reality docs:** [`plugins/agent-bridge/docs/architecture.md`](../../plugins/agent-bridge/docs/architecture.md) ·
  [`plugins/agent-worktrees/docs/mux.md`](../../plugins/agent-worktrees/docs/mux.md) ·
  upstream `github/copilot-agent-runtime` (`src/cli/acp/server.ts`,
  `src/cli/sdkServerHost.ts`, `src/sdk/docs/features/streaming-events.md`)

## Purpose & Intent

The [Copilot Driver](../plugins/agent-remote-driver/README.md) supplies one
Session Host broker for direct headless ACP children and extension-connected
interactive mux sessions. Both presentations preserve durable execution,
exclusive input custody, native identity and caller-independent reconnection.
Factoring that common app is independent of choosing a default presentation.

This vision asks whether muxed interactive Copilot should become the preferred
presentation for agent-to-agent and delegated work. Such a choice must earn
full lifecycle, extension presence, input/permission handling and exclusive
driver parity against the headless mode. A working mux or SDK connection is not
proof that a caller can create, drive, restore and retire the complete session.

The north star is venue-independent driving with the selected minimal Driver
package, without a receiving coordination suite or a second launcher. Changing
the default remains a separate controlled decision; the explicit per-request
mode contract stays intact until that decision clears its validation gate.

## Concepts & Components

### Standalone remote-driver and scoped managed delivery

A standalone extension supplies interactive Copilot's native connection to
the Driver. Its independently installed or hoisted core app owns Session Host
and mux wrapping. Headed extension integration and direct headless ACP children
expose the same broker; the extension does not make Bridge its mandatory host.

A Bridge- or Worktrees-requested launch can carry the same selected compatible
redistributable, including scoped launch-time extension trust, without requiring
the guest's full suite or rewriting its global plugin configuration. This
composes [host-selected receiving support](../plugins/agent-bridge/README.md#host-selected-receiving-support),
not a second driver or launcher. It does not promote CLI mode as the default.

Bridge may retain its narrower reporting/light-steering companion, but the
end-to-end floor belongs to the standalone remote-driver in either delivery
form. Both retain launch-time presence, exclusive driver custody and the
receiving repository's hosting policy.

### Launch-time presence, not join-time injection

The permission- and hook-relevant capabilities below are only available when
the extension is present **from session creation** — confirmed directly:
`onPreToolUse` hooks registered by a joining extension apply only to tool
calls dispatched *after* registration, never retroactively; and ask_user/
elicitation routing is fixed at creation/cold-resume and explicitly
*ignores* a warm-attaching connection's variant. "Reliably pushed into the
target session" must therefore mean **wired into the launch command**
(`--plugin-dir`, marketplace install, or equivalent), never injected into an
already-running session after the fact. This is a correction to this
vision's own working premise at the start of the research that produced it.

### Driver exclusivity arbitration

The common Driver broker preserves exclusive mutation custody for both
presentations while permitting independent observation. Terminal attachment
does not grant another programmatic writer, and changing presentation cannot
fork the native conversation or bypass human takeover.

### The blocked-interaction escalation ladder

Ordered from least to most costly, for a tool call or prompt that would
otherwise block on a human:

1. **Pre-decide via hook or pre-trust policy.** `onPreToolUse` can allow/deny
   a known-safe/known-unsafe call before any prompt is shown, when the
   extension was present from launch. This closes most ordinary
   tool-permission friction without touching ask_user/elicitation at all.
2. **Synthesize a best-guess answer.** The legacy ask_user callback
   (`onUserInputRequest`) has **no decline-shaped return at all** — its
   type forces `{answer, wasFreeform}`. The available workaround is
   crafting the answer text itself (e.g. "the user is not immediately
   available; proceed with your best reasonable assumption and flag it"),
   not a protocol-level decline.
3. **Decline or cancel, where the SDK allows it.** Structured elicitation
   (`onElicitationRequest`) has first-class `"decline"`/`"cancel"` actions —
   but they are **final**, not deferrable; there is no "pending, I'll answer
   out-of-band shortly" state in the SDK today.
4. **TTY command bridge**, supplying the proposed path's full lifecycle:
   typing `/clear` for a fresh
   context window, or `/exit` for a graceful process end, into the mux pane,
   with creation/fresh-context and owned termination guarantees comparable to
   ACP + Session Host. Scoped to a small, named set of
   recognized slash-commands — not a general automation mechanism.
5. **Worst case: full TTY screen-scrape driving.** Acknowledged explicitly
   as large, fragile, last-resort code that this vision does not propose
   building as a normal-path mechanism. Its existence as a fallback is a
   reason to prioritize closing rungs 1-4, not a reason to accept it as a
   steady-state design.

Nothing in this ladder claims a true human-equivalent answer exists for
ask_user/elicitation. Rungs 2-3 are explicitly best-guess or terminal-decline,
never a deferred real answer — a gap this vision does not claim to close and
flags honestly for whatever caller depends on it.

## Features

### user-global-remote-driver-install

An independent Driver app installation makes the core hosting capability
available on a machine or image; selected interactive launches carry its
extension from creation. No coordination plugin is required on that target.

### venue-independent-driving

A CodeSpace, container, or machine needs no Bridge, Worktrees or Manager install
just to become programmatically drivable. The selected Driver package supplies
Session Host and the chosen presentation's support as one receiving app.

### launch-time-extension-presence

The extension ships wired into the launch command (or the ambient
marketplace state) of every session this mechanism intends to drive, never
retrofitted into an already-running one.

### mux-native-driver-exclusivity

The Driver's common broker retains generation/claim custody for mux-hosted
sessions, so multiple driving processes cannot race the same conversation.

### tty-command-bridge-for-session-lifecycle

A narrow, named set of slash-commands (starting with `/clear`, `/exit`)
deliberately typed into the pane, closing agent-bridge's own extension's gap
against the existing ACP + Session Host lifecycle flow. Creation/fresh context
and owned, deliberate termination remain required guarantees, not capabilities
proved solely by slash-command or protocol names.

### layered-blocked-interaction-escalation

The escalation ladder above, implemented as an explicit, inspectable policy
— never a silent hang and never a claim of a human-equivalent answer where
none exists.

### controlled-validation-before-default-promotion

A named validation plan (see Behaviors) whose results — not architectural
appeal alone — decide whether any default changes.

## Behaviors

### extension-presence-is-launch-time-only

No code path treats a post-launch extension join as equivalent to launch-time
presence for permission, hook, or ask_user/elicitation purposes. Where this
distinction matters, the mechanism fails closed (falls back to the
escalation ladder) rather than assuming parity it cannot honestly provide.

### never-silently-hang-on-a-blocked-prompt

A tool call or prompt that cannot be resolved by rungs 1-3 of the escalation
ladder visibly surfaces that fact (to a monitoring caller, a log, or a
retained subscriber) rather than leaving the turn indefinitely pending with
no observable signal.

### default-promotion-requires-proof

This vision's Status stays **Draft**, and
[remote-interactive-sessions](../remote-interactive-sessions/README.md)'s
`opt-in-not-ambient-default` / `explicit-per-request-mode` features remain
authoritative, until a controlled validation plan — at minimum: (a) a
cross-venue extension-injection reliability test (local machine, CodeSpace,
container, at least one Dev Box image), including functional launch-time
presence at the explicitly selected minimum supported native version,
(b) a driver-exclusivity test proving
two concurrent driving attempts cannot corrupt a session, (c) a blocked-
interaction test exercising all five escalation rungs against real tool
calls and at least one genuine ask_user/elicitation case, and (d) a side-by-
side comparison against an equivalent ACP+Session-Host flow for the same
task — is run and its results are recorded against this vision's Provenance.
Only a result that closes (a)-(c) without an unacceptable regression licenses
editing any sibling vision's default-mechanism language.

## Non-Goals / Boundaries

- **Not removal of Session Host.** Both Driver modes use its common broker;
  headless mode keeps direct ACP child hosting. Selecting a different
  presentation does not remove ownership, replay or lifecycle guarantees.
- **Not a generic TTY automation framework.** The TTY command bridge is
  scoped to a small, explicitly named set of recognized slash-commands, not
  arbitrary keystroke injection or screen-scraping as a steady-state
  mechanism.
- **Not claiming ask_user/elicitation can be answered on the human's behalf.**
  The escalation ladder's rungs 2-3 are named as best-guess/terminal-decline
  specifically so no caller mistakes them for a real human-equivalent
  answer.
- **Not default promotion through architectural factoring.** The Driver's
  common broker does not itself change the explicit per-request mode contract
  or assert that unattended interactive driving has met its validation gate.
- **Not a specification.** This vision fixes the claims, the risks, and the
  validation gate — not the concrete extension package name, wire format,
  or exclusivity-primitive implementation. That belongs to the effort that
  realizes it.

## See Also

- Parent vision: [agent-fabric](../agent-fabric/README.md)
- Sibling vision: [session-hosting](../session-hosting/README.md) — the
  provider-neutral hosting boundary this vision's mux-hosted provider class
  already has a named slot under (`CLI/mux` in *session-host-provider*).
- Execution app: [Copilot Driver](../plugins/agent-remote-driver/README.md) —
  owns Session Host, mux wrapping and standalone receiving distribution.
- Sibling vision: [remote-interactive-sessions](../remote-interactive-sessions/README.md) —
  establishes the human-attended, opt-in case this vision asks whether to
  extend to unattended/delegated work; its `opt-in-not-ambient-default` and
  `explicit-per-request-mode` features are the specific stance this vision
  contests, pending validation.
- Sibling vision: [venue-parity](../venue-parity/README.md) — already treats
  `--plugin-dir` resolution as a venue-agnostic core concern; this vision's
  venue-independent-driving feature extends that same principle to the
  extension-loading mechanism itself.
- Plugin vision: [agent-bridge](../plugins/agent-bridge/README.md) — owns the
  coordination layer consuming the Driver's broker.
- Plugin vision: [agent-dispatch](../plugins/agent-dispatch/README.md) — the
  delegation layer named in this vision's Subject as a second consumer of
  whichever mechanism becomes default.

## Provenance

- **2026-10-02** — Initial draft, authored from a single research session's
  direct inspection of `github/copilot-agent-runtime` (hooks, session
  control, permission/ask_user routing, extension-loading gates across
  interactive/ACP/AHP/prompt modes) and `copilot-extensions` (agent-bridge's
  Session Host architecture, agent-worktrees' mux implementation).
  Originating conversation identified: the ACP extension-loading gate has no
  existing override (confirmed by direct inspection of `acp/server.ts`,
  not inferred from documentation); `--plugin-dir` already works
  identically in interactive and ACP modes; a joined extension cannot
  create sessions but can stream tool-call-level events and call
  `session.abort()`; `ask_user` has no decline shape while elicitation has
  only a terminal (non-deferrable) one; and mux's detach/reattach already
  matches what Session Host provides for survivability, but not for driver
  exclusivity. No validation work has yet been performed; see
  *default-promotion-requires-proof*.
- **2026-10-09** — Reconciled lifecycle and extension-presence framing against
  the current coordination implementation. Creation/fresh context and owned
  termination remain required, without attributing downstream termination to
  native `session/close`. Distinguished SDK-extension availability from
  plugin/skill/MCP composition and retained the independent launch-time proof.
  Historical native-source observations above are not a fresh validation of
  every supported native version. No default or promotion gate changed.
