# Custom Context Aggregator Retirement

- **Slug:** `custom-context-aggregator-retirement`
- **Repo:** copilot-extensions
- **Branch(es):** sequenced plan, upstream retirement, and adopter-migration changes
- **Created:** 2026-09-07
- **Status:** Active
- **Vision:** closes
  [`visions/harness-guidance`](../../../visions/harness-guidance/README.md)
  §Non-Goals/`no-custom-cross-plugin-aggregation-authority`
- **Umbrella issue:** [#2173](https://github.com/ThomasMichon/copilot-extensions/issues/2173)

## Guiding Intent

Retire the custom session-context aggregation authority now that plugins have a
reliable checked-in pointer plus exact-session guidance-file path. Keep the
portable guidance contract independent of one cross-plugin rendezvous, cache,
or spill engine. When the host natively composes every plugin's
`additionalContext`, direct plugin-owned contributions may become the preferred
perfectly dynamic path after version-floor proof, without restoring a custom
authority.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Architecture driver | Vision, pattern, compatibility boundary, and reviewed sequencing | Isolated worktree |
| Producer migration lane | Remove aggregate-only hooks and preserve exact-session writers | Sequenced implementation commits |
| Validation lane | Scanner, plugin suites, clean-room launch paths, and adopter contract | Disposable fixtures |

## Coordination

- **Topology:** one reviewed plan PR followed by short serial implementation
  PRs.
- **Host (owns PRs):** architecture driver.
- **Delegates:** bounded inventory or validation may be delegated; the host
  integrates all changes and owns the public surface.
- **Handoff:** each implementation PR must leave the suite valid without
  requiring a later PR to restore session startup.

## Context

The completed
[Session Context Aggregation](../../2026/08/31%20session-context-aggregation/README.md)
effort built a deterministic compatibility authority for host releases that
discarded independently correct `sessionStart` `additionalContext` values. The
later
[Session-Scoped Dynamic Guidance](../../../docs/patterns/session-scoped-dynamic-guidance.md)
pattern established a more reliable baseline: a checked-in pointer directs the
agent to a plugin-owned file written beneath the exact session root.

The custom authority now duplicates responsibility across every producer:
source-qualified resolvers, wrapper copies, contributor declarations,
rendezvous state, aggregate admission, cache/spill behavior, and an
aggregate-specific clean-room witness. Native runtime composition remains the
right long-term dynamic mechanism, but it is a separate host capability and
does not justify retaining this compatibility layer.

## Request

> Remove the custom context aggregation path in favor of the exact-session
> guidance model. Preserve native multi-hook `additionalContext` as the
> preferred future dynamic path once the runtime fix is available and proven.

## Plan

### Phase 1 — Land the retirement contract

- [x] Revise the harness-guidance vision with an explicit boundary against a
  custom cross-plugin aggregation authority.
- [x] Record the retirement scope, native-composition future seam, ordering,
  and validation plan in this effort.
- [x] Land the vision and effort through review before removing code
  ([PR #2176](https://github.com/ThomasMichon/copilot-extensions/pull/2176)).

### Phase 2 — Remove the custom authority

- [x] Remove the `context-injection` plugin, marketplace entry, adoption
  config, aggregate-specific pattern/reality claims, and clean-room scenario.
- [x] Remove aggregate-only producer hooks, wrappers, resolvers, and
  declarations while retaining checked-in pointers, exact-session guidance
  writers, and the underlying emitters those writers call.
- [x] Update scanner and suite guards so a session-file-only stack is valid
  without an aggregate authority and ambiguous non-empty startup outputs still
  fail closed.
- [x] Audit every marketplace plugin's startup behavior: dynamic ambient
  guidance uses a checked-in pointer plus exact-session writer, static-only and
  bootstrap-only hooks are explicitly output-free, and no plugin silently loses
  a previously delivered command or policy surface.
- [x] Bump every changed plugin version and reconcile the marketplace/docs
  roster in the same change.

### Phase 3 — Validate and migrate adopters

- [x] Deferred to `#2160 / #2182 / #2183`: Run changed plugin suites, repository guards, install-contract checks,
  and static searches for active authority surfaces.
- [ ] Prove fresh/resume/ACP launch paths still deliver exact-session guidance
  with no stale session or CWD reuse.
- [x] Publish migration guidance for adopters to remove the authority plugin
  and config without removing their checked-in pointers.
- [x] Keep native multi-hook activation outside this effort until the supported
  runtime floor proves complete composition.

## Validation Plan

- [x] No marketplace plugin, active config, hook, wrapper, resolver,
  rendezvous/cache/spill code, or aggregate-specific scenario remains.
- [x] Every retained `sessionStart` hook emits one JSON object; the scanner
  reports no ambiguous non-empty startup output and requires no custom
  authority.
- [x] The full marketplace roster passes one suite guard that derives expected
  startup behavior from plugin manifests and projections rather than a
  hand-maintained adopter subset.
- [ ] Checked-in pointers and exact-session guidance writers remain
  deterministic, contained, resume-safe, and cross-platform.
- [x] Deferred to `#2160 / #2182 / #2183`: Changed plugin suites, repository consistency guards, lint, and install
  contract pass.
- [x] Public docs identify native host composition as the only future
  multi-plugin dynamic preference and require version-floor proof before
  activation.

## Proposal

Use one current reliability path and one future convergence path:

1. **Current:** reviewed static pointer plus plugin-owned exact-session guidance
   file.
2. **Future:** direct plugin-owned `additionalContext`, composed by the native
   host only after supported-version proof.

There is no third, custom composition authority between plugins and the host.

## Journal

### 2026-09-07 — Kickoff

- Issue #2173 was claimed as the public retirement tracker.
- The removal is an explicit negative boundary, not an inference from silence:
  the harness must not depend on a custom cross-plugin aggregation authority.
- Native host composition remains the preferred future dynamic path after its
  supported-version validation gate.

### 2026-09-07 — Custom authority removed

- Removed the `context-injection` plugin, repository adoption config, aggregate
  pattern, clean-room scenario, marketplace entry, and all producer-side
  wrappers and authority resolvers.
- Retained plugin-owned static pointers, exact-session guidance writers, and
  the underlying emitters those writers call. Empty `session-context.json`
  declarations now classify mixed writer/bootstrap hook lists as output-free;
  they carry no contributors or aggregation behavior.
- Updated customization scanning to accept output-free stacks without a custom
  authority and to continue blocking ambiguous non-empty startup outputs. The
  source repository now explicitly disables the retired plugin against any
  user-global enablement and checks in its own declared fallback projections.
- Integrated validation: payload generation, runtime sync, install contract,
  bootstrap sync, runtime resolution, marketplace isolation, version
  consistency, docs consistency, and the strict settings-aware customization
  scan all pass. The strict scan reports an `output-free-stack` with zero
  blocking findings.
- The complete changed-plugin run recorded 4,009 passed, 43 skipped, and nine
  failures. Five pre-existing Windows container private-state/rescue failures
  remain tracked by #2160; three Codespaces installer-recovery failures were
  filed as #2182; one consistently reproduced managed-index lifecycle
  assertion was filed as #2183. None of the failing test files or managed
  runtime code changed in this retirement.
- The operator strengthened the acceptance gate from adopter consistency to the
  complete marketplace: vision, patterns, authoring guidance, customization
  scanning, review enforcement, and every plugin's actual startup behavior must
  agree on the new best practice whether or not one downstream currently
  enables that plugin.
- Added
  [`reviewing-customizations`' Session Guidance Conformance Runbook](../../../plugins/customizing-copilot/skills/reviewing-customizations/references/session-guidance-conformance.md)
  for both adopter repositories and plugin-suite repositories. It drives a
  complete settings/marketplace-derived inventory, authority disablement,
  projection sync, output classification, blind launch probes, and the future
  native-composition gate.
- Migrated `agent-codespaces`, `agent-vault`, and `agent-index`, which had lost
  their command catalogs when aggregate hooks were removed, to the same
  checked-in pointer plus exact-session writer pattern. `agent-worktrees`
  retains its equivalent writer inside `hook_client.py`.
- Strengthened roster-wide review enforcement: payload coverage derives
  runtime expectations from the marketplace and special-cases only the
  existing agent-worktrees hook client; the harness guard derives dynamic
  pointer/writer requirements from every plugin's manifests and projection
  templates. The payload guard passes 14 tests; focused harness,
  customization, SSH, Vault, and Index suites pass 1,048 tests with 96 skips.

### 2026-09-07 — Output-free bootstrap and scanner hardening

- Audited the full 21-plugin marketplace roster. Fourteen plugins still
  register `sessionStart` commands; seven (`efforts`, `visions`,
  `customizing-copilot`, `copilot-extensions-harness`, `wsl-setup`,
  `harness-knowledge`, and `delegation-guidance`) do not and needed no startup
  command audit.
- Fixed stdout-contract leaks across every retained operational
  bootstrap/registration path that still wrote human text to stdout or returned
  an empty stdout stream: `bootstrap-check.{sh,ps1}` in `agent-bridge`,
  `agent-codespaces`, `agent-containers`, `agent-dispatch`, `agent-logger`,
  `agent-machines`, `agent-mcp`, `agent-ssh`, `agent-vault`, and
  `budget-guidance`; `register-bridge-provider.{sh,ps1}` in
  `agent-codespaces` and `agent-containers`; and
  `register-dispatch-companion.{sh,ps1}` plus the matching hook wrappers in
  `agent-index` and `agent-ssh`. Each path now emits exactly one JSON object
  (`{}` here), keeps diagnostics on stderr, and preserves the prior side
  effects.
- Audited but did not need to change the exact-session writer / hook-client
  paths in `agent-worktrees`, `ai-attribution`, `context-handoff`,
  `agent-bridge`, `agent-codespaces`, `agent-containers`, `agent-dispatch`,
  `agent-index`, `agent-logger`, `agent-machines`, `agent-mcp`, `agent-ssh`,
  and `agent-vault`; their writer wrappers and underlying Python writers were
  already JSON-clean once the direct operational scripts were fixed.
- Hardened `scan-customizations.py` so `sessionStart` output-free certification
  is no longer name-only. It now resolves the actual payload-local script path
  from each hook command, statically proves the script shape (JSON guard/trap or
  writer wrapper plus JSON-only stdout behavior), follows editable
  `copilot-extensions` source footprints instead of blindly trusting installed
  payload identity, and rejects trusted-name scripts that still print plain text
  to stdout.
- Added scanner regression coverage for both sides of the contract:
  JSON-only named `bootstrap-check` scripts remain `proven-output-free`, while
  a trusted-name `bootstrap-check` that writes plain stdout is now rejected.
- Validation this pass:
  1. Isolated stdout/stderr contract audit: 56/56 scratch runs passed across
     both shells and the modified bootstrap/registration branches.
  2. Targeted plugin suites: 709 passed, 39 skipped, 0 failed across
     `agent-bridge`, `agent-codespaces`, `agent-containers`, `agent-dispatch`,
     `agent-index`, `agent-logger`, `agent-machines`, `agent-mcp`,
     `agent-ssh`, `agent-vault`, `budget-guidance`, and
     `customizing-copilot`.
  3. Roster/guard checks: payload generation (12 manifests), payload coverage
     (14 passed), runtime sync (12 plugins / 11 resolvers), install contract
     (12 plugins), bootstrap sync (12 runtime plugins / 7 families), strict
     runtime resolution, marketplace isolation (report-only baseline, 791
     findings), version consistency, docs consistency (21 plugins: 12 runtime /
     9 payload-only), installer-readiness (46 passed, 1 skipped), and the
     strict settings-aware customization scan (`blocking: 0`,
     `session_context.disposition: output-free-stack`) all passed.
