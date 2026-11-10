# First-run Manager front door (#542)

[Parent effort](README.md#phase-4--bare-invocation-seam--handoff-plugin-side-landed-end-state-pending)
and [original acceptance](https://github.com/ThomasMichon/copilot-extensions/issues/542).

**Status:** Active; F1 source accepted in #11837. F2/F3 and release,
installed/native qualification remain open.
**Participant:** `patient-knob`, bounded Manager presentation/entry slice.
This is an agent-recommended implementation sequence under the existing effort,
not a replacement for #542 or a new configurator implementation.

## Intent and existing boundaries

Close Picker `first-run-onboarding-entry` and Installer
`onboards-from-empty-gracefully`: the same bare entry should present a useful
setup home before a project exists and Worktrees once a usable engine and
selected adopted project exist. Noninteractive invocation remains textual.
See the [Picker vision](../../../visions/picker/README.md#first-run-onboarding-entry),
[Installer vision](../../../visions/installer/README.md#onboards-from-empty-gracefully)
and [Configuration workbench](phase-5-configuration-workbench.md).

Explicit `picker` enters the production pending-binding screen. That screen
paints before engine discovery, but project-binding errors alone cannot
represent ordinary first-run states: `runner._select_project`
rejects an absent engine or empty/multiple project roster, while
`ProjectBootstrap` correctly requires a real nonempty project. Catching those
errors and inventing an empty project/source would bypass the engine contract.
Routing every bare TTY directly to that path is therefore insufficient.

Preserve the pending-first-paint and surviving-widget activation boundaries.
No operational Picker, worktree loader, project context, cwd switch or automatic
housekeeping exists before a real engine bootstrap is accepted.

| Scope | Existing owner retained |
|---|---|
| Standalone entry, no-project chrome, navigation, command receipts and re-entry | This bounded Manager slice |
| Core/prerequisite installation | Existing `setup_cli`, `core_install`, provisioner and authoritative installer |
| Repository acquisition, registration, trust and preparation | #356/#8545 and `repository_bootstrap` / engine repository setup |
| Machines, plugins and repository configuration | Existing contributed Configuration sections and native adapters |
| Knowledge selection, authentication and first-task/provider readiness | #10763/#10844, #3004/#3030 and existing native/Configuration owners |
| Engine bootstrap, New admission and operational Worktrees | Existing engine and production Picker owners |
| Runtime construction, native qualification and installed rollout | Existing owners; no authority transferred by this plan |

No new daemon, provider registry, binding writer, credential store or installer.
Do not enroll maintenance, acquire credentials, prepare payloads, repair services
or install anything merely to browse, refresh or obtain a status observation.

## Observable outcomes and order

- [x] F1: Bare interactive entry opens responsive, project-independent
  Setup/Doctor chrome, observes state, runs an explicitly selected read-only setup
  plan through the real Manager command, shows its complete result and can
  refresh or quit without side effects. Provisioned single-project entry binds
  and reaches the existing Worktrees view. This is a complete navigation and
  diagnostic flow, not completion of onboarding.
- [ ] F2: The home guides all original missing-source actions: Install core,
  Add machine, Adopt repo and Enable plugins. Compose available existing owners
  with explicit target/effects and their confirmation, trust and result contracts.
  An unavailable owner has a truthful supported next step, not an enabled action
  that silently does nothing or an invented substitute writer.
- [ ] F3: Complete the original empty/partial/provisioned TTY/non-TTY matrix,
  including recovery and actual supported provisioning/configuration handoffs.
  Reconcile the original #542 checklist before proposing closure.

F1 may land independently with its limitation stated. It must not close #542,
check off F2/F3, or claim a full setup experience from a screenshot or a unit test.
F2 needs concrete adapter reconciliation before implementation; this document
does not reserve their writers or prescribe unsupported command options.

## Entry, observation and navigation contract

Only an argument-free call changes its landing behavior. Preserve explicit
verbs, `--help`, `--version`, project-only routing, terminal-launch admission and
launcher JSON/stdout contracts. Require interactive stdin and a usable terminal
output channel for automatic TUI entry; pipes, missing/broken stream objects and
ACP-style redirected invocations retain legible textual status/next steps.
Reuse the existing stdout-to-stderr rendering rule where stdout is captured.

Paint the common shell before observing state. A Manager-owned no-project view
must be a real presentation state, not a fake `PickerBinding`, adopted project,
empty operational data source or second demo Picker. Keep setup accessible
when no engine or plugin contributions are installed.

Read only the existing installation/runtime resolution and registered-project
sources on a bounded worker. Reuse shared readers rather than cloning their
rules. Preserve absent, partial, ready, unavailable and malformed observations
as distinct results; an unreadable roster is not an empty roster. Optional
payload, PR, machine reachability or authentication probes must not gate project
discovery. Exclude knowledge-only entries using the existing repository role
rules; do not make a knowledge companion a standalone launch target.

| Observation | Landing / next action |
|---|---|
| Engine absent or incomplete | Setup home, missing-state explanation and Review setup plan |
| Engine available, no launchable project | Setup home, first-repository guidance through its owner |
| One launchable project | Request its existing engine bootstrap; activate only while still admitted |
| Several launchable projects | Select a project explicitly; do not guess from sort order or cwd |
| Registry unreadable/malformed or discovery failed | Visible diagnostic plus explicit retry; never silently offer first-time replacement |
| Selected engine too old or project bootstrap refused | Preserve the literal capability/refusal explanation and supported recovery |
| Engine/project ready but task prerequisite missing or unknown | Existing Worktrees/Configuration readiness and action-time admission remain authoritative |

Selecting a real project retries the original engine-owned bootstrap in the
same owner lifetime. Explicit Reload re-observes state; completing an owner
action re-observes relevant facts rather than setting a remembered "ready" bit.
Do not repeatedly auto-enter Worktrees after the user deliberately opens setup.
Cancel, dismissal and owner loss fence late results and prevent activation.
No probe loops, automatic retries or new background workers.

## Explicit diagnostic and action contract

F1's Review setup plan uses existing `worktree-manager setup` without `--apply`
or a repository/trust argument. The result view mounts before the bounded child
starts and retains stdout, stderr and actual exit status when observed. Timeout
reports retain bounded partial streams without inventing a normal exit. Failure is a failed
observation, not a partial install or success. Return/reload works after success,
failure and cancellation. Do not execute arbitrary rendered command strings.

Doctor is explicit, never a render-time/automatic startup probe. Reuse the
existing Manager diagnostic command/result owner without adding
`--apply-daemon-health` or changing its observation-versus-repair contract.
Honor that command's process cleanup, no-window, output and deadline boundaries.
Do not run it against shared native services during source tests.

Any later Apply is a separate, named operator action through the original
installer and its serialization. Show its target/effects and retain the actual
receipt, restart requirement and unsuccessful status; do not silently repeat an
unknown install. Repository setup must preserve source/account qualification,
chosen destination and explicit trust, not infer them from the selected project
or an onboarding acknowledgement. Knowledge/authentication uses the standing
sections once available, not a second first-run wizard.

Installer composition retains
[authoritative runtime replacement](../../../visions/installer/README.md#authoritative-runtime-replacement):
incoming readiness and all current selections define completion, independently
of incumbent recovery or outgoing retirement. Automatic inhibition does not
block explicit installation. Preserve user state and session keepers; optional
delayed cleanup stays with its existing central-only, current-generation owner.
This plan does not qualify existing installers as fully conformant or change
their independently tracked construction/replacement adoption gaps.

## Validation and promotion gates

- [x] Fail-first entry controls cover interactive bare, redirected stdin,
  redirected output with/without stderr terminal, absent/broken streams,
  ACP-style invocation, explicit verbs/project/help/version and launcher output.
- [ ] Production Textual tests prove chrome before slow discovery, empty and
  partial states, explicit project selection, unknown versus empty roster,
  retry/recovery, cancellation and no late activation after owner loss.
- [x] A real isolated Manager subprocess exercises Review setup plan and its
  result/return/reload path. Prove successful and unsuccessful status with the
  actual transport, not only a mocked button callback.
- [ ] Fresh isolated homes and strict before/after state comparisons prove that
  launch, browse, refresh, plan, diagnostic and cancel never install, adopt,
  rewrite source, bind knowledge, authenticate or start maintenance/services.
  Effect adapters and process tests use inert fixture-owned targets.
- [x] Preserve the provisioned production bootstrap/current-feed consumer and
  knowledge-only/project-selection exclusions. Do not broaden mock/capture paths.
- [ ] F2 adds focused real-owner handoff, confirm/cancel, failure, restart and
  reread controls for every supported missing-source action, preserving separate
  source writers and existing compatibility refusals.
- [x] For F1, run required focused suites, lint, install/documentation contracts and
  exact-head review/CI. Record clean-room first-touch and genuinely available
  live/native tiers separately, with explicit reasons when unavailable.

**Performance impact:** one first-paint shell and one bounded startup observation
per entry, refreshed only on explicit retry or relevant action completion.
No startup network, recursive scan, historical read, native mux CLI probe or
per-frame process is added. Reuse the existing pending/inbox lifetime boundary.
Count discovery/command work in deterministic controls; use the existing
[measurement contract](../../../docs/patterns/performance-measurement.md) for
actual visible/input-ready and key-to-result qualification. Synthetic mount
timing is not native latency proof. No source plan or merge proves installed
behavior, release promotion or rollout.

## F1 source implementation and validation

The bare-entry router requires interactive input and a terminal output channel.
The existing production app paints pending chrome before its worker observes
installation state and strict registry-owned project identity. No engine/project
means a genuine Setup / Doctor widget, not a fake bootstrap or operational data
source. One available project uses the existing bootstrap; multiple projects
require explicit selection. Selection re-observes the registry before bootstrap,
including removal or conversion to a knowledge-only repository.

The setup plan invokes the actual Manager CLI without apply/trust arguments.
Doctor consumes the existing Installation & Updates diagnostic action. Both use
the canonical finite caller-owned process primitive and existing result screen;
Return waits for settlement, preserves stdout/stderr and the exit code, then
re-observes state without automatic Worktrees entry. Cleanup uncertainty retains
its exact exception/custody and disables retry, rather than replaying a command.
The setup child retains the caller's selected installation-context pointer after
the general child-environment scrub; unrelated contributed commands retain that
scrub, and Doctor retains its owner-provided argv handoff. A timeout preserves
bounded partial stdout/stderr, including byte captures, without a normal exit
status.

Source controls exercised:

- The initial five-file packet passed 120 cases: startup text/entry, first-run
  observations, pending/operational binding, no-project navigation and existing
  production launch dispatch. The stale relocated-launch assertion now retains
  the existing `--first-launch` flag as well as the already-created worktree ID.
- After adding selection-time re-observation, the focused first-run and
  production-dispatch packet passed 80 cases.
- The final pending/Setup packet passed 16 cases, including real inert child
  success and Doctor-route failure receipts, terminal cleanup-custody refusal,
  owner cancellation and an actual fresh-home Manager setup plan. The plan
  returned exit 1 for missing prerequisites/core without changing the empty
  home; fixture PATH excludes host/native prerequisite commands.
- Ten focused TUI stream controls passed, including captured, absent and closed
  original stdout, stderr-terminal rendering and stdout restoration.
- The final 28-case model/Setup packet also drives actual bare `main` through
  the production runner and TUI into the real app's contained terminal driver,
  proving project selection, bootstrap refusal, Reload and Quit compose together.
- The scope/timeout packet's 31 cases passed across its selected packet and a
  focused rerun of the Windows namespaced report case. That case supplies a real
  namespaced-only observation to the home, executes the actual setup child and
  compares all fixture files/directories before and after. The initial core
  observation is recorded separately from UI dispatch, not treated as a native
  discovery latency budget. Portable scope transport, byte-output timeout and
  a real printing-then-stalling owned child cover the remaining corrections.
- Changed Python `F,E9` lint, the install-contract guard and headless-launch
  guard passed. No dependencies or version fields were changed.

These are source/contained-consumer proofs, not native visible/input-ready
latency or installed acceptance. A full installer clean-room/apply lane is not
selected because this flow performs no installation; fresh-profile first touch
is exercised directly. No admitted fixture-scoped live TTY venue was exercised,
and no shared native-service Doctor/probe or rollout was performed. Full partial
state/action/TTY qualification remains unchecked above; #542 remains open.

## F1 acceptance and next slice

#11837 merged to `dev` on 2026-11-10 as
`22be86107608ad89337a528a08533ec414537768`. Copilot approval
`5836169797` covers exact head `63bc96b642775623b6a81182008e7d53c723c76f`
with no active issues. Both scope/timeout threads are resolved, and all three
required checks passed before the managed merge. This is accepted source,
not release promotion or installed rollout.

Next: reconcile F2's concrete Install core, Add machine, Adopt repo and Enable
plugins adapters against their existing command, confirmation, trust, lifetime
and result contracts. Keep unavailable-owner guidance truthful, preserve the
same writers and obtain reviewed intent before changing an owner's boundary.
F3 and the original #542 checklist remain open.

## F2 owner reconciliation and bounded next delivery

The following is an agent-recommended refinement of the already-reviewed F2,
not acceptance of the original onboarding checklist. Inspection found no
generic no-project Configuration writer for machine creation or plugin
enablement. Do not equate an owner of a configuration domain with a shipped
interactive adapter for changing that domain.

| Missing-source action | Qualified existing surface | Scope, consent and lifetime |
|---|---|---|
| Install core | Manager `setup` / `setup --apply`, `setup_cli`, `core_install` and the prerequisite provisioner | Read-only plan is already composed. Apply may provision prerequisites, run the real core installer and require a new terminal. It needs a discoverable source checkout or bootstrap staging checkout. It is a synchronous owner command, not the durable target-update API; an absent engine cannot be installed by pretending it is an updatable target. |
| Adopt repo | Manager `register` / `setup --repository`, `repository_bootstrap`, engine `repos setup` | Choose a credential-free HTTPS source and absolute destination, and optionally a full commit and declared context. The dry-run prints the declaration only: it does not inspect or qualify remote setup. Actual acquisition/registration/setup retains native account, trust and refusal checks. Repository-setup trust is a separate explicit choice, never implied by opening a guide. |
| Add machine | Repository topology owner and agent-ssh's documented mesh/profile contracts | Current machine roster and ordinary SSH preferences are read-only presentation. `machine_config.py` selects an overlay root; it is not a machine-creation API. No Add-machine Configuration contribution is declared by agent-ssh. Topology changes use the owning repository's worktree/review flow; profile emission, transport installation, credentials and reachability remain separate explicit operations. |
| Enable plugins | Repository settings and the native plugin-installation/enablement workflow | The current Repositories Configuration contribution provides existing-knowledge binding and future launch preferences, not plugin enablement. Plugin source selection and settings changes retain their own trust and repository publication gates. Loading a payload is not runtime installation or service activation. |

**F2a: navigable owner guidance, first.** Add four clearly named, read-only
guides to the project-independent home, using the existing full-content screen
and return mechanics. Each guide states the actual owner, prerequisites,
effects, supported next step and unavailable in-app operation. Install core
continues to offer the real read-only setup plan. Repository instructions name
the supported Manager CLI and its declaration-only dry-run without claiming
registration or source qualification. Machine and plugin guides point to their
published owning references and preserve source-edit/review boundaries.
No rendered command is executed, no guessed owner command is offered, and
returning to the home does not fabricate a successful action or ready state.
Guides are available even if the engine, project or contributed pivots are
absent. They do not discover providers, open a browser, launch an agent or
write configuration merely to display help.

**F2b: effectful composition remains open.** Core apply and repository setup
have real synchronous CLI owners, but no qualified durable no-project TUI
handoff is established here. Do not run them under the diagnostic timeout,
convert observer loss into installer cancellation, or introduce a second
installer/job engine. Until their original owners provide or qualify that
handoff, disclose that the operator must use the named CLI outside the TUI,
retain its actual result and restart instructions, then explicitly Reload.
Machine creation and plugin enablement likewise remain unavailable as in-app
mutations, rather than enabled no-op actions. The original owner issues and
Phase 5 retain that work; F2a alone does not check off F2 or F3.

**F2a acceptance:** production Textual navigation must exercise all four
guides, their return/cancel paths and compact-terminal scrolling. Compare
fixture state before and after browsing, returning and Reload; assert no
command/provider runs merely to open a guide. Exercise the core guide's real
setup-plan route and actual unsuccessful receipt without apply, repository or
trust flags. Preserve project selection and the explicit Doctor route. The
original effectful, fresh-install, native and installed gates remain open;
source-only guide tests cannot satisfy them.
