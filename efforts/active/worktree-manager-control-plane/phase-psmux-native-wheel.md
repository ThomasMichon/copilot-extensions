# Finite PSMux native-wheel configuration

[Back to the Manager effort](README.md).

**Status:** Done for the finite source increment; implementation #13524.
Native wheel-event and whole-parent acceptance remain open.
**Source:** #4102, including the complete preserved #4305 original and
M4305-P1/P2/P3/LV/G. The parent stays open across partial source acceptance.
**Participant:** Native-wheel configuration implementer (`patient-knob`).

## Outcome and boundaries

Configure PSMux through its supported native mouse-event path, not through
tmux-only `WheelUpPane` / `WheelDownPane` bindings to `send-keys -M`.
Keep both configuration producers and the actual launcher composition
consistent, with truthful documentation of supported application behavior.
This is a finite source correction, not a promise that every terminal or
frontend receives wheel input and not a new all-host qualification programme.

Reconcile #4102's original all-custom-keys opt-in wording with the accepted
[Mux Companion scope](../../../visions/mux-companion/README.md#concepts--components):
automatic defaults on a private worktree PSMux server remain automatic;
shared-tmux key tuning remains explicitly selected. The optional PowerShell
helper deliberately persists its managed block unless `-NoPersist` is
selected and applies its fragment to the selected live-server inventory.
Calling that helper is not a no-op or a per-worktree-only action.

Personal/unmanaged configuration, unrelated settings, exact session/private
server selection and ordinary tmux behavior remain protected. Legacy owned-file
retirement is a separate lifecycle/approval boundary; filename or old header
does not authorize deleting unknown or edited personal configuration.

## Source evidence and capability limits

The upstream release sources distinguish actual behavior from comments or
executable presence:

| Target | Actual source mechanism | Limit |
|--------|-------------------------|-------|
| Reported PSMux 3.3.4 | [Client wheel dispatch](https://github.com/psmux/psmux/blob/v3.3.4/src/client.rs#L3444-L3468) emits native `pane-scroll` up/down; [the receiver](https://github.com/psmux/psmux/blob/v3.3.4/src/window_ops.rs#L923-L977) injects both wheel directions for an alternate-screen pane. | Main-screen applications do not gain app-directed wheel forwarding from these nonexistent mouse-key bindings. Existing copy-mode/scrollback policy still applies. |
| PSMux 3.4.0 | [Client dispatch](https://github.com/psmux/psmux/blob/v3.4.0/src/client.rs#L7171-L7199), [server mouse admission](https://github.com/psmux/psmux/blob/v3.4.0/src/server/mod.rs#L4144-L4156), and [pane delivery](https://github.com/psmux/psmux/blob/v3.4.0/src/window_ops.rs#L2011-L2116) use native mouse handling. The actual [forwarding predicate](https://github.com/psmux/psmux/blob/v3.4.0/src/window_ops.rs#L396-L398) permits alternate-screen OR app-owned mouse-protocol input. | A nearby historical alternate-screen-only comment is not the predicate. No unconditional arrow-key or Enter translation is appropriate for arbitrary inline applications. |
| Mouse-key declarations | [3.3.4 key parsing](https://github.com/psmux/psmux/blob/v3.3.4/src/config.rs#L1027-L1108) cannot represent these names; [3.4.0 explicitly describes them as unmodelled](https://github.com/psmux/psmux/blob/v3.4.0/src/config.rs#L2560-L2574). | Accepted source-file text or a successful bind command is not wheel-operation evidence. |

These are source observations, not a current-host version census or native
reproduction. Keep the existing new-session version policy and older-session
compatibility unchanged. In particular, do not add a wheel-version floor or
infer capabilities from whichever executable happens to be on PATH.

## Plan

- [x] **W-P1:** Correct BOTH `apply-mux-keybinds.ps1`'s persisted block and
  `psmux-passthrough.conf` to select native mouse handling after root-table
  clearing, without either unsupported wheel binding. Keep explicit
  `mouse on` and the existing private-session scrollback policy coherent.
  Do not substitute fabricated arrow/Enter sequences.
- [x] **W-P2:** Preserve the actual `source-file -t <session>` launcher seam,
  automatic private-server defaults, opt-in helper/persistence boundary,
  existing managed-block replacement and unrelated personal content.
  Do not change `apply-mux-keybinds.sh`'s working tmux wheel mechanism.
- [x] **W-P3:** Correct misleading helper/session-options comments and the
  authoritative mux/default documentation. Reconcile both original policy
  outcomes explicitly; document automatic private defaults versus deliberate
  helper persistence, both wheel directions and the target/application limits.
  Refresh directly relevant shipped examples if they repeat the bad recipe.
- [x] **W-P4:** Add feasible focused fail-first controls through actual
  PowerShell helper and sourced-fragment/launcher consumers using inert
  synthetic command owners and private configuration fixtures. Qualify
  persistence/no-persistence, exact session selection, personal sentinels,
  both directions' native contract and ordinary tmux compatibility. Do not
  label a fake mux or a text assertion as native event delivery.
- [x] **W-P5:** Run the owning repository's focused contained tests, strict
  whole-file Python checks where affected, lint/docs/install-contract and
  exact-source required CI; obtain a genuine review, merge, then record
  canonical finite acceptance separately from the parent's remaining work.

## Validation and acceptance

- [x] **W-V1:** Regression controls fail against the unsupported configuration,
  then pass against BOTH actual emitted/persisted PowerShell configuration
  and the fragment consumed at the existing named-session seam. Include a
  fixture starting with mouse handling disabled, rather than only testing
  an already-configured happy path.
- [x] **W-V2:** Real helper execution against inert command owners preserves
  personal sentinels, managed-block idempotence, `-NoPersist`, no-server and
  missing-fragment behavior. No real mux executable, process or configuration
  is consulted. Existing tmux option/binding controls remain passing.
- [x] **W-V3:** Document the source-supported positive and unsupported
  capability boundaries above; distinguish source, synthetic process,
  actual native and installed tiers. A code comment, static file presence or
  synthetic success does not establish real event receipt.
- [x] **W-V4:** Required exact-head checks and review pass before merge.
  Reconcile the accepted source delta with M4305-P1/P2/P3/LV/G without
  silently checking off unqualified wheel/installed behavior, closing #4102,
  or changing another owner's acceptance.

No live mux command/help/version probe, key event, installation, personal
configuration write or cleanup is authorized by this plan. Native/installed
wheel evidence remains explicitly unobserved until a separately admitted
fixture or operation supplies it. That is a retained acceptance boundary,
not a requirement to invent a native run before reviewing this source plan.

## Coordination

The existing Manager effort is canonical; this is its bounded companion, not
another programme or implementation authority. The #4102 discussion reservation
owns only this source increment. Preserve #13507's path, compatibility,
side-by-side installation and launcher-selection work; no competing changes
to those seams are included. Keep restart-recovery, Mux Companion,
configuration, source/resource and native custodians unchanged.

No service, scheduler, new probe command, native version census, new runtime
dependency, installer change, rollout, legacy-file removal or claim transfer
is part of this source delivery.

## Journal

### 2026-11-19 - Qualified finite source acceptance

Reviewed intent #13514 preceded implementation #13524, actually merged at
23:03:26 UTC as `77c8d9cea5710e3c4a7d817bbf1118570e3e1fad`.
The helper's persisted block and sourced fragment use native mouse handling,
with honest private-default versus explicit persistent-helper documentation.
Ordinary tmux behavior, version/installer owners and legacy retirement remain
unchanged.

Four actual-helper/fragment controls failed before the configuration change.
The focused contained packet passes 24 cases, covering all seven new controls,
existing decoupling and relevant launcher composition. An installed-wheel
fixture assumed an editable source location; both source-payload uses now
select the canonical test source independently of the installed package.
The retained callback fixture's old text delimiter also captured unrelated
launcher main code. PowerShell AST extraction now selects only its named
function, preserving the fail-on-error gate, synthetic mux-observation
boundary and actual packaged register/remove mapping assertions.

The three complete changed Python test modules pass strict Python 3.10
analysis for Linux and Windows, with no ignores/stubs or policy changes.
F/E9 lint, documentation consistency, PowerShell parsing, install-contract,
module-size and whitespace checks pass. Current-head Copilot approval
`5984760418` reports no open issues; refreshed required CI
[44632843332](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44632843332)
passes actual unit/coverage and the final PR gate.

Disposable Windows run
[44631128191](https://github.com/ThomasMichon/copilot-extensions/actions/runs/44631128191)
qualifies exact published head
`a9876d9a18527f5f1649b067a7ecaafabcb91a05`: 128 passed, zero failed/skipped,
all three phases passing in 286.907 seconds. Its selection excludes only four
declared POSIX-only pane-environment cases. The preceding full-file run's
128 passes plus four platform skips remain an **incomplete** qualification,
not a retroactive pass. Earlier local timeouts and the first hosted callback
failures remain failed observations; no assertion, timeout, cleanup,
containment or qualifier was weakened.

This accepts W-P1..5/W-V1..4 only. M4305-P1/P2/P3/LV/G and #4102 remain
open for whole-contract reconciliation and unqualified wheel/native evidence.
Source-supported capability limits, synthetic configuration/process controls
and an installed-wheel fixture are not proof that a user's wheel event reached
an application. Release, productive installation, live configuration, native
wheel events and legacy-file cleanup were not observed or authorized here.
