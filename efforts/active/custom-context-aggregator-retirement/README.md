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
### 2026-09-07 — PR #2194 merged: six review findings closed

- Consumed a handoff to resume Phase 11 and closed the six remaining
  high-confidence findings from the independent review before landing:
  `agent-worktrees` hook_client.py sessionStart now folds decision/lifecycle
  context into the exact-session guidance file and always emits `{}` (with a
  follow-up fix moving that write outside the `try` block so an unhandled
  exception still leaves valid stdout — flagged by the PR's own automated
  review); `agent-codespaces` and `agent-index` guidance writers now also
  compose `emit-codespace-map` / `emit-scope-binding` using the authoritative
  payload `cwd`; the full-roster bootstrap/output-free audit and scanner
  hardening (recorded above); authoring docs (`authoring-skills/SKILL.md`,
  `docs/patterns/README.md`, `docs/harness-runbook.md`) rewritten to the
  exact-session-writer model; and `agent-ssh` now requires a validated
  absolute/existing payload `cwd` before running the repository-sensitive
  mesh-pointer producer, never falling back to ambient process `cwd`.
- Opened [PR #2194](https://github.com/ThomasMichon/copilot-extensions/pull/2194)
  and iterated through five review rounds before merging (squash, self-merge
  per the `pr-self-merge` profile — GitHub authors cannot approve their own
  PRs):
  1. `check-version-bump` guard failure: `agent-mcp` content changed without a
     version bump; bumped to `0.2.0-dev100` across `plugin.json`,
     `pyproject.toml`, `__init__.py`, and `marketplace.json`.
  2. `libs/payload-invocation/tests` (`test_generate.py`,
     `test_worktrees_catalog.py`) and `agent-dispatch`'s own
     `test_payload_invocation.py` asserted the retired direct
     `emit-command-catalog` sessionStart wiring; updated all three to accept
     the exact-session-writer wiring (a `write-session-guidance`/`hook_client.py`
     hook that composes `emit-command-catalog` internally) as an equally valid
     contract.
  3. The PR's own Copilot review flagged `hook_client.py` could still produce
     no stdout on an unhandled exception during `sessionStart`; moved the
     unconditional `{}` write outside the `try` block, and restored the
     scanner's literal `sys.stdout.write("{}")` static-proof pattern (my first
     fix used a variable-assignment style that satisfied exception-safety but
     broke `scan-customizations.py`'s hardened content proof, regressing the
     marketplace-wide `output-free-stack` disposition to
     `single-possible-output`). Added a dedicated regression test
     (`test_session_start_main_emits_empty_object_on_unhandled_exception`).
  4. Regenerated the stale `agent-dispatch` instruction projection lock/marker
     (`.github/copilot/context-projections.json` +
     `.github/instructions/agent-dispatch/session-guidance.instructions.md`)
     via `manage-instruction-projections.py sync .` after the version bump.
  5. The PR's review also flagged `bootstrap-check.sh`/`.ps1` in
     `agent-worktrees` for emitting an `additionalContext` JSON object from the
     no-python-available last-resort fallback, inconsistent with this effort's
     own "sessionStart always emits `{}`" contract; routed that setup hint to
     stderr and always emit `{}` there too, accepting that the ultra-rare
     no-Python fallback no longer surfaces the hint as model context (only as
     an operator-visible diagnostic).
- Final review: 0 findings, "Findings: None" with 2 prior findings marked
  resolved. Merged and reconciled the worktree onto `origin/main`
  (`agent-worktrees pr-complete`).
- Still open before this effort reaches Done: the fresh/resume/ACP launch-path
  proof (Phase 3) and the cross-platform determinism/resume-safety validation
  item remain unchecked below.

### 2026-09-07 — Windows launch-path proof passed; WSL parity blocked

- Ran blind `sessionStart` probes against the actual `hook_client.py` /
  `write_session_guidance.py` entrypoints for `agent-worktrees`,
  `agent-codespaces`, `agent-index`, and `agent-ssh`, using synthetic
  session IDs plus hidden high-entropy canaries injected through the
  payload-sensitive guidance contributors and pre-seeded stale guidance files.
  The Windows/PowerShell path proved fresh-launch session scoping, resume
  overwrite of stale guidance, payload-`cwd` ownership (no ambient `cwd`
  reuse), and fail-open behavior when no prior guidance file or matching
  session snapshot existed. The same entrypoint reads also confirmed no
  launch-mode branch skips guidance writing for fresh, resume, prompt, or ACP
  shapes: each path consumes the same `sessionStart` payload and emits `{}` on
  stdout.
- The proof uncovered one real regression outside the writer logic:
  `agent-index`'s compatibility `bootstrap-check.{ps1,sh}` still exited with an
  empty stdout stream instead of the required single JSON object. Fixed both
  wrappers to emit exactly `{}` and added a regression test covering the bash
  and PowerShell entrypoints.
- Re-ran the Windows-side wrapper checks after that fix. `bootstrap-check.ps1`
  passed for all four plugins, and the `agent-codespaces`
  `register-bridge-provider.ps1` plus `agent-index` / `agent-ssh`
  `register-dispatch-companion.ps1` wrappers remained deterministic, contained,
  and idempotent across repeated runs.
- The remaining cross-platform/bash validation is still blocked. Two attempts to
  run the WSL parity pass through the configured bridge failed before the remote
  session processed any turn: ACP session launch timed out during the remote
  Copilot startup handshake. Until that bridge path succeeds, the Phase 3
  launch-path checkbox and the cross-platform validation-plan checkbox remain
  intentionally unchecked and the effort stays Active.
