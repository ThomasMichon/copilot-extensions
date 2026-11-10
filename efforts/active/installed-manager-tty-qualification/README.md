# Installed Manager TTY Qualification

- **Slug:** `installed-manager-tty-qualification`
- **Repo:** copilot-extensions
- **Branch(es):** reviewed proposal, then implementation PRs against `dev`
- **Created:** 2026-11-07
- **Status:** Done
- **Vision:** [release qualification](../../../visions/release-pipeline/README.md#configured-feed-construction-and-first-launch), [fresh-machine behavior](../../../visions/clean-room-validation/README.md#fresh-machine-fidelity), [authoritative replacement](../../../visions/installer/README.md#authoritative-runtime-replacement)
- **Umbrella issue:** #11377
- **Related owners:** #3336 release control; #7529 configured-feed construction; #5579 installed launcher chain

## Guiding Intent

Promotion must not publish a candidate that installs successfully but cannot
open the Manager's interactive terminal through an adopted project's binstub.
The installed consumer, including its terminal admission and input handling,
is the acceptance boundary. This is a bounded extension of installed-entry
qualification, not a return of the general integration portfolio to unit CI.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Qualification coordinator | Plan, integration, validation and PR ownership | Managed source `threaded-ledger` |
| Native witness contributor | Isolated installation and real terminal consumer witness | Same source branch; coordinator-mediated assignment |
| Promotion contributor | Existing release-lane integration and evidence admission | Same source branch; coordinator-mediated assignment |
| Main activation coordinator | Workflow-only controller preparation and reviewed adoption | Managed source `quiet-sprocket`; same PR owner |

## Coordination

- **Topology:** one Dev implementation worktree and a separate workflow-only Main
  activation worktree; Dev contributors have disjoint edit ownership.
- **Host (owns PRs):** Qualification coordinator; contributors do not commit,
  rebase or publish independently.
- **Delegates:** Native witness contributor owns the selected native fixture;
  Promotion contributor owns selected workflow/control and gate tests.
  Exact files are agreed before edits.
- **Handoff:** return tested artifacts and exact candidate/evidence identities
  to the coordinator. Keep release-control, native execution, construction and
  lifecycle owners; do not supersede another contributor's open PR.

## Context

A project launcher discovers its registered Manager and checks its usability.
The installed Windows Manager launcher can perform a native terminal capability
query even for a version request. Failure there prevents the interactive
front door from reaching the TUI, despite an existing installation completion
record. A version-only or construction-only witness misses the requested
install-to-interaction contract.

The existing release vision already admits bounded construction/installed-entry
and native Windows consumer qualification. This slice extends that owner rather
than creating another release publisher, test scheduler or unit-test category.
The installed-launch witness must address the exact frozen candidate and
configured execution inputs that the existing promotion owner will publish.

## Request

Verbatim operator requirement:

> We must block promotion if launch after install fails, so we need a test that actually does install, with a representstive harness repo, and then launches the worktree-manager via the binstub, and ensure it loads the TTY.

## Scope Approvals

This request authorizes the bounded source test and promotion-gate change.
It does not authorize changes to an operator installation, queue recovery,
runner enrollment, service restarts, security/feed policy or credentials.
Any separately authorized live rollout remains a separate objective.

## Plan

The following realization details are **agent-recommended**.

### Phase 1 - Review the consumer and gate contract

- [x] Identify the existing actual-install/native-terminal fixture owners and
  supported Windows qualification lane; agree exact implementation file ownership.
- [x] Review this proposal and the narrowly extended installed-entry vision
  before implementation.

### Phase 2 - Prove actual installed terminal readiness

- [x] Install the candidate through its authentic installation owner into fresh,
  isolated state with a representative generic adopted harness repository.
- [x] Invoke that repository's generated binstub through the supported Windows
  terminal path, reaching the installed Manager rather than a source import
  or a substituted direct module invocation.
- [x] Assert real visible TUI readiness and input response, then close the
  fixture through its owner and verify bounded descendant cleanup.
- [x] Add a falsifying control where installation succeeds but installed launch
  fails; the witness must fail without retries or improvised repair hiding it.

### Phase 3 - Enforce the result before promotion

- [x] Reuse the supported native qualification lane with evidence bound to the
  frozen candidate, installation inputs and actual terminal consumer.
- [x] Refuse promotion for failed, timed-out, missing, skipped, incomplete or
  stale qualification; cover these decisions with bounded in-process tests.
- [x] Document invocation, input/evidence identity and failure ownership; retain
  the existing unit coverage, source integrity and publication gates.
- [x] Validate and land the implementation through normal review, and verify
  actual gate activation without claiming an unperformed host rollout.

## Validation Plan

- [x] The actual installer constructs a fresh fixture; it does not reuse the
  operator's selected runtime, caches as installation state, or private repository.
- [x] The exact generated project binstub launches the installed Manager under
  a real supported Windows TTY and the observed UI responds to a test input.
- [x] Positive and install-success/launch-failure controls exercise the actual
  consumer; process existence, `--version`, mount callbacks or synthetic ready
  files cannot satisfy the TTY assertion.
- [x] Timeout, cancellation and failure retain diagnostics and settle only
  fixture-owned resources without altering host daemons, sessions or selections.
- [x] Gate decision tests reject stale candidate identities and every missing
  or non-success result; the live/native witness is not reclassified as unit proof.
- [x] A supported promotion run or the existing owner's equivalent exact-candidate
  qualification demonstrates that this gate actually blocks publication.
- [x] Source review, required CI and installation-contract checks pass; any
  unavailable validation tier or actual infrastructure blocker is explicit.

## Proposal

Proposed: preserve the existing bounded installed-entry/native qualification
architecture and strengthen its consumer contract to actual project-binstub
TTY readiness. Exact lane and fixture integration follows the bounded owner
inventory; unsupported execution must not be reported as a successful gate.

## Journal

- [2026-11-10 - Gate activation and frozen-candidate release](journal/2026/11.10%20Gate%20activation%20and%20frozen-candidate%20release.md)
- [2026-11-09 - Fixture release and activation preparation](journal/2026/11.09%20Fixture%20release%20and%20activation%20preparation.md)
- [2026-11-07 - Implementation and native qualification](journal/2026/11.07%20Implementation%20and%20native%20qualification.md)
