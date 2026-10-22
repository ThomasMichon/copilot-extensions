# CodeSpaces Authentication CTA

- **Slug:** `codespaces-authentication-cta`
- **Repo:** copilot-extensions
- **Branch(es):** independent reviewed PRs targeting `dev`
- **Created:** 2026-10-22
- **Status:** Active
- **Vision:** vision-closing: `visions/picker` (graceful capability scaling,
  render/derive rather than own, headless assertions) and
  `visions/plugins/agent-codespaces` (per-repo identity)
- **Umbrella issue:** #7793
- **Related issue:** #4180 (broader account-aware operation preflight; not claimed here)

## Guiding Intent

An unavailable CodeSpaces credential must be a visible, actionable state, not
an apparently ready empty pool. Only an explicit operator action may start
vendor authentication. The provider owns credential selection and acquisition;
the optional Manager renders that state and drives the provider across its
existing process boundary.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| CTA implementer | Plan, provider, Manager, tests and reviewed landing | Managed source handle `whirring-axle`; #7793 |

## Coordination

- **Topology:** plan PR first, implementation PR second.
- **Host (owns PRs):** CTA implementer.
- **Delegates:** none.
- **Handoff:** durable journal and #7793; deployment remains with the adopting operator.

## Context

The canonical `agent-codespaces pool --picker-json` contribution already emits
the #980 summary banner. Its best-effort probe currently suppresses failures.
The production Manager and its lightweight preview have different render/action
paths; both must remain coherent. Existing account mapping and CodeSpace bindings
remain authoritative. No operator override, live configuration or installed
runtime is changed by this effort.

Patterns reconciliation: preserve independently usable providers, right-sized
on-demand processes, attributable commands, visible failures, cross-platform
headless spawning and existing installation context. No new daemon, private
broker dependency or Manager-owned credential store is introduced.

## Request

Public-safe summary of the requested scope: show Sign in / Authorize in the
CodeSpaces pivot when credentials or the `codespace` scope are missing. A user
click alone permits a nonblocking vendor OAuth/browser/device ceremony on the
consuming device. Support cancel, error, success and refresh. Never authenticate
from render/list, switch the global `gh` account, use a PAT, export tokens into
the session environment, or add a repository opt-out. Exercise the actual
headless widget/action with mocked authentication and land through review/CI.

## Plan

### Phase 1 - Reviewed contract
- [x] Land this proposal before adding an authentication acquisition owner.

### Phase 2 - Provider and Manager
- [x] Publish structured `auth_required`, `scope_blocked`, `ready` or probe-error
      status, with selected account and actionable banner, in both one-shot and
      streaming pool results. Never equate probe failure with authenticated empty.
- [x] Add a provider-owned explicit authorization command. Reuse the vendor
      GitHub CLI OAuth registration/device protocol and account-selection primitives; isolate its
      configuration from global active-account selection, verify the resulting
      login before using it, retain credentials only on the consuming device,
      and expose only progress/device instructions, never tokens.
- [x] Add a declarative pivot-level action usable without a selected row and a
      cancellable, nonblocking Manager modal. Bind action context to the
      provider's account/status, preserve command provenance, refresh on success,
      and show terminal cancellation/error without fake success.
- [x] Keep the production UI, preview UI and provider protocols coherent;
      preserve unrelated plugin pivots and existing pool behavior.

### Phase 3 - Land and record
- [x] Add focused provider and real Textual headless tests; update directly
      related docs and product changefiles; pass lint and install contracts.
- [ ] Obtain independent review through the normal Copilot review path, address
      blocking findings, pass required CI and merge the implementation.
- [ ] Journal actual merged results and release state; transfer deployment and
      native authorization acceptance explicitly to the adopting operator.

## Validation Plan

- [x] Mock missing credentials, missing scope, unrelated failed accounts,
      indeterminate probes, and account mismatch; prove no read starts OAuth.
- [x] Assert actual widget/button/action wiring for blocked empty views, modal
      responsiveness, cancellation, failure, success and provider refresh.
- [x] Verify one-shot and streaming authentication fields agree and streaming
      updates do not retain a stale successful authentication status.
- [x] Verify no global account switch, token output or session-environment
      mutation; test the vendor command/config boundary with synthetic inputs.
- [ ] Run bounded isolated targeted suites, targeted Ruff, install-contract and
      required publication checks. Do not perform a real OAuth ceremony.

## Proposal

Implementation may refine the smallest declarative action/progress seam after
review; account ownership, click-only consent and failure visibility are fixed
requirements, not optional implementation details.

## Journal

### 2026-10-22 - Contract
- Claimed #7793 after checking related account-aware preflight work.
- Confirmed the canonical banner and distinct production/preview paths.
- No vendor authorization, credentials or live configuration were changed.
- Plan PR #7794 merged after a current-head Copilot approval and passing checks.
  Implementation began only after pulling forward onto the reviewed plan.
- The vendor ceremony uses isolated `gh` configuration plus login/scope
  verification; existing per-process account pinning consumes verified results.
  No reusable authentication broker exists in the provider, so no optional
  sibling or adopter-private broker is imported.
- Source validation: 258 provider/CLI/lifecycle tests and 244 Manager
  contract/production-widget/preview tests pass in isolated bounded runs.
  Tests drive actual clicks, visible device instructions, cancel/error/success,
  provider refresh, account mismatch and credential adoption with mocked vendor
  boundaries. Install and headless-launch contracts pass.
- Native OAuth and Windows credential-store/focus acceptance remain explicitly
  operator-run; no real authorization or runtime rollout was performed.
- Validation also covers the shared context decorator's single-argument handler
  contract, with the annotation callback passed only to the pool. Inventory
  subprocess timeouts now produce error snapshots and recover on the next
  subscription tick. The stream consumer publishes summary changes after an
  empty initial snapshot without changing its row-arrival/reconnect budget.
  Non-progress view commands pass through the same confirmation gate as
  progress commands; a real headless click verifies Cancel-by-default.
- Vendor-source inspection confirmed `GH_CONFIG_DIR` does not isolate the
  shared OS keyring: `gh auth login` activates a shared unkeyed credential, and
  file-storage mode still deletes that slot. The provider's on-demand owner
  therefore uses GitHub's device endpoints with the vendor's public application
  identifier, verifies identity/scope in memory, and publishes to a provider-owned,
  installation-qualified keyring namespace (private-file fallback on POSIX).
  It never calls the mutable vendor login command or touches GitHub CLI keyring
  slots. Windows refuses storage failure rather than falling back to an
  unprotected file. This is a storage-mechanism correction
  within the reviewed provider-owned contract, not a new broker or daemon.
- One shared candidate selector now serves authentication and inventory,
  including mapped-only accounts. Pointer activation is constrained to the
  actual button row; blank-row clicks are explicitly non-authorizing.
- An unknown ambient identity reports selection error, not missing credentials.
  Provider-only candidates do not consult an unrelated global status probe, and
  each inventory request reads current provider credentials without memoization.
  Preview command buttons reject form/internal actions and bind through the
  same payload-root resolver as production. Tests execute a benign contributed
  provider with a conflicting provider first on PATH and refuse missing owned
  commands rather than falling through to that peer.
- Command rewriting validates argv shape before filesystem resolution. Scalar,
  mixed-type and embedded-NUL argv across list, row, view, worktree and
  configuration surfaces produce isolated invalid-contract findings; a valid
  peer remains available in every regression case.
