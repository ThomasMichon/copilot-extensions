# CLI-Default Bridging — Vision

- **Subject:** Whether a **user-global, launch-time-loaded remote-driver
  extension riding an ordinary muxed `copilot` CLI session** should become the
  **default** control surface agent-bridge and agent-dispatch drive
  agent-to-agent work and delegation through — in place of today's default
  (ACP + Session Host) — once a bounded set of open risks is proven out by
  controlled testing.
- **Scope:** leaf (cross-cutting capability within the agent fabric; contests
  one stance of a sibling Active vision — see Non-Goals and Provenance)
- **Status:** Draft — pre-validation. Nothing here authorizes changing any
  default today; see *default-promotion-requires-proof* in Behaviors.
- **Last revised:** 2026-10-02
- **Reality docs:** [`plugins/agent-bridge/docs/architecture.md`](../../plugins/agent-bridge/docs/architecture.md) ·
  [`plugins/agent-worktrees/docs/mux.md`](../../plugins/agent-worktrees/docs/mux.md) ·
  upstream `github/copilot-agent-runtime` (`src/cli/acp/server.ts`,
  `src/cli/sdkServerHost.ts`, `src/sdk/docs/features/streaming-events.md`)

## Purpose & Intent

Today's default mechanism for driving a Copilot session programmatically is
ACP, wrapped by agent-bridge's own **Session Host** — a purpose-built,
reattachable-ACP-wire layer with generation-based claim arbitration, durable
frame replay, and bespoke cross-venue spawners, built specifically because a
bare `--acp` child has none of that on its own. It works, but it was built to
compensate for two things ACP mode does not give a driving program for free:
survivability across a frontend disconnect, and a transport at all for a
process with no attached terminal.

An ordinary interactive, muxed `copilot` session already has both of those —
from different owners. The multiplexer (`tmux`/`psmux`) keeps the process
alive across detach/reattach; that is its entire purpose, confirmed durable in
practice (`agent-worktrees`' own `docs/mux.md`). A loaded extension inside
that real interactive process already has a live, bidirectional SDK
connection to it. The sibling
[remote-interactive-sessions](../remote-interactive-sessions/README.md)
vision already established this for the **human-attended, operator-opted-in**
case: "a Session Host exists to give a headless `copilot --acp` child two
things it cannot provide itself... a muxed, interactive CLI session already
has both, from different owners," and deliberately scoped CLI mode as
**opt-in, never an ambient or default execution mode** a coordinating agent
reaches for on its own.

This vision asks whether that scoping should **change** for agent-to-agent
and delegated work specifically — not because the attended case's reasoning
was wrong, but because **full end-to-end programmatic drive via mux was not
previously verified as achievable**. The existing `remote-interactive-sessions`
scoping is correct about what it covers: it was built for observation and
limited steering of a session a human already started, not for an agent
being able to create a session, fully control it, and cleanly end it the way
ACP + Session Host already can. That is a real, important distinction this
draft must not blur: **ACP already has native session create/terminate** —
`session/new` and `session/close` are ordinary ACP methods Session Host
already relies on. The gap this vision is actually about is narrower and
more concrete: **agent-bridge's own CLI-side extension today only supports
reporting on user-launched sessions and offering limited steering** — it was
never built to create a session, drive it end-to-end, and gracefully end it.
This session's research turned up concrete facts suggesting that gap may now
be closable:

- **Extensions do not auto-load in ACP mode today, and nothing can make them.**
  `src/cli/acp/server.ts` has zero references to "extension" anywhere in the
  file. The runtime's gate
  (`requestExtensions === true || (enableConfigDiscovery === true &&
  featureFlags.EXTENSIONS === true)`, `sdkServerHost.ts:3549-3550`) is never
  satisfied by any CLI flag, environment variable, or ACP wire parameter in
  the ACP session-construction path — confirmed by direct inspection, not
  inferred. An ACP-driven session is therefore invisible to plugin-provided
  sub-agents, skills, and hook-based extensions unless upstream CAR adds a
  flag or wire parameter that does not exist today. A muxed interactive
  session has none of this gap: ambient marketplace extensions load the same
  way they would for a human.
- **`--plugin-dir` is not an ACP-exclusive advantage.** Both the ACP branch
  and the ordinary interactive-startup path in `src/cli/index.ts`
  independently call the identical `scanPluginDirs()` →
  `setAdditionalPlugins()` wiring. Arbitrary, caller-composed plugin
  arrangements are equally available to a mux-launched interactive session —
  the gap above is specifically about *ambient, launch-time extension
  auto-load*, not plugin/skill/MCP composition generally.
- **The programmatic capability gap is narrower than assumed.** A joined
  extension gets real streamed tool-call/tool-result events (not just
  hook-level visibility), and `session.abort()` is reachable and not
  host-only-gated. The genuine, confirmed gap is session **creation** (a
  joined extension cannot call an equivalent of `session/new`) and the
  **ask_user/elicitation routing**, both detailed in Concepts below.
- **ACP's native session create/terminate has no current equivalent in
  agent-bridge's own extension, but mux already offers one.** Session Host
  relies on ACP's `session/new`/`session/close` for full lifecycle control;
  agent-bridge's current CLI-side extension has nothing equivalent because it
  was built only for reporting/steering on human-launched sessions. A
  human-equivalent `/clear` or `/exit` typed into the mux pane is a real,
  available way to close that **specific** gap for a mux-driven session —
  parity with what ACP already does natively, not a capability ACP lacks.

The north star, if validation succeeds: a `copilot` process launched *anywhere*
— any machine, any repo checkout, any CodeSpace, any container, any Dev Box —
is drivable the moment a single, user-global extension install is present on
that machine or image, with **no per-venue install of agent-worktrees,
agent-bridge, or any other plugin required just to make driving possible**.
That is a strictly stronger venue-independence story than today's ACP+Session
Host default, which still requires agent-bridge's own daemon/spawner
infrastructure to exist wherever it drives from.

This is deliberately **not yet a decision**. It is a bounded set of claims this
session's research supports, paired with the concrete open risks that must be
closed — through the careful, controlled tests this vision calls for — before
any sibling vision's "opt-in, never ambient-default" language is revisited.

## Concepts & Components

### Remote-driver extension (user-global, not bridge-bundled)

A standalone, marketplace-installable extension distinct from anything
agent-bridge ships today. Installed once per machine/image at the user-global
level (not per-repo, not per-worktree), it gives baseline drivability —
attach to the live event stream, send/steer, abort — to **any** `copilot`
process that launches with it present, independent of which (if any)
coordination plugin is also installed. agent-bridge's own existing CLI-side
extension (`extensions/agent-bridge/`) is narrower than this by design today:
it was built for **reporting on and lightly steering sessions a human
already launched**, not for creating, fully driving, and gracefully ending
one — the capability this vision is actually about. agent-bridge may still
ship that narrower companion extension for its own niche needs, but the
floor capability — "can this session be driven end-to-end" — belongs to
this standalone extension, not to agent-bridge's existing one.

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

### Driver exclusivity arbitration (new work, not inherited for free)

Session Host's `host_index.py` solves "exactly one driver at a time" through
generation-based claims — a real problem it was built to close. A raw mux
session does the opposite by design: it allows multiple simultaneous
attaches. Betting the default mechanism on mux without an equivalent
exclusivity primitive reopens the split-brain risk Session Host exists to
prevent. This vision treats that primitive as required new work, not an
inherited property of switching to mux.

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
4. **TTY command bridge**, closing the one place agent-bridge's own extension
   still trails ACP's native lifecycle control: typing `/clear` for a fresh
   context window, or `/exit` for a graceful process end, into the mux pane,
   as the mux-driven equivalent of ACP's `session/new`/`session/close` (which
   Session Host already has and relies on). Scoped to a small, named set of
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

A single marketplace/global install gives any `copilot` process on that
machine or image baseline drivability, independent of whether agent-bridge,
agent-worktrees, or any other coordination plugin is also present.

### venue-independent-driving

A CodeSpace, container, or Dev Box needs no agent-bridge daemon, Session
Host, or agent-worktrees install just to become programmatically drivable —
only the remote-driver extension present at the image/machine level.

### launch-time-extension-presence

The extension ships wired into the launch command (or the ambient
marketplace state) of every session this mechanism intends to drive, never
retrofitted into an already-running one.

### mux-native-driver-exclusivity

A generation/claim-style primitive equivalent to Session Host's
`host_index`, built for mux-hosted sessions specifically, so multiple
driving processes cannot race the same session.

### tty-command-bridge-for-session-lifecycle

A narrow, named set of slash-commands (starting with `/clear`, `/exit`)
deliberately typed into the pane, closing agent-bridge's own extension's gap
against ACP's native `session/new`/`session/close` — parity with Session
Host's existing lifecycle control, not a capability beyond it.

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
container, at least one Dev Box image), (b) a driver-exclusivity test proving
two concurrent driving attempts cannot corrupt a session, (c) a blocked-
interaction test exercising all five escalation rungs against real tool
calls and at least one genuine ask_user/elicitation case, and (d) a side-by-
side comparison against an equivalent ACP+Session-Host flow for the same
task — is run and its results are recorded against this vision's Provenance.
Only a result that closes (a)-(c) without an unacceptable regression licenses
editing any sibling vision's default-mechanism language.

## Non-Goals / Boundaries

- **Not a replacement for Session Host everywhere.** Headless, no-peek venues
  where exclusivity and reliability matter more than plugin/feature parity
  keep ACP + Session Host as the default unless and until validation proves
  otherwise for that case too. This vision does not assert blanket
  superiority.
- **Not a generic TTY automation framework.** The TTY command bridge is
  scoped to a small, explicitly named set of recognized slash-commands, not
  arbitrary keystroke injection or screen-scraping as a steady-state
  mechanism.
- **Not claiming ask_user/elicitation can be answered on the human's behalf.**
  The escalation ladder's rungs 2-3 are named as best-guess/terminal-decline
  specifically so no caller mistakes them for a real human-equivalent
  answer.
- **Not editing `remote-interactive-sessions` or `session-hosting`'s current
  status or feature set.** This vision's job is to *prove or disprove*
  whether their attended-only, opt-in framing should extend to unattended/
  delegated work too — not to assert that extension already holds.
- **Not a specification.** This vision fixes the claims, the risks, and the
  validation gate — not the concrete extension package name, wire format,
  or exclusivity-primitive implementation. That belongs to the effort that
  realizes it.

## See Also

- Parent vision: [agent-fabric](../agent-fabric/README.md)
- Sibling vision: [session-hosting](../session-hosting/README.md) — the
  provider-neutral hosting boundary this vision's mux-hosted provider class
  already has a named slot under (`CLI/mux` in *session-host-provider*).
- Sibling vision: [remote-interactive-sessions](../remote-interactive-sessions/README.md) —
  establishes the human-attended, opt-in case this vision asks whether to
  extend to unattended/delegated work; its `opt-in-not-ambient-default` and
  `explicit-per-request-mode` features are the specific stance this vision
  contests, pending validation.
- Sibling vision: [venue-parity](../venue-parity/README.md) — already treats
  `--plugin-dir` resolution as a venue-agnostic core concern; this vision's
  venue-independent-driving feature extends that same principle to the
  extension-loading mechanism itself.
- Plugin vision: [agent-bridge](plugins/agent-bridge/README.md) — owns the
  coordination layer and Session Host whose default-mechanism role this
  vision asks whether to change.
- Plugin vision: [agent-dispatch](plugins/agent-dispatch/README.md) — the
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
