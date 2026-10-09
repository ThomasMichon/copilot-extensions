# Vision Backport Sweep

- **Slug:** `vision-backport-sweep`
- **Repo:** copilot-extensions
- **Branch(es):** per-slice (each session/slice opens its own PR branch; no
  shared long-lived branch — see Coordination)
- **Created:** 2026-10-05
- **Status:** Done <!-- source reconciliation; implementation owners remain open -->
- **Vision:** `visions/README.md` (the whole index — a repo-wide sweep,
  not one item); scoped so far:
  `visions/plugins/agent-dispatch/tasks-pane-ux` §Concepts/the-suspended-task-waiter,
  `visions/plugins/agent-dispatch/reviewer` §Behaviors/stagnation-escalates,
  `visions/plugins/agent-dispatch` §Behaviors/inherits-runtime-service-invariants,
  `visions/plugins/agent-codespaces` (repo-sourced-provenance,
  in-venue-plugin-injection, dual-mode-session-reach),
  `visions/remote-interactive-sessions` §Purpose & Intent (stale-framing
  correction, carried by the same slice's PR review),
  `visions/plugins/agent-ssh` (local-process reach, fragment provenance,
  mesh self-healing, machine maintenance escalation, venue-contract-reach),
  `visions/plugins/agent-containers` (interactive-venue-reach-trusted,
  restricted-venue-picker-discovery, host-backed persistence),
  `visions/plugins/agent-worktrees/pull-requests` (confirmed realized;
  closed 2 stale issues, narrowed 1), `visions/plugins/agent-index`
  (per-source git ref override + authenticated fetch, multi-project corpus
  grafting, shared dropin-registry library),
  `visions/plugins/context-handoff` (durable baton/pickup separation,
  extension-independent recovery, objective authority, evidence-faithful
  continuation, safe background-work transfer, attributable exclusive pickup),
  `visions/plugins/agent-bridge` (observation/attribution and generation-handoff
  and control slices), `visions/plugin-services` (shared transition authority),
  `visions/agent-fabric` (venue/memory ownership seams)
- **Umbrella issue:** `ThomasMichon/copilot-extensions#5456`
- **Sub-issues:** `ThomasMichon/copilot-extensions#5356` (plugin-services
  conformance gap, pre-existing, now vision-linked) ·
  `ThomasMichon/copilot-extensions#5452` (tasks-pane-ux delegated/relay
  waiter-surfacing gap) ·
  `ThomasMichon/copilot-extensions#5468` (agent-logger: in-place
  same-version rewrite, needs the refuse-or-new-generation fix) ·
  `ThomasMichon/copilot-extensions#5472` (consolidated: every `agent-*`
  plugin's immutable-versioned-runtime conformance gap for a same-version,
  content-changed update — agent-vault/agent-codespaces/agent-worktrees/
  agent-mcp/agent-index's unsafe races, agent-bridge's lock-safe-but-still-
  non-conforming rewrite (the only plugin that's actually lock-safe), and
  agent-ssh/agent-containers/agent-machines' unconditional in-place
  reinstall; filed and expanded this slice) ·
  `ThomasMichon/copilot-extensions#5683` (automatic force-tier background-work
  transfer safety) · `ThomasMichon/copilot-extensions#5684` (consume drops
  failed succession-promotion outcomes) ·
  `ThomasMichon/copilot-extensions#5750` (failed new-version cutover falls back
  to stop/start) · `ThomasMichon/copilot-extensions#5754` (recovery runs before
  acquiring lifecycle-transition authority) ·
  `ThomasMichon/copilot-extensions#5791` (explicit scoped gh identity degrades
  to ambient execution after token minting fails) ·
  `ThomasMichon/copilot-extensions#5833` (distinct attributed raw head override
  is not surfaced by the inspected Mux Companion UI) ·
  `ThomasMichon/copilot-extensions#5842` (claim acceptance releases source
  responsibility before consumer commit) ·
  `ThomasMichon/copilot-extensions#5843` (lost carrier responses can repeat
  admitted mutating operations) ·
  `ThomasMichon/copilot-extensions#5851` (source build/cleanup stages mutate
  repository checkouts) ·
  `ThomasMichon/copilot-extensions#5852` (ambient installer project inference
  changes repository hooksPath) ·
  `ThomasMichon/copilot-extensions#5853` (installation overrides explicit
  Copilot experimental preference) ·
  `ThomasMichon/copilot-extensions#5855` (semantic config migration overrides
  an explicitly disabled idle reaper) ·
  `ThomasMichon/copilot-extensions#5861` (fleet named deployment/config
  reconciliation follow-on) ·
  `ThomasMichon/copilot-extensions#5862` (fleet subscribed observation/recovery
  follow-on) ·
  `ThomasMichon/copilot-extensions#5868` (test-environment preparation precedes
  containment and aggregate budgets) ·
  `ThomasMichon/copilot-extensions#5869` (ignored sandbox cleanup failures can
  preserve a successful result) ·
  `ThomasMichon/copilot-extensions#5877` (named scoped noncredential
  host-resource realization) ·
  `ThomasMichon/copilot-extensions#580` (pre-existing relay stability tracker;
  source-proven pinned-port eviction without occupant ownership/staleness proof) ·
  `ThomasMichon/copilot-extensions#5890` (existing unresolved closed-signature
  recurrence and bounded automated-attempt policy) ·
  `ThomasMichon/copilot-extensions#5896` (unreadable context sources silently
  omitted from budget attribution) ·
  `ThomasMichon/copilot-extensions#5898` (production AHP Picker mux presentation;
  an explicitly excluded follow-up, not a new relocation regression) ·
  `ThomasMichon/copilot-extensions#5900` (optional process-spawn provenance
  realization through existing telemetry seams) ·
  `ThomasMichon/copilot-extensions#5903` (tolerant endpoint JSON-shape access
  raises outside its declared unavailable outcome) ·
  `ThomasMichon/copilot-extensions#5904` (endpoint owner-check/unlink publication
  race after the completed PID-guard slice) ·
  `ThomasMichon/copilot-extensions#5908` (Sessions overlay loses provider failure
  and renders empty history) ·
  `ThomasMichon/copilot-extensions#5909` (unknown execution observation becomes
  affirmative Picker labels) ·
  `ThomasMichon/copilot-extensions#5910` (durable gitless core-source discovery
  after installed invocation/self-update) ·
  `ThomasMichon/copilot-extensions#5912` (venue subtitle durable-title authority) ·
  `ThomasMichon/copilot-extensions#5913` (declared New-venue provision/embody
  bindings, beyond the completed design-only handoff) ·
  `ThomasMichon/copilot-extensions#5918` (explicit-cell Vault WSL endpoint
  fallback accepts unattributed legacy provenance) ·
  `ThomasMichon/copilot-extensions#5921` (empty clean-room outcome report
  accepted as green) ·
  `ThomasMichon/copilot-extensions#5922` (Bridge solo absent-base witness
  declared but not enforced) ·
  `ThomasMichon/copilot-extensions#5924` (model-dependent subject witnesses
  versus the agent-free Tier P contract) ·
  `ThomasMichon/copilot-extensions#5926` (attached Container launch loses
  accepted seed-file input) ·
  `ThomasMichon/copilot-extensions#5927` (remote repo-plugin resolution
  failure erased into supported-empty discovery) ·
  `ThomasMichon/copilot-extensions#5930` (remote-driver exclusive,
  generation-fenced mutation ownership) ·
  `ThomasMichon/copilot-extensions#5935` (Companion prior-session selection
  and in-pane recovery, distinct from ordinary cutover and raw override) ·
  `ThomasMichon/copilot-extensions#5936` (provider-observed host-loss
  presentation and confirmed bulk resume) ·
  `ThomasMichon/copilot-extensions#5937` (effective target/provider/version
  disclosure distinct from local controller chrome) ·
  `ThomasMichon/copilot-extensions#5938` (POSIX stable registration and
  native update availability/tracking) ·
  `ThomasMichon/copilot-extensions#5940` (Vault live-owner admission before
  socket replacement/publication) ·
  `ThomasMichon/copilot-extensions#5941` (Logger named configuration lookup
  at scheduled execution) ·
  `ThomasMichon/copilot-extensions#5942` (default archive exclusion with a
  retained checkout) ·
  `ThomasMichon/copilot-extensions#5946` (PR-watch non-firing baseline
  persistence and restart transition continuity) ·
  `ThomasMichon/copilot-extensions#5947` (PR-watch subscriber expiry under
  provider outage) ·
  `ThomasMichon/copilot-extensions#5952` (caller-settings read failure fidelity) ·
  `ThomasMichon/copilot-extensions#5953` (identity-bound keeper retirement custody) ·
  `ThomasMichon/copilot-extensions#5954` (independent owned-forward cleanup) ·
  `ThomasMichon/copilot-extensions#5955` (Pythonless POSIX first-use bootstrap) ·
  `ThomasMichon/copilot-extensions#5956` (uniform deploy-template scope decision) ·
  `ThomasMichon/copilot-extensions#5957` (remaining live-contract tolerance windows) ·
  `ThomasMichon/copilot-extensions#5958` (persistent helper payload-CWD inheritance) ·
  `ThomasMichon/copilot-extensions#5959` (Vault exhausted transport cause preservation) ·
  `ThomasMichon/copilot-extensions#5960` (CI charter/tool and diagnostic-file promises) ·
  `ThomasMichon/copilot-extensions#5961` (selected venue session/mux attachment) ·
  `ThomasMichon/copilot-extensions#5962` (component-aware subtitle truncation) ·
  `ThomasMichon/copilot-extensions#5963` (claim-kind metadata/production admission) ·
  `ThomasMichon/copilot-extensions#5964` (hook/provisioning activation boundary)

## Guiding Intent

A lot of real capability has landed across many plugins without matching
vision buildout, and without strong adherence to the visions that already
exist. Run the `backporting-visions` skill (specializes `envisioning`)
repo-wide: for each vision in `visions/README.md`'s index, reverse-engineer
embodied intent from reality (code + reality docs) and fold it back into the
vision as a superset — additive only, never scaling back — then run the
design/service-invariant audit against `visions/plugin-services/README.md`
for any subject that ships an installable runtime. Carve every genuine
vision→reality delta (a north-star-ahead item, or an invariant
nonconformance) into a GitHub issue citing the vision item, grouped here.

This is explicitly a **multi-session, multi-stretch** effort — this repo's
vision index (`visions/README.md`) is too large for one sitting. Land one
coherent slice per session (one vision reconciled + its deltas carved) and
hand off the rest.

A companion, independent concern — refreshing stale user-facing docs,
README capability claims, and Picker preview screenshots/images — is tracked
separately as `adopter-material-refresh` (`#5799`), per the operator's Phase 4
decision. Its implementation does not expand this sweep's completion gate.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Primary (operator's agent) | Owns the whole sweep: reconciles visions, runs the invariant audit, carves issues | A `copilot-extensions` worktree, one per session/slice |

## Coordination

- **Topology:** independent per-slice PRs — each session lands one coherent
  slice (one or a few related vision files + any issues/comments it carves)
  through its own PR rather than a shared long-lived feature branch.
- **Host (owns PRs):** whichever session/worktree is actively working a
  slice; no standing owner beyond that.
- **Delegates:** no standing execution delegates. Bounded read-only evidence
  agents support independent source comparisons; the coordinator retains
  integration, issue decisions, edits, validation, and PR ownership. No
  cross-machine dispatch is needed for this documentation sweep.
- **Handoff:** a session that can't finish a slice hands off via the normal
  `context-handoff` mechanism, naming which Plan items are done and which
  vision files/issues are mid-flight, per this effort's own Journal.

## Context

- **Origin:** operator handoff directive, end of a session that landed
  `#5400` (agent-dispatch run-waiter surfacing) and `#5421` (masked-output PR
  guidance), plus two still-open bug issues (`#5356`, `#5410`) filed in the
  same session. The operator flagged these as concrete, session-evidenced
  vision-backport candidates and asked for the broader sweep to be scoped and
  sequenced across sessions rather than attempted at once.
- **Governing skill:** [`backporting-visions`](../../../../plugins/visions/skills/backporting-visions/SKILL.md)
  (specializes `envisioning`) — read its superset discipline and
  design/service-invariant audit sections before reconciling any further
  vision.
- **Vision index:** [`visions/README.md`](../../../../visions/README.md) — the
  full standing list this sweep works against, prioritizing branch visions
  and any leaf whose plugin had heavy recent PR traffic.

## Request

Operator, end of a long multi-repo session:
> "In a handoff, we probably need to do some 'vision backporting in
> copilot-extensions'. I worry a lot has gone in without vision buildout or
> strong adherence, and so we'll need to do some adaptation. The docs,
> preview images, and other user-facing materials are also getting out of
> date with all the new improvements, enforcements, and capabilities."

Follow-up decisions, 2026-10-08 (verbatim selections):
- Material-refresh relationship: **"Separate sibling effort"**
- Confirmed sibling slug: **"adopter-material-refresh"**
- Repository identity contract: **"Strict for explicit choices; preserve
  unconfigured ambient defaults"**

Follow-up installation-boundary decisions, 2026-10-09 (verbatim selections):
- Source artifacts: **"Strict: stage builds and cleanup outside the source checkout"**
- Updater orchestration: **"Keep installer phases machine-local; allow intentional higher-level source-sync orchestration"**

## Plan

### Phase 1 — The three session-evidenced targets (cheapest/most-certain first)
- [x] `visions/plugins/agent-dispatch/tasks-pane-ux/README.md` — fold back the
      run-waiter Tasks-board/Claims surfacing from `#5400` as a new Concepts &
      Components subsection ("The suspended-task waiter"), stated as pure
      should-be (every board path surfaces the waiter). The documented
      delegated-cross-machine/relay-subscribe implementation gap is **not**
      enumerated in the vision; it is tracked as
      `ThomasMichon/copilot-extensions#5452`, filed against this vision item.
      Provenance entry added.
- [x] `visions/plugins/agent-dispatch/reviewer/README.md` — fold back the
      recipe's standing `_STAGNATION_CLAUSE` charter behavior (already
      shipped, governs both `land=self`/`land=author`) as a new Behavior,
      `stagnation-escalates`, distinct from `bounded-verdict-reliability`'s
      render-attempt budget. Provenance entry added.
- [x] `visions/plugin-services/README.md` + `visions/plugins/agent-dispatch/README.md` —
      ran the Direction-1/Direction-2 invariant audit for agent-dispatch's
      runtime: the invariant vision already stated
      `register-once-cutover-on-update`/`immutable-versioned-runtime`
      correctly, but the agent-dispatch (branch) vision did not cite or
      restate them — added `inherits-runtime-service-invariants` there to
      fix that (Direction 1; its own Provenance entry records why, with no
      issue reference, per the vision-history-only convention). Direction 2
      (does the subject conform?) is `#5356` (agent-dispatch's `install.ps1`
      retires supervisor/coordinator processes *after* the in-place
      reinstall rather than before, plus an undetected stale `uv.exe`
      hazard) — linked to the `plugin-services` invariants directly via an
      issue comment, since that's the vision layer the conformance check is
      against.
      **Not yet done:** the *full* invariant audit this vision calls for
      ("every `agent-*` plugin's `install.ps1`, not just the one subject you
      happened to find a bug in") — tracked as Phase 3 below, not assumed
      complete from this one subject.

### Phase 2 — Widen the vision sweep
- [x] Read every remaining vision in `visions/README.md`'s index against its
      subject's current reality docs + code, prioritizing branch visions
      (`agent-fabric`, `native-convergence`, `plugin-services` itself) and any
      leaf whose plugin had heavy recent PR traffic. Apply the three-bin sort
      per vision (fold-back / north-star-ahead / omit); run the superset
      check before committing any single file. Track progress as a
      vision-by-vision checklist here once Phase 1 closes out and this phase
      starts in earnest (deliberately not pre-enumerated now — scope it from
      the actual state of each vision at reconciliation time, not guessed
      upfront).
      Completion roster: all 35 current index entries are explicitly attributed
      below. This closes current-source reconciliation, not the additive
      runtime/acceptance backlog carried by the named owners.
      - [x] `visions/plugins/agent-codespaces/README.md` — reconciled against
            313 commits of drift since its 2026-07-31 authoring. Folded back
            *repo-sourced-provenance* (active-plugin-declared venue policy,
            lower precedence than adoption) and *in-venue-plugin-injection*
            (the governing harness plugin injecting plugins into the venue
            itself), plus *dual-mode-session-reach* (interactive `copilot
            <name>` vs. headless `--detach`, orchestrator-blind `--ref-file`
            hand-off, `--reverse-forward`/`--forward` port bridging) under
            `coordination-layer-provider`. Corrected a stale "agent-containers
            when authored" sibling reference and widened the reality-docs
            skill list (`recovering-codespaces`, `cleaning-codespaces`). No
            conformance gap found — all fold-back, no issue carved.
      - [x] `visions/plugins/agent-ssh/README.md` — reconciled against 139
            commits of drift since its single-day 2026-07-22 authoring,
            never revisited since. Folded back *local-process reach* (the
            `wsl` in-box transport), fragment provenance/staleness
            detection (`doctor`'s managed-fragment audit), continuous mesh
            self-healing (`refresh-mesh` wired into `agent-machines`'
            hourly watchdog), a new *Machine maintenance escalation*
            concept, and *venue-contract-reach* (`agent-ssh copilot
            <ssh-target>` — an adopted mesh machine driven as a CLI-mode
            venue under the same contract agent-codespaces/agent-containers
            use). The last of these required reconciling a real textual
            contradiction: the vision's "Not a venue provider" Non-Goal
            flatly ruled out exactly this shipped capability. Reworded it
            to "Not a compute provisioner" (agent-ssh never creates
            compute; reaching an existing mesh member as a venue is
            squarely in scope) — consistent with
            `remote-interactive-sessions`'s own subject line, which already
            named "any agent-ssh-reachable machine" as a venue type
            alongside CodeSpaces/containers. `install.ps1`'s
            nonconformance is already tracked by `#5472` (Phase 3). PR
            review (#5627) caught one overstatement (WSL's "no daemon"
            claim — its own `sshd` + keepalive are still real
            prerequisites, only the network hop is removed; corrected) and
            one genuine pre-existing implementation gap the self-healing
            fold-back assumed worked end to end (`refresh_mesh`'s
            temp-directory provenance bug) — already tracked independently
            as `#5478`; cross-linked rather than refiled.
      - [x] `visions/plugins/agent-containers/README.md` — reconciled
            against 123 commits of drift since its 2026-08-27 last
            revision. Folded back *interactive-venue-reach-trusted*
            (`agent-containers copilot <name>` / `--detach` — the exact
            same CLI-mode venue contract agent-codespaces/agent-ssh use,
            deliberately excluded for restricted venues),
            *restricted-venue-picker-discovery* (`ssh-profile <name>
            --project` — a named, read-only Worktree Picker source), and
            extended `full-harness-projection-trusted` with host-backed
            persistence + `systemd_capable` self-maintenance for
            image-backed trusted fleets — a capability that had moved from
            the vision's own "as the capability matures" aspiration to
            shipped reality without the vision being told. Added a
            cross-link to `remote-interactive-sessions`, matching the
            ownership split already established there for
            agent-codespaces/agent-ssh. No conformance gap found requiring
            a new issue — `init.ps1`'s nonconformance is already
            tracked by `#5472` (Phase 3).
      - [x] `visions/plugins/agent-worktrees/pull-requests/README.md` —
            reconciled against 372 commits of drift in the plugin's PR
            surface since its 2026-09-20 last revision. Found in unusually
            good shape: two of the three gaps its own 2026-09-14 authoring
            named are now fully realized (`reviewer-capable-provider`,
            `conformance-verified-mock-provider`); the third,
            `foreign-repo-pr-operations`, is **partially** realized —
            `pr-watch`/`pr-merge`/`create-pr`/`pr-ready`/`pr-abandon`
            support it, but `pr-status`/`pr-diff`/`pr-comment`/`pr-review`
            still lack a foreign-repo argument. Closed two now-stale
            tracking issues with evidence (`#2699`, `#2691`) rather than
            leaving them open against shipped capability; narrowed `#2700`
            to those four remaining commands instead of closing it.
            Updated the vision's own stale Purpose & Intent opening, which
            still described reviewer-side operations as entirely missing.
            Only the opening narrative, the reality-docs pointer, and the
            tracked-issue state had drifted — the Feature/Behavior prose
            itself needed no correction.
      - [x] PR capability current-stage watch-runtime comparison: folded back
            durable shared target observation, independent subscription
            criteria/lifetimes and intended restart continuity. Pending
            subscriber identity/filter/config restoration is implemented;
            observed-baseline continuity remains partial under `#5946`.
            Configured lifetime expiry also remains provider-success-dependent
            (`#5947`); notification generation/attempt is not receipt proof.
            Narrowed the
            notification-platform boundary rather than accidentally forbidding
            the real scoped watch capability. Durable subscription ownership
            is not merely an optional warm-cache process; no idle-exit or
            missing-inline-subscription violation was established.
      - [x] `visions/plugins/agent-index/README.md` — reconciled against 70
            commits of drift since its 2026-09-18 last revision. Folded
            back three realized-but-undocumented capabilities: a `git:`
            source's per-source `ref:` override + `auth.account`
            authenticated fetch (sharpening `continuous-delta-freshness`'s
            "canonical default branch" framing from an absolute rule to a
            default-with-exception); corpus composition as a dynamically
            grafted, multi-project config (every locally-adopted project's
            `corpus.sources` unioned via the worktree registry) as the
            concrete mechanism realizing "a harness repo and its close
            ecosystem"; and the `providers.d/` external-content-domain-
            provider discovery now running on the shared `dropin-registry`
            library that agent-bridge's own namespace resolvers also
            consume (sharpened from "the same shape" to "the same
            library"). No conformance gap found requiring a new issue —
            the remaining drift (CPU-priority throttling, FTS
            rebuild/recovery refinements, server-venv packaging split,
            peer-launch/CWD-compliance infra shared with other plugins) is
            already-described behavior being bug-fixed into working order;
            `mutable-dev-slot`-pattern drift specifically is already
            tracked by `#5472` (Phase 3) — peer-launch and CWD-compliance
            are not that issue's scope and are not separately tracked
            here, being cross-cutting infra outside this vision's own
            subject.
      - [x] `visions/plugins/context-handoff/README.md` — reconciled against
            41 commits since its 2026-09-13 revision. Folded back durable
            baton storage distinct from pickup signaling, explicit manual
            operation even with automatic behavior disabled, extension-free
            recovery, exclusive attributable pickup, compact effort-backed
            continuation, and preservation of original objective authority.
            Sharpened evidence-faithful completion checks and safe transfer
            of background work without moving execution mechanics into this
            policy plugin. Not all guarantees are realized: automatic
            force-tier quiescing remains a gap (`#5683`), and successful
            consume discards failed lineage-promotion outcomes (`#5684`).
            Closed stale feature-delivery issues `#2595` and `#2596` with
            direct source/owning-effort evidence; their umbrella `#2594`
            remains open, not inferred complete from these two closures.
      - [x] `visions/plugins/agent-bridge/README.md` — scoped observation,
            attribution, generation, control, and carrier slices: evidence-backed presence, reported
            consumption with per-figure provenance and rollup coverage,
            conservative sub-agent attribution, creator identity distinct
            from caller affinity, and continuity-aware backward history
            browsing; deployment slice additionally reconciled candidate
            custody, transition serialization/recovery, and deployment
            freshness. Installer fallback remains a violation (`#5750`);
            recovery before serialization is `#5754`; native supervisor
            binding/coherence remains under `#4022`/`#5225`. The control
            slice additionally covers delivery decisions and
            cooperative stop/confirmation. The carrier slice additionally
            reconciles shared remote-control
            reach, isolated logical subscriptions, hosting-owned replay/cursor
            acknowledgement, and explicit unsupported-capability fallback.
            Uncertain mutating retries remain a source-proven violation
            (`#5843`), not a realized no-repeat guarantee.
      - [x] `visions/plugins/agent-bridge/README.md` — remaining current-stage
            hosting/address/routing/protocol comparison. Folded back explicit
            preference authority, compatible reuse and preserved confirmed
            choices; target-settings inheritance remains unproved.
            Existing `#566/#2530/#1266/#1460/#1468` retain unsupported hosting,
            catalog and protocol-envelope realization. Cold-store/origin
            resolution is conditional, not universal provider federation or
            archive-filter conformance.
      - [x] `visions/agent-fabric/README.md` — layer/venue/memory seam slice
            reconciled: attended and unattended session peers, shared
            coordination versus provider-owned lifecycle, capability-honest
            restricted venues, observation distinct from control, and
            agent-logger as a session-evidence consumer rather than a rival
            coordination layer. Ground/head/claims intent was retained;
            existing hardening remains in its owning efforts (`#3584` and
            design tracker `#3761` provide context). This scoped
            comparison is supplemented by the current-stage
            machine/trust/telemetry/accountability source map below; neither
            claims complete runtime realization.
      - [x] Agent-fabric remaining current-stage machine/trust/telemetry/
            accountability comparison. Declared locus/checkout class drives
            venue selection; opt-in machine convergence and constrained
            maintenance remain with their owners. Static fleet description is
            not proposed Gateway execution. Vault/relay profile gates and
            structural restricted-venue checks are real but not universal
            account/audience or enrollment proof. Existing lifecycle telemetry
            is optional/no-op by default, not a mandatory new control plane.
            No missing positive intent or new source-proven delta was found.
      - [x] `visions/native-convergence/README.md` — standards/native proof
            boundary slice reconciled: concrete AHP interoperability is not proof of
            a released native Copilot host; command or metadata presence is not
            enough to transfer primitive ownership. Existing `#985`/`#986`/
            `#988`, `#1266`, and `#1460` retain their respective convergence
            work. Phase B `#987` is closed with an unrelated closing artifact;
            its mapping scope remains under open umbrella `#985`, not inferred
            complete from that issue state.
      - [x] `visions/native-convergence/README.md` — remaining root/catalog/
            working-boundary source audit recorded in the owning
            `native-construct-convergence` effort. Configured roots, direct Git
            creation, harness-owned catalogs, native identity consumption, and
            native trust/permission integration were distinguished from proof of
            native mapping. No additional vision-level fold-back was required.
            Existing `#985`/`#986`/`#988` retain the unproved implementation
            scope; unrelated closure of `#987` is not Phase B completion.
            This completes the reconciliation audit, not native convergence.
      - [x] `visions/plugins/agent-worktrees/README.md` — account-boundary slice:
            account-catalog/routing separation, scoped credentials,
            inspectable choice, and explicit authentication failure versus
            ambient consent. Filed `#5791` for the concrete CLI fallback;
            retained the separate Copilot inference-identity safety boundary.
      - [x] `visions/plugins/agent-worktrees/README.md` — remaining parent
            reconciliation beyond the account slice, including head/claims,
            contribution roles, daemon authority, and lifecycle coverage.
            Retained-checkout archive filtering is now source-proven `#5942`;
            bounded daemon mutation admission stays with `#3761`.
            Final source legs resolve helper/package, direct hook admission,
            claim-provider hygiene, durable target and retirement questions:
            `#1096/#3761/#1043/#396/#5453/#5754` retain their respective
            implementation, with uniformity/protocol policy `#5956/#5957`.
      - [x] `visions/plugins/agent-worktrees/README.md` — scoped lifecycle,
            head-recovery, finalization, role resolution and daemon-write
            comparison. Rejected the initial design-only/bypass inference:
            migrated verbs dispatch through the real resident-daemon wrapper,
            with safe pre-send fallback and explicit ambiguous-write refusal.
            Remaining migration and read consolidation stay with `#3761`;
            direct implementation-body persistence is not a bypass finding.
      - [x] `visions/plugins/agent-worktrees/README.md` and
            `visions/agent-fabric/README.md` — scoped finalization/claim-bundle
            acceptance audit. Existing intent already states atomic accepted
            transfer; source releases responsibility before consumer completion,
            carved as `#5842`. This does not complete either parent vision.
      - [x] `visions/test-portfolio/README.md` — shared-host admission slice
            reconciled across direct-host and devcontainer entry points. Folded back
            protection of shared mutable test environments, explicit bounded
            contention, and read-only inventory availability; corrected the
            owning effort's stale guard/collection exemption. No tests were
            removed or reclassified.
      - [x] `visions/test-portfolio/README.md` — current-stage contract-map,
            effectiveness, tier/effect, growth and portfolio-observability
            comparison. Existing intent covers the inspected runner/policy;
            the owner explicitly leaves census, falsification, per-wave
            observability and governance ahead of implementation under `#1303`.
            Marker attribution and collected counts are not measured assurance.
            Running or rationalizing every test family is the owner's work,
            not a new completion requirement for this source-reconciliation
            sweep; no tests were removed or reclassified.
      - [x] `visions/test-portfolio/README.md` — scoped declaration,
            preparation/collection, contained-process and cleanup trace.
            Folded back the separate preparation capability without treating
            declared markers as measured assurance. Carved concrete preparation
            and cleanup-status violations (`#5868`/`#5869`); complete family
            inventory/effectiveness/growth policy and ownership proofs remain
            with `#1303` and the still-open full-leaf audit.
      - [x] `visions/coverage-guided-ci/README.md` — scoped source reconciliation of
            baseline collection, promoted pointer correlation, ancestor/source
            coordinates, debt, selection, and tier-restricted fallback. Existing
            vision passages already cover the observed intent; no new fold-back
            or source-proven deviation was established. Real artifact and
            consumer acceptance remain with the owning `coverage-guided-ci`
            effort, not inferred complete from this reconciliation.
      - [x] `visions/coverage-guided-ci/README.md` — ordinary-CI invocation and
            activation source audit. The real workflow still runs collect-only
            plus guards; selection is a bounded, non-blocking shadow annotation,
            not executable test selection. The owner retains Phase 4 live
            activation and runtime-covered acceptance under `#4453`; no new
            vision intent or redundant activation ticket was required.
      - [x] `visions/mux-companion/README.md` — scoped normal explicit cutover,
            read-only status/lineage boundaries, and refresh source trace
            inspected. No new fold-back was established.
      - [x] `visions/mux-companion/README.md` — current-stage Companion,
            refresh/error presentation, action dispatch and summon-source
            reconciliation. Existing intent covers the real status/lineage and
            human-gated cutover. Prior-session recovery is a distinct missing
            action (`#5935`); raw override remains `#5833`, failed-lineage
            fidelity extends existing `#5908`, and independent expected-head
            comparison stays with `#4369`. Windows Ctrl+K registration is real;
            clicking and tmux delivery are explicitly deferred in `mux-bind-relay`.
            Live summon/reattach and broader relay realization remain those
            implementation owners' acceptance, not new sweep execution.
      - [x] `visions/plugin-services/installation-cells/README.md` — scoped explicit
            context resolution, canonical receipts, activation generation,
            ambiguous legacy refusal, and Windows payload invocation traced.
            No new intent or source-proven cross-cell violation was established.
      - [x] `visions/plugin-services/installation-cells/README.md` — remaining
            current-stage POSIX invocation/adopting-manifest and lifecycle
            source map. Ten manifests require explicit context; Dispatch,
            Pull Requests and Budget remain legacy invocation declarations.
            Machines' receipt-authorized management and Index generation
            transactions are real, not universal lifecycle adapter adoption.
            Mode-qualified discovery/cleanup/rollback rollout and named MCP
            config precedence clarification remain `#1109/#1110`.
      - [x] `visions/machine-fleet/README.md` — current-stage vision/proposal
            ownership reconciliation. The owning foundation explicitly claims
            a proposed fixed-service route, not implemented runtime; preserved
            static-driver/service/credential/lifecycle authorities and optional
            standalone/direct operation. No missing embodied intent was found.
            Kept `#5789` intact and carved its excluded named reconciliation
            and subscribed-observation vision scope as `#5861`/`#5862`.
            Runtime realization and its acceptance proofs stay with those
            objectives; no global runtime absence or conformance is inferred.
      - [x] `visions/host-resource-providers/README.md` — current-stage
            reconciliation of the complete vision against the credential-source,
            provider-profile and relay endpoint seams. Existing intent already
            covers the inspected capabilities; no new vision prose was needed.
            The supported credential shape is narrower than a general named
            resource catalog or ensure/release contract (`#5877`). Added the
            concrete pinned-port occupant-eviction violation to existing `#580`,
            preserving dynamic-port startup and verified owned recovery.
            General resource realization and full credential/session lifecycle
            proof are not claimed by this source audit.
      - [x] `visions/plugins/efforts/README.md` — payload-only policy delivery
            reconciliation. Static projection and directly callable staged
            producers were distinguished from registered automatic hooks.
            Existing vision intent already covers the policy; no new runtime
            validator, daemon, or counter requirement was manufactured from
            unproved agent conduct. The historical owner is archived, not lost.
      - [x] `visions/plugins/agent-dispatch/task-outputs-and-review/README.md` —
            scoped conversation-source comparison and durable unsubmitted-draft
            fold-back. Retained independent confirmation, exact review, history,
            and affirmative delivery ahead of current implementation; the
            canonical backend owner's Phases 5-7 remain under `#3681`.
      - [x] `visions/plugins/agent-dispatch/task-outputs-and-review/README.md` —
            remaining current-stage creation-policy, completion retry and
            adapter comparison. Folded back compatible missing-material
            recovery without reopening terminal tasks or replacing recorded
            results. Backend Phases 5-6 and Tasks-pane Phases 10-12 retain
            creation/schema/composer and live acceptance under `#3681`;
            existing generic structured-result transport is not task-schema
            enforcement or universal retry-incarnation proof.
      - [x] `visions/plugins/agent-logger/README.md` and session-intelligence
            child — scoped preservation/derivation/catalog/consumer-source
            comparison; folded back structured work-item/session associations.
            Existing `#1817`/`#5676`/`#5677`/`#5678` retain their respective
            configuration, preservation, accounting and adoption objectives.
            Manifest preparation was not mistaken for final log rendering.
      - [x] `visions/plugins/agent-logger/session-intelligence/README.md` —
            explicit index attribution for that child comparison: structured
            associations, reusable preservation/derivation/catalog surfaces
            and independent accounting/aggregation intent were read with the
            parent. This attribution does not close the remaining corpus/
            consumer source question or their implementation acceptance.
      - [x] Logger remaining current-source corpus, admission and cold-reader
            comparison. Cross-root primitives are real, but chronicler
            settle/journal gates are not independent all-session derivation;
            process-log readers do not repair venue producers that never
            transported those logs. Qualified cold lookup and publication/
            accounting gaps remain explicitly `#5676/#5677/#5678`.
      - [x] Deferred to `ThomasMichon/copilot-extensions#5678`: released
            consumer adoption and implementation/live aggregation acceptance.
      - [x] Logger scoped rescue, cold-provider resolution, persistent digest
            readers and aggregate-plan/daily-manifest comparison. These
            foundations are real, but do not prove all-session derivation,
            accounting products, executable aggregate catch-up/rebuild or
            released consumer adoption. Existing `#5676`/`#5677`/`#5678`
            retain those deltas; no global absence or duplicate-rendered-log
            allegation was inferred from this cohort.
      - [x] `visions/ci-failure-remediation/README.md` — current-stage watchdog,
            trusted verification, compiled scope/output gates and charter source
            reconciliation. No missing vision-level intent was established.
            Open-issue recurrence is not another automated dispatch; closed
            recurrence is a distinct unresolved policy, now tracked as `#5890`
            in its existing owner. No live fix-agent run was triggered.
      - [x] CI-remediation remaining source charter/output/cancellation
            comparison. Deterministic path/output gates are real, semantic
            intent triage remains agent/reviewer conduct. Direct comment and
            promised diagnostic-file SHA clauses are `#5960`; generated
            reporting and original-issue SHA availability remain valid limits.
      - [x] Deferred to `ThomasMichon/copilot-extensions#4783`: CI-remediation
            complete external execution/acceptance and runtime blockage.
      - [x] `visions/harness-guidance/README.md` — scoped ownership, static/local
            delivery and context-accounting source comparison. Existing intent
            covers the inspected guarantees; repaired only duplicate section
            placement without removing intent. Reproduced silent source-read
            omission in the real budget API with mocked discovery (`#5896`).
      - [x] Harness-guidance current-stage routing/budget adapters,
            trust/recovery, native composition and contributor comparison.
            Static/session/local fallback remains the current compatibility
            owner; native composition requires supported-version proof.
            Offline budget resolution preserves attribution/freshness/errors
            and cannot qualify a model. Budget refinement of eligible routing
            remains explicitly ahead of code under `#2137`/`#2014`, not a
            manufactured executable conduct-validator requirement.
      - [x] `visions/process-registry/README.md` — complete current-stage
            vision/proposal comparison. The existing `#5559` owner explicitly
            leaves placement and implementation open; proposed admission,
            broker, query, journal and retention rules are not runtime evidence.
            No new intent or implementation-absence claim was manufactured.
      - [x] `visions/process-telemetry/README.md` — scoped existing lifecycle
            emitters, sink/spool and two real shared-spawn callers compared.
            No missing vision-level intent was found. Per-site instrumentation
            is optional; generic process publication remains unproved, tracked
            as north-star realization `#5900`, not per-site violation tickets.
      - [x] Telemetry closing-resource/adopter current-source comparison.
            Procutil spawn/wait/job-close and generic lifecycle/spool paths
            are real but not process closing-resource publication. The two
            inspected callers are optional unadopted sites, not violations.
            Portable start/end-or-closing records, cheap figures and adoption
            remain explicitly under `#5900`; no global absence, mandatory
            per-site rollout or released instrumentation is inferred.
      - [x] `visions/session-hosting/README.md` — scoped provider/agency,
            selection, interaction, replay/reconnect, fencing and retirement
            source audit. Existing vision covers the observed intent. Pure
            production-routing/composition proof confirmed the known deferred
            AHP/mux gap (`#5898`); narrowed direct handoff choreography to a
            partial provider-boundary question under `#2062`.
      - [x] Remaining hosting current-stage title, represented-human,
            contributed-provider and restart-bookkeeping comparison.
            Represented humans have real send/conditional-abort/mode controls;
            answerability is the narrower read-only boundary (`#2971`).
            Status projection is not native-title conformance (`#3588`);
            namespace/front-door registration is not generalized execution-host
            conformance (`#2062`). Full-restart/live acceptance remains with
            the owning hosting objectives, not executed by this sweep.
      - [x] `visions/installer/README.md` — scoped bootstrap, real core/module
            invocation, update fallback and doctor/readiness source audit.
            Existing intent covers the inspected capabilities. Withdrew the
            filesystem-presence-as-false-ready and blanket provisioning-abort
            claims; only bounded gitless source discovery was carved (`#5910`).
      - [x] Installer current-stage autonomous-refresh, readiness-consumer,
            adoption/presets and dependent-restart source comparison. Real
            checking/explicit repair and the readiness foundation/core adapter
            are not whole-set orchestration. Manager Phase 2/5/7 and open
            `#356`/`#357` retain realization; dependent restart is unproved,
            not a new danger inferred from late advice. Closed foundations
            `#352`/`#355`/`#1160`/`#1278` do not close those objectives.
      - [x] `visions/picker/README.md` — scoped row identity/disposition/
            observation, selection/action routing and history presentation trace.
            Existing intent covers the source; carved unknown-observation label
            loss (`#5909`) and false-empty history (`#5908`). Existing `#1193`
            and `#3587` retain neutral Worktrees contribution and search facets.
            New cold-prompt intent was already backported under `#5415`, not
            redundantly added here.
      - [x] Picker current-stage recovery/context/contribution comparison.
            Existing vision already states the intent. Carved production
            host-loss/bulk-recovery `#5936` and effective provider/target
            disclosure `#5937`; retained contribution convergence `#1193`,
            data authority `#5555` and guided provisioning `#542`/`#356`/`#357`.
            The ordinary banner correctly names Manager, not an engine version.
            Live/final-attachment acceptance remains with those owners.
      - [x] `visions/venue-pivots-ux/README.md` — scoped symmetric adapter,
            shared-renderer, provider-action and creation-contribution trace.
            Replaced stale pre-overhaul absence claims with pure standing
            intent; preserved title precedence, ranking, lifecycle differences,
            Open/New, supervision and every boundary. Carved title content
            (`#5912`) and per-venue creation binding (`#5913`); `#3657` and
            `#3507` retain supervision/actions and transferred live validation.
      - [x] Venue exact selected-session/mux, truncation and claim-admission
            source comparison. Real scope-derived attach is not selected-row
            identity propagation (`#5961`); whole subtitle pass-through is not
            transient-first ellipsis (`#5962`); installed kind metadata is not
            activated/executable or fully journalable extension (`#5963`).
      - [x] Deferred to `ThomasMichon/copilot-extensions#3657`: venue live
            action/supervision and selected-execution acceptance; `#3507`
            retains the transferred real-Docker lane.
      - [x] `visions/clean-room-validation/README.md` — scoped runner,
            report-admission, Bridge-solo and judge-contract source comparison.
            Existing vision intent covers the inspected guarantees. Carved
            empty-report admission (`#5921`), absent-base enforcement (`#5922`)
            and model-dependent Tier P witnesses (`#5924`); retained useful
            subject coverage and the declared no-model lane.
      - [x] Clean-room remaining current-source admission, report and
            judge-consumer comparison. Targeted manifest/setup/provenance
            guards are real; runners report judged:false and caller/agent
            literal judgment is not a hidden universal hard validator.
            Progressive fixture adds scenario-owned mechanical checks;
            turn-key assembly is not proved by honest-stop/doc-audit success.
            Repetition contamination/accepted false pass was not established.
      - [x] Deferred to `ThomasMichon/copilot-extensions#5921`: clean-room
            implementation acceptance for truthful outcome-report admission;
            `#5922/#5924` retain composition/lane realization.
      - [x] `visions/venue-parity/README.md` — scoped shared launch, seed
            forwarding, plugin composition, preference and detach comparison.
            Existing intent already covers those capabilities. Carved attached
            Container seed-file loss (`#5926`) and remote plugin-discovery
            false-empty (`#5927`); distinct detach implementations alone are
            not proof of a parity violation.
      - [x] Venue-parity remaining shared attached/detached/ACP and preference
            source comparison. Failed caller preference reads become empty
            defaults (`#5952`), but explicit/recalled flags and target-mode
            receipt/refusal are counterexamples to a global authority failure.
            Shared production primitives are real; distinct bodies alone are
            not a parity violation.
      - [x] Deferred to `ThomasMichon/copilot-extensions#954`: venue-parity
            P2-P5 implementation and live acceptance beyond source comparison.
      - [x] `visions/cli-default-bridging/README.md` — current-stage Draft,
            prototype and existing ACP lifecycle comparison. Corrected native
            close-method attribution and distinguished SDK-extension proof
            from plugin/skill/MCP composition. Retained every promotion gate,
            the existing default and sibling opt-in scope; `#5930` links the
            required mutation arbitration to its existing Phase 2 owner.
            Native callback support was not freshly proved from this checkout.

### Phase 3 — Full design/service-invariant audit
- [x] Recorded individual source dispositions for all 36 current anchors
      across thirteen runtimes at `657c41a40b74` (468 cells), superseding the
      grouped map for anchor-level interpretation. This completed artifact is
      not Phase 3 closure: **P** retains the precise source/rollout question
      described by its evidence key. **C** covers the inspected source lane,
      not released/live acceptance; **V** identifies the stated counterexample;
      **N** is scoped inapplicability. Conditional legacy/cell and native/
      companion distinctions are not flattened into global results.

      **Core evidence keys:** R package/materialization and helper location;
      I completed-slot mutation; S first-use dispatch; E endpoints; L native
      lifecycle/cwd; K ownership/retirement; Q adopted coalescing; G registry
      hygiene; T live contracts; A install/adopt; M managed companions;
      H hook descendants; N named identity; X diagnostic commands. Exact
      evidence and owner correspondence follows the tables.

      | Individual anchor | Worktrees | Bridge | Dispatch |
      |---|---|---|---|
      | self-contained-runtime | P/R | P/R | C/R |
      | immutable-versioned-runtime | V/I | V/I | V/I |
      | discoverable-local-endpoint | C/E | P/E | P/E |
      | a-la-carte-installability | C/G | C/G | C/G |
      | platform-native-lifecycle | C/L | P/L | P/L |
      | least-privilege-lifecycle-tier | C/L | C/L | P/L |
      | self-provisioning-runtime | P/S | P/S | P/S |
      | delegated-heavy-companion-runtime | N | N | P/M |
      | graceful-composition | C/G | C/G | C/G |
      | self-auditing-drop-in-composition | P/G | C/G | V/G |
      | version-skew-tolerant-contracts | P/T | P/T | P/T |
      | uniform-deploy-contract | P/R | P/R | P/R |
      | install-adopt-boundary | V/A | V/A | V/A |
      | entity-relationship-diagnosability | C/X | C/X | C/X |
      | collision-free-endpoints | C/E default | C/E default | C/E default |
      | endpoint-discovered-not-assumed | C/E | P/E | P/E |
      | standalone-reachability | P/R | P/R | C/E |
      | degrade-gracefully | C/G | C/G | C/G |
      | stale-drop-ins-are-inert-and-legible | P/G | C/G | V/G |
      | interoperate-across-version-skew | P/T | P/T | P/T |
      | local-first-exposure | C/E | C/E | C/E |
      | minimal-network-exposure | V/E | V/E | V/E |
      | fail-loud-on-endpoint-error | P/E | P/E | P/E |
      | install-leaves-repos-unaltered | V/A | V/A | V/A |
      | zero-downtime-cutover | V/I,L legacy | V/I,L | V/I,L |
      | register-once-cutover-on-update | N | V/L POSIX | V/L POSIX |
      | cutover-coherent-service-tracking | N | P/L,K | P/L,K |
      | payload-remains-replaceable | V/R cell payload-origin; P other | P/L | C/L |
      | single-instance-lease | P/K | P/K | P/K |
      | work-coalescing-singleton | V/Q writes | P/Q | N durable queue |
      | process-count-scales-with-services-not-sessions | P/K,H | P/K,H | P/K,H |
      | hooks-and-callbacks-are-transient | P/H | P/H | P/H |
      | identity-resolves-by-name-not-path | C/N | P/N | V/N |
      | refuse-not-silently-misidentify | P/N | P/N | V/N |
      | registered-tasks-target-by-name | N inspected resident | P/N | V/N |
      | traversal-questions-stay-answerable | C/X | C/X | C/X |

      **Venue keys:** R release/package construction; I numbered slots/source
      effects; B/H invocation/hooks; E discovery/exposure; L supervision;
      K leases/keepers; A/S independence/compatibility; D optional SSH
      companion; G/J/T registries/names/traversals; X Machines cell adapter.

      | Individual anchor | CodeSpaces | Containers | SSH | Machines |
      |---|---|---|---|---|
      | self-contained-runtime | C/R packaged | C/R packaged | C/R packaged | C/R packaged |
      | immutable-versioned-runtime | V/I legacy | V/I legacy | V/I legacy | V/I legacy; C/X cell |
      | discoverable-local-endpoint | P/E | P/E | N inspected API | N |
      | a-la-carte-installability | P/A | P/A | C/A | C/A |
      | platform-native-lifecycle | P/L | N execution rigs | P/L hosted transport | N oneshots |
      | least-privilege-lifecycle-tier | P/L | N | P/L | C/L |
      | self-provisioning-runtime | V/B fallback lock | V/B fallback lock | V/B fallback lock | P/B,X |
      | delegated-heavy-companion-runtime | N | N | N/D installed service control | N |
      | graceful-composition | P/A | P/A | C/A,D | C/A |
      | self-auditing-drop-in-composition | P/G | P/G producer only | P/G | N |
      | version-skew-tolerant-contracts | P/S | P/S | P/S | P/S |
      | uniform-deploy-contract | P/R | P/R | P/R | P/R |
      | install-adopt-boundary | P/I | P/I | V/I | P/I,X |
      | entity-relationship-diagnosability | P/T | P/T | P/T | P/T |
      | collision-free-endpoints | P/E | P/E | N inspected API | N |
      | endpoint-discovered-not-assumed | V/E legacy fallback | P/E | N | N |
      | standalone-reachability | P/A | P/A | C/D | N listener |
      | degrade-gracefully | C/A | C/A | C/A | C/A |
      | stale-drop-ins-are-inert-and-legible | C/G inspected scan | P/G producer only | C/G inspected scan | N |
      | interoperate-across-version-skew | P/S | P/S | P/S | P/S |
      | local-first-exposure | P/E | C/E | P/D | N |
      | minimal-network-exposure | P/E | P/E | P/D | N |
      | fail-loud-on-endpoint-error | P/E,L | P/E | P/D | N |
      | install-leaves-repos-unaltered | P/I | P/I | V/I | P/I,X |
      | zero-downtime-cutover | V/L | N installed-service scope | P/L hosted transport | N |
      | register-once-cutover-on-update | V/L POSIX; C/L Windows | N | P/L | C/L schedule |
      | cutover-coherent-service-tracking | P/L | N | P/L | N |
      | payload-remains-replaceable | P/K,R | P/K,R | P/K,R | P/R,X |
      | single-instance-lease | V/K live-owner admission | P/K | P/K | N daemon |
      | work-coalescing-singleton | P/K | P/K | P/K | N |
      | process-count-scales-with-services-not-sessions | V/K owner overlap | P/K rigs | P/K rigs | C/H oneshots |
      | hooks-and-callbacks-are-transient | P/H | P/H | P/H | P/H |
      | identity-resolves-by-name-not-path | P/J | P/J | P/J | C/J |
      | refuse-not-silently-misidentify | P/J | P/J | P/J | P/J,X |
      | registered-tasks-target-by-name | N owner service action | P/J | P/J,D | C/J |
      | traversal-questions-stay-answerable | P/T | P/T | P/T | P/T |

      **Auxiliary keys** are plugin-qualified in the evidence paragraphs:
      package/bootstrap/template, endpoint/wire, native lifecycle/retirement,
      registry/names/diagnosis, and hook chains. Cells retain the governing
      distinction; a bare P never means a guessed runtime defect.

      | Individual anchor | MCP | Logger | Index | Vault | Pull Requests | Budget |
      |---|---|---|---|---|---|---|
      | self-contained-runtime | P package | P package | P package/modes | P package | P package | C offline |
      | immutable-versioned-runtime | V legacy | V legacy | V native; P cell | V legacy | C completed-slot refusal | V numbered slot |
      | discoverable-local-endpoint | C rendezvous | N listener | P service/engine | P scoped discovery | C rendezvous | N |
      | a-la-carte-installability | C local | C local | P modes | C local | C own local surfaces/downward capability | C offline |
      | platform-native-lifecycle | N warmth registration | C timer; P cell | P modes | C native facility | P declared availability | N |
      | least-privilege-lifecycle-tier | C user process | C user timer | C native user; P companion | C user facility | C user process | N |
      | self-provisioning-runtime | P bootstrap/cell | P bootstrap/cell | P explicit modes | P bootstrap/cell | C first use | C first use |
      | delegated-heavy-companion-runtime | N | N | P reviewed companion/current registration | N | N | N |
      | graceful-composition | C direct fallback | C contribution | P modes | C extensions | C downward capability | N peer |
      | self-auditing-drop-in-composition | N consumer | N consumer | V provider registry | N consumer | N consumer | N |
      | version-skew-tolerant-contracts | P upstream/IPC | P contribution | P wire | P wire | P wire | N |
      | uniform-deploy-contract | P template | P engine | P cell/native template | P engine | P engine | P template |
      | install-adopt-boundary | P descendants | V source stage | P descendants | V source stage | V source stage | P descendants |
      | entity-relationship-diagnosability | N named suite entities | P composition | P composition | N named suite entities | C claimant CLI | N |
      | collision-free-endpoints | C ephemeral/native | N | V native engine; P cell | V legacy port; P cell | C ephemeral | N |
      | endpoint-discovered-not-assumed | C rendezvous | N | V native engine; P cell | V legacy fallback; P scope | C rendezvous | N |
      | standalone-reachability | C local | N listener | P modes | C local | C local service | N |
      | degrade-gracefully | C direct fallback | C optional peer | P modes | C extensions | C downward capability | C unavailable posture |
      | stale-drop-ins-are-inert-and-legible | N consumer | N consumer | V provider registry | N consumer | N consumer | N |
      | interoperate-across-version-skew | P upstream/IPC | P contribution | P wire | P wire | P wire | N |
      | local-first-exposure | C local | N | C defaults | C local | C loopback | N |
      | minimal-network-exposure | V TCP control | N | V native TCP; P companion | V additive TCP | V TCP | N |
      | fail-loud-on-endpoint-error | C control; P fallback | N | P bind/control | V client dial-cause loss | P RPC | N |
      | install-leaves-repos-unaltered | P descendants | V source stage | P descendants | V source stage | V source stage | P descendants |
      | zero-downtime-cutover | P drain/forced retirement scope | P scheduled work | P native/cell/companion | V restart handoff | P stop/restart | N |
      | register-once-cutover-on-update | N warmth registration | V POSIX; C Windows | V native POSIX; P cell/companion | V POSIX; C Windows | N native registration | N |
      | cutover-coherent-service-tracking | N native registration | N daemon handoff | V native POSIX; P companion | C tracked restart; P full cutover | N native registration | N |
      | payload-remains-replaceable | P launch/installer cwd | C schedule; P installer | C native cwd; P cell/installer | C service cwd; P callers | P launch/installer cwd | P installer cwd |
      | single-instance-lease | P promoted lease/retirement | N daemon lease | P native/cell/companion | V admission | C OS lease | N |
      | work-coalescing-singleton | P warm pool | N optional accelerator | N optional accelerator | N optional accelerator | N durable service | N |
      | process-count-scales-with-services-not-sessions | P lease/retirement | C bounded work | P modes | V admission | C OS lease | C bounded CLI |
      | hooks-and-callbacks-are-transient | C callback; P descendants | C callback; P schedule descendants | V admitted native hook; P companion | P legacy drift/start chain | C callback | C callback |
      | identity-resolves-by-name-not-path | N named repo/task | V named config | P named boundaries | N named repo/task | C slug/claimant | N |
      | refuse-not-silently-misidentify | N named repo/task | P trust/refusal | P named boundaries | N named repo/task | C claimant refusal | N |
      | registered-tasks-target-by-name | N named repo/task | V named config | P declarations | N named repo/task | C PR target slug | N |
      | traversal-questions-stay-answerable | N named suite entities | P composition | P composition | N named suite entities | C claimant CLI | N |

      **Exact evidence / source decisions and owners**

      - Core R: `materialize_main.py:357-450,500-507`, installer/launcher
        materializers; Worktrees `registry_paths.py:24-62`, `config.py:872-874`,
        `monitor_roots.py:19-20`, resident sweep `744-749`. `install_dir`
        returns runtime/state root, not payload; the separate helper lookup
        prefers inherited payload-root variables and rereads payload validators
        on the explicit-cell payload-origin route. An installed helper copy
        exists but that route does not select it. Thus self-contained is P,
        not blanket V, while that route violates payload replaceability.
        Bridge governance eagerly loads/caches its helper (`55-147`), so
        recurring reload is not proved. Cell ownership remains `#1096/#1110`.
      - Core I/S/A: versioned install bodies, actual first-use dispatchers and
        deployment/integration callers cited by the preceding source map.
        `#5472` remains immutability; `#1132` fallback lock; `#5851/#5852/
        #5853/#5855` install/adopt. Shared primitives and materialization do
        not close complete deploy-template, toolchain or descendant coverage.
      - Core E/T: Worktrees `hook_ipc.py:72-153,273-319`; Bridge
        `service_start_cli.py:78-96,181-209`, `client.py:201-267,441-478`;
        Dispatch `server.py:357-390`, `config.py:276-339,484-535`.
        Default ephemeral binds conform to allocation, not port-free exposure
        (`#54`). Per-verb/range/capability gates are source-present; whole
        support-window/envelope semantics remain `#1460/#1468`.
      - Core L/K/Q/H: resident startup/lease/retirement, Bridge singleton and
        self-retire, Dispatch route/supervisor locks; Worktrees
        `tracking_write.py:309-346,632-650` and shared singleton
        `server.py:59-105,500-525`. Preserve `#5453/#3761`, Bridge native
        tracking `#4022/#5225`, failed deployment/recovery `#5750/#5754` and
        POSIX registration `#5938`. Hook-to-installer/service descendant and
        complete promotion/candidate-retirement boundaries remain P.
      - Core G/N/X: config/claim providers, Bridge desired namespace sweeps,
        Dispatch registration publisher `66-74`, generic pointer writer
        `299-333`; named lookup, lineage and relationship playbook. `#1043`
        owns uncertainty/withdrawal hygiene, `#396` named registration,
        `#5942` default archive exclusion. Diagnostic C allows documented
        commands and named gaps; it is not proof of every storage lookup.
      - Core M: Dispatch `managed_runtime.py:100-164,471-490,1075-1100`
        and supervisor `980-1110` establish real materialization. Complete
        authorization/rollback/retention composition remains within its owning
        managed-runtime effort and transferred acceptance, not absence.
      - Venue R/I/B/H: `materialize_main.py:193-307,366-455,495-507` and
        release refusal `promote_release.py:324-333` establish the packaged
        canonical-library lane. Registered installer-engine materialization
        is not full control-flow uniformity. Slot/source effects remain
        `#5472/#5851`; payload fallback locks `#1132`; prerequisite and
        hook-descendant coverage stays with startup/provisioning owners.
      - Venue E/L/K: CodeSpaces relay lookup `422-475` includes legacy
        fallback; native update `install.sh:896-921,1120-1156` is `#5938`;
        owner `connection_owner.py:581-641,793-991` is `#4309`.
        Container broker `144-165` and required-live relay `880-955`,
        shared keeper/holds `115-330` / `74-153,276-475`, and SSH adopted
        keeper `416-493` establish reuse and OS-locked holds, not complete
        final signal/cleanup success. `#54/#1109/#1110` retain transport/cell
        scope. Resource-store age locks are not daemon leases.
      - Venue A/S/D/G/J/T/X: compatibility preflight, restricted stdio and
        explicit hosted transport, registry scans/doctor, named resource CLI
        and repository-free Machines discovery are real. Machines
        `cell_lifecycle.py:619-740,820-922` revalidates receipts/selection/
        inventories before removal and preserves state. Complete endpoint/
        task reader, protocol window and traversal inventories remain P;
        cell rollout stays `#1109/#1110`, not a new guessed defect.
      - MCP: `serve.py:136-228,400-620,660-691,811-839`,
        `forward.py:101-162`, `ipc.py:77-162`, upstream negotiation
        `protocol.py:35-44` / `client.py:207-230`. Native data transport,
        ephemeral discovery and direct fallback are real; TCP control is
        `#54`. Graceful-cutover owner retains promoted lease/drain/forced
        retirement and full caller/installer cwd decisions.
      - Logger: Windows stable launcher `install.ps1:980-1017,1161-1300`;
        POSIX units `install.sh:166-199,233,1094-1177`; named config
        `835-995` / `919-1088`; trust/diagnostic readers and sync lock.
        `#5938/#5941/#2701` retain native/name/cell obligations; corpus,
        accounting and consumer products remain `#5676/#5677/#5678`.
        Deferred manifests and same-holder retries stay legitimate.
      - Index: admitted native `ensure` `install.ps1:78-107,1638-1715,
        2277-2323,2510-2526`, POSIX `1888-1991`; cell transaction bodies
        and distinct engine/service startup; additive provider factory
        `sources/providers.py:283-318,495-558`. `#5768/#1096/#1110`
        retain mode/receipt/current-companion-registration decisions,
        `#1043` desired sets, `#5938` native tracking and `#54` exposure.
        Historical companion acceptance does not prove current registration.
      - Vault: `cli.py:57-310,328-415`; service `968-1089,1327-1333`;
        native launcher/update, warm handoff and bootstrap drift chain.
        `#5940/#5918/#5938/#54` retain admission/scope/update/transport.
        Client dial-cause preservation and hook-driven legacy service-start
        semantics require narrower ownership decisions; neither a bound
        endpoint nor a returning callback closes those anchors.
      - Pull Requests: completed-slot refusal `install.ps1:525-540` and
        `install.sh:423-465`; local service/claimant routes and OS lease;
        durable subscription `watch_daemon.py:204-240,298-371`.
        `#5946/#5947` retain continuity/expiry; lifetime ownership is not
        optional cache warmth. Protocol-1 equality alone is not a tolerance
        window. Release closure and daemon/installer cwd remain P.
      - Budget: offline strict CLI `68-125`, serialized binstub `155-292`,
        numbered-slot installers `294-317,800-815` / `12-20`.
        Immutability uses the same consolidated runtime owner; package/
        prerequisite/cwd branches remain P, not a nonexistent daemon.

      **Source decisions still open:** complete transitive package/refusal and
      prerequisite effects; full installer/caller cwd descendants; final
      keeper/candidate signaling and cleanup outcomes; current managed
      companion registration and every cell receipt/recovery/retirement
      branch; complete wire-window and documented traversal inventories.
      These remain sweep work until resolved or transferred to a named
      objective. Runtime fixes, release execution and live acceptance are
      separate and are never inferred from this artifact.

- [x] Produced a grouped source map spanning every current Feature/Behavior
      family across thirteen installer-bearing runtimes at `82c2b4590414`,
      including service-free applicability and the reviewed companion exception.
      This is a non-completing triage map, not an individual-anchor conformance
      verdict or Phase 3 closure. Scores describe grouped inspected evidence:
      **C** conforms in the cited path, **P** partial/mixed/unproved,
      **V** at least one grouped anchor has a demonstrated violation,
      **N** not applicable. A group score never asserts which individual members
      conform or fail; those dispositions must be resolved separately.

      | Group | Complete anchor membership |
      |---|---|
      | A | self-contained-runtime; a-la-carte-installability; graceful-composition; standalone-reachability; degrade-gracefully; delegated-heavy-companion-runtime |
      | B | immutable-versioned-runtime; self-provisioning-runtime; uniform-deploy-contract; install-adopt-boundary; install-leaves-repos-unaltered |
      | E | discoverable-local-endpoint; collision-free-endpoints; endpoint-discovered-not-assumed; local-first-exposure; minimal-network-exposure; fail-loud-on-endpoint-error |
      | W | version-skew-tolerant-contracts; interoperate-across-version-skew |
      | L | platform-native-lifecycle; least-privilege-lifecycle-tier; register-once-cutover-on-update; cutover-coherent-service-tracking; payload-remains-replaceable |
      | T | zero-downtime-cutover; single-instance-lease; work-coalescing-singleton; process-count-scales-with-services-not-sessions |
      | H | hooks-and-callbacks-are-transient |
      | R | self-auditing-drop-in-composition; stale-drop-ins-are-inert-and-legible |
      | N | entity-relationship-diagnosability; identity-resolves-by-name-not-path; refuse-not-silently-misidentify; registered-tasks-target-by-name; traversal-questions-stay-answerable |

      Evidence paths are under the named plugin unless marked shared. These
      grouped scores retain the earlier immutable/install-boundary findings.

      | Plugin | A | B | E | W | L | T | H | R | N | Source / qualified delta |
      |---|---|---|---|---|---|---|---|---|---|---|
      | agent-worktrees | P | V | V | P | P | V | P | P | P | `invoke-payload-runtime.sh:444-564`; `status_monitor_cli.py:228-292,491-522`; `tracking_write.py:567-690`; `claim_providers.py:402-461,766-812`. Native transport `#54`, first-use lock `#1132`, mutation backpressure `#3761`, resident replacement `#5453`. |
      | agent-bridge | P | V | V | P | P | V | P | C | P | `runtime-gate.sh:147-220`; `service_start_cli.py:78-96,123-209`; `singleton.py:24-65`; `provider_sources.py:435-471`; `agent_registry_discovery.py:185-275`. Preserve `#54/#1132/#5750/#5754/#4022/#5225/#1460/#1468`. |
      | agent-dispatch | P | V | V | P | P | P | P | V | V | `server.py:286-410`; `managed_runtime.py:1-105`; `registrar_discovery.py:108-145,299-333`; registration publisher `66-74`. Desired withdrawal `#1043`, named pointer migration `#396`; queue authority is not a disposable warm cache. |
      | agent-codespaces | P | V | P | P | V | V | P | P | P | `install.sh:213-218,896-921,1120-1156`; `connection_owner.py:581-641,793-958`; `config.py:1085-1213,2062-2218,2398-2445`. Live-owner admission `#4309`, POSIX registration `#5938`, no-flock `#1132`. |
      | agent-containers | P | V | P | P | P | P | P | P | P | `init.ps1:635-770`; `runtime-gate.sh:167-184`; `__main__.py:880-955`; shared `forward_keeper.py:115-330`. Venue-keyed keepers are execution support, not automatically a daemon-count violation; `#1132/#5926/#5927` retain scope. |
      | agent-ssh | P | V | P | P | P | P | P | P | P | `install.ps1:868-955`; `runtime-gate.sh:166-182`; `fragment_registry.py:824-1044`; installed dtssh launcher `665-703`. Restricted stdio and explicit tunnels remain legitimate; complete hosted transport update/cell proof remains partial. |
      | agent-machines | P | P | N | P | P | P | P | P | P | `init.ps1:2195-2251`; `discover.py:197-226,390-456,705-809`; `self_update_tasks.py:92-117,468-492`; `cell_lifecycle.py:619-740,820-922`. Finite scheduled work is not an always-on daemon; repository-free packages and receipt-aware lifecycle are real. |
      | agent-mcp | C | V | V | P | P | P | C | N | N | `runtime-gate.sh:148-245`; `serve.py:136-228,400-620,660-663`; `protocol.py:35-44`; `client.py:207-230`. Data IPC does not eliminate TCP control (`#54`); upstream protocol negotiation is not every local tolerance window. |
      | agent-logger | C | V | N | P | V | P | C | P | V | `install.ps1:835-1017,1161-1300`; `install.sh:233,919-1152`; `chronicle/orchestrator.py:60-80,150-196`. POSIX stable supervision `#5938`, named scheduled config `#5941`, cell invocation `#2701`; manifest preparation is not log rendering. |
      | agent-index | P | V | V | P | V | P | V | V | P | `hooks.json:13-15`; admitted native installer `ensure`/`Ensure-Running`; `server.py:245-250,748-789`; `sources/providers.py:495-558`. Mode-qualified hook/authority reconciliation `#5768`, native tracking `#5938`, registry hygiene `#1043`, exposure `#54`. No Dispatch-companion violation inferred. |
      | agent-vault | C | V | V | P | V | V | C | N | N | `cli.py:328-415`; `service.py:968-1089,1327-1388`; `install.ps1:875-965`; `install.sh:615-703`. Admission `#5940`, POSIX update `#5938`, explicit-cell fallback `#5918`; warm-secret transfer is not complete ZDD. |
      | agent-pull-requests | C | V | V | P | P | P | C | N | C | `install.ps1:525-540` preserves immutable-slot refusal; `__main__.py:54-85,446-588`; `watch_daemon.py:204-240,270-371`. Identity restoration is real; baseline continuity and outage-independent expiry remain partial (`#5946/#5947`). Durable ownership is not an idle-exit/no-inline violation. |
      | budget-guidance | C | P | N | N | P | N | C | N | N | Owning binstub `155-292`; `cli.py:55-93`; `resolve.py:35-107`; `posture.py:25-118`. Strict offline failure/freshness is real; later routing/adapters remain `#2137/#2014`, not a hidden network service. |

      All entries retain release-materialization, platform/cell and configured
      callback scope limits. Versioned helpers or first-use gates alone do not
      prove every POSIX cleanup/rollback receipt or declared tolerance window.
      `marketplace-scoped-installations` (`#1109/#1110`) and the relevant
      service owners retain those realization/acceptance obligations; this
      source cohort is not a whole-fleet deployment or outage reproduction.
- [x] Explicit-cell Vault WSL fallback source audit. The scoped launch gate
      carries identity/run-root/port-zero policy, but Windows-profile fallback
      accepts an unattributed legacy record even with an expected installation
      identity. An entirely synthetic use of the actual discovery body selected
      that record (`#5918`); preserve unconfigured legacy discovery and explicit
      recovery rather than globally removing them.
- [x] Scoped endpoint discovery/publication/cleanup API and two shutdown
      consumers inspected. Shared transport/alternate/override/legacy metadata
      and conservative PID/probe evidence are real foundations, not whole-cell
      or all-plugin conformance. In-memory use of actual APIs reproduced
      malformed-object access (`#5903`) and a publication after the final owner
      read being deleted by cleanup (`#5904`). No endpoint, PID, socket, service
      or live cutover was used for either proof.
- [x] Remaining endpoint/current-mode and lifecycle consumer source questions
      classified individually and through finite residual traces. Existing
      discovered/ephemeral default paths are not port-free guarantees or full
      cross-cell/native permission acceptance.
- [x] Deferred to `ThomasMichon/copilot-extensions#54`: endpoint transport/
      exposure realization and native permission/acceptance hardening; `#873`
      retains the specific Windows Vault pipe boundary and `#5918` provenance.
- [x] Ran a slice of the `plugin-services` invariant audit against every
      `agent-*` plugin's runtime-deploy path, scored against **two separate
      questions per plugin**: does a same-version, content-changed update
      (a) ever mutate the already-built slot in place
      (`immutable-versioned-runtime`), and (b) interrupt a live daemon doing
      so (`zero-downtime-cutover`, applicable only where a daemon exists)?
      `register-once-cutover-on-update`'s own distinct requirement (the
      supervisor registration staying bound to a stable launcher across an
      update) is **not** evaluated here — narrowing this completed slice to
      the two invariants the table actually scores, rather than claiming
      broader coverage.

      | Plugin | Immutable-versioned-runtime | Zero-downtime-cutover | Evidence |
      |---|---|---|---|
      | **agent-pull-requests** | **Conforms** | Not scored for the later watch daemon | Refuses to rebuild a completed same-version slot whose content changed, even under `-Force` (`install.ps1:524-535`). The original no-daemon classification does not describe its current durable watch service; that is scored separately in the later all-anchor source cohort. |
      | agent-bridge | **Violates** | **Violates** | `Test-SlotContentCurrent` only no-ops on an exact content match; a genuine same-version content change explicitly downgrades to "classic stop-and-rebuild" (`install.ps1:2813-2856`) — lock-safe (stops before rewriting, avoiding a crash/corruption race) but still an in-place rewrite of a built slot, and the stop-rebuild-start cycle is itself a no-service window. Not a reference implementation — a file-lock mitigation, not immutable-generation-plus-ZDD. **The only plugin in the unsafe/lock-safe split that is actually lock-safe.** |
      | agent-dispatch | **Violates** | **Violates** | `#5356` (pre-existing, linked to this vision item in the prior slice): `Invoke-Update` calls `Install-Runtime` *before* `Retire-SupervisorProcesses` — not lock-safe, so this can crash/corrupt, not just interrupt. |
      | agent-vault | **Violates** | **Violates** | `Invoke-Update` calls `Install-Runtime` (can rebuild the active slot) *before* `Stop-VaultDaemonGraceful`, with no `agent-pull-requests`-style refusal (`install.ps1:1092-1105`, `737-822`). Same not-lock-safe shape as `#5356`. Filed as `#5472`. |
      | agent-codespaces | **Violates** | **Violates** | `Deploy-Venv`/`Deploy-Package` have no refusal or stop-if-active guard; the Connection Owner daemon is only synced via `Sync-ConnectionOwnerService` at the very end (`install.ps1:1507-1544`, `888-930`). Filed as `#5472`. |
      | agent-worktrees | **Violates** | **Violates** | `Test-SlotAlreadyComplete` (the #2174 fix itself) only short-circuits the hash-match (nothing-changed) case; genuinely changed same-version content falls through to `Deploy-Venv`/`Deploy-Package` with no refusal or stop guard, despite the function's own comment naming the status-monitor daemon as exactly this risk (`install.ps1:1331-1356, 3829-3866`). Filed as `#5472`. |
      | agent-mcp | **Violates** | **Violates** | Confirmed: `init.ps1:612-708` unconditionally installs into the existing `$VenvDir`; the zdd cutover at `init.ps1:735-783` runs only afterward and explicitly skips an exact-version-match daemon, so a same-version content change mutates the active slot *before* any cutover runs at all. Filed as `#5472`. |
      | agent-index | **Violates** | **Violates** | **Not lock-safe, despite appearing to stop-before-rebuild at first read:** the `$targetWasActive`-guarded stop/remove branch (`install.ps1:1418-1431`) only runs when `$slotReady` is false (stale/incomplete). When the slot is healthy and marked complete, that branch is *skipped* — but the unconditional `uv pip install --reinstall-package`/`--refresh-package` steps right after it (`install.ps1:1492-1551`) still run into that same live, active venv regardless, with `Invoke-ServiceCutover` called only afterward (`2498-2499`). Filed as `#5472`. |
      | agent-logger | **Violates** | **Violates (partial fix tracked)** | `Install-Package` rewrites the already-built same-version venv regardless (`install.ps1:1019-1055`). The previously-filed `#5468` only proposed stopping/restarting the Scheduled Task — that closes the lock-safety gap, **not** the immutability violation; `#5468` is updated (see its own issue comment) to the refuse-or-new-generation fix, same as everything else in this table. |
      | agent-ssh | **Violates** | N/A (no daemon) | Unconditionally reinstalls the package into the existing `$VenvDir` whenever the venv already exists, with no same-version-content-changed refusal (`install.ps1:872-949`). |
      | agent-containers | **Violates** | N/A (no daemon) | Same unconditional in-place reinstall shape (`init.ps1:648-758`). |
      | agent-machines | **Violates** | N/A (no daemon) | Same unconditional in-place reinstall shape (`init.ps1:2164-2251`). |

      **Net finding:** almost the entire `agent-*` runtime-deploy ecosystem
      violates `immutable-versioned-runtime` for a same-version,
      content-changed update. Of the daemon-bearing plugins, only
      `agent-bridge` sequences the rewrite lock-safely (stop-before-rewrite,
      still non-conforming); `agent-dispatch`, `agent-vault`,
      `agent-codespaces`, `agent-worktrees`, `agent-mcp`, **and**
      `agent-index` (whose apparent stop-guard doesn't actually cover the
      "slot healthy, content changed" path) all mutate a live, active slot
      with no stop at all. `agent-pull-requests` is the sole conforming
      reference: refuse the rebuild and require a real version bump (or,
      equivalently, build a genuinely distinct generation) rather than ever
      rewriting a completed slot.

      No blind spot found requiring a fold-up into the `plugin-services`
      invariant vision itself — the vision's existing wording already states
      the correct contract precisely ("once a version's venv is built it is
      never edited in place"); the gap is conformance across nearly every
      plugin, not a missing or imprecise invariant.
- [x] Scoped `install-adopt-boundary` source-effect matrix across thirteen
      installer-bearing runtimes, including `budget-guidance`, against snapshot
      `3ea7d5844d61`. Inspected Windows/POSIX entry dispatch and reachable
      mutation bodies; no installer was executed by the evidence agents.
      The cited violation-producing files remained unchanged when pulled
      forward for integration. This is a scoped matrix, not whole-runtime
      conformance or a fresh all-venue execution result.

      | Runtime | Scoped status | Concrete source effect / coverage | Delta |
      |---|---|---|---|
      | agent-worktrees | Violates | `install.ps1:566-627` / `install.sh:438-516` can infer a project from CWD; update reaches `Deploy-GitHooksPath`, whose bodies change repository-local `core.hooksPath` (`3475-3503` / `2413-2428`). Experimental-setting helpers (`3440-3473` / `2529-2561`) overwrite a known false preference. | `#5852`, `#5853`; explicit integration remains supported. |
      | agent-dispatch | Violates | POSIX install/provision reaches `_pip_install` and `_scrub_payload_build_artifacts` (`install.sh:927-1099`); `1000-1044` deletes source build/egg-info, including src/vendored artifacts. This establishes source-tree deletion, not observed tracked-file deletion. | `#5851`; preserve clean package output in an owned stage. |
      | agent-vault | Violates | Install/update/provision passes the original plugin source to shared installer-engine cleanup (`install.ps1:737-874`, `install.sh:501-614`); the helper deletes source build/root egg-info after packaging. | `#5851`; source installation stays supported. |
      | agent-pull-requests | Violates | A build-triggering install passes the source cleanup target (`install.ps1:578-579`, `install.sh:450-459`). A matching completed slot skips the build; no integration action is needed for the source cleanup path. | `#5851`; keep immutable-slot and no-op guarantees. |
      | agent-codespaces | Partial | Host install/provision writes runtime/adoption-schema/service state; explicit config init/migrate has separate repo effects (`config.py:2176-2220`). Source backend writes and full integration authority were not closed. | No proven new gap; remaining coverage stays open. |
      | agent-containers | Partial | Runtime/container-schema migration and provider/SSH/source projections target machine state; explicit remote workspace provisioning is a separate capability. Source backend and custom-root/helper effects remain unclosed. | No inferred conformance or missing capability. |
      | agent-ssh | Violates | Direct source install passes `PluginDir` as `PayloadDirToScrub` (`install.ps1:239-240,938-942`, `install.sh:131`), reaching shared source artifact deletion. Snapshot first-use is a different path. | `#5851`; retain SSH/user-level projection and source install. |
      | agent-machines | Partial | Host runtime installation differs from explicit `migrate --repo --apply` and default-preview restore (`layout.py:403-470`, CLI dispatch). Cell helpers/backend and full explicit migration authority remain unclosed. | No blanket install-versus-restore equivalence. |
      | agent-bridge | Violates | Cold deployment/start reaches `migrate_config` (`service_start_cli.py:118-120`); `config.py:252-267` changes an unmarked zero idle TTL to 600. POSIX local package install also scrubs source artifacts (`install.sh:406-436,1411-1420`). | `#5855`, `#5851`; preserve configured cleanup and unconfigured-default handling. |
      | agent-index | Partial | Service/engine state and user-level registration are distinct from explicit `setup` repo designation (`config.py:625-659`). Cell recovery/cutover and configured source descendants remain unclosed. | No inference from a named repository or contract doc. |
      | agent-logger | Violates | Local source install/update/provision passes the source cleanup target (`install.ps1:1113`, `install.sh:865-872`) to shared artifact deletion. Configured sync/prune is separately declared source policy, not proof of incidental installer mutation. | `#5851`; retain configured source capability. |
      | agent-mcp | Partial | Host package/lifecycle work differs from caller-selected `materialize` projection (`materialize.py:372-449`). Cutover/reaper, cell and bridge descendants remain unclosed. | Explicit projection is not ambient adoption. |
      | budget-guidance | Partial | Host slots/markers/binstubs/user PATH are concrete; shared installer/prerequisite descendants were not exhaustively traced. No register/adopt writer appeared in the inspected CLI. | Search absence is not whole-runtime conformance. |

      Shared artifact deletion is in
      `libs/installer-engine/installer-engine.ps1:63-69` and `.sh:29-47`;
      local custom scrubs are listed separately above. Machine snapshots and
      marketplace staging are not confused with original checkout mutation.
      Only this invariant was scored. The matrix does not score every package
      backend, arbitrary configured callback, or lifecycle descendant.
- [x] Complete the remaining install/adopt source coverage: namespaced
      lifecycle helpers, package-backend/prerequisite effects, and
      ownership/contribution enforcement for explicit integration/projection
      routes identified as partial above.
      The declared package graph/materialization/refusal path is source-present;
      remaining source-stage and fallback effects are `#5851/#5820/#4410`,
      interpreter acquisition `#5955`, cell cleanup/rollback `#1096/#1110`.
- [x] Resolve individual-anchor/per-mode dispositions from the grouped map
      before Phase 3 closes: each applicable item needs its own bounded status,
      source evidence and implementation owner, rather than inheriting a
      strongest-negative group score. Remaining source decisions concern
      release-materialized package/installer boundaries, supported
      lease/coalescing admission and retirement consumers, and cell-qualified
      discovery/lifecycle/cleanup/rollback receipts. Distinguish uninspected
      source from declared rollout under `#1109/#1110` and acceptance retained
      by service owners; do not silently turn any of them into conformance.
      Live execution of every carved fix is not this sweep's completion gate.
      Finite residuals were resolved into source C/V/N or named implementation/
      policy/rollout objectives. The final owner table and Journal close
      generic unread-source blockers without upgrading P to false conformance.
- [x] Deferred to `ThomasMichon/copilot-extensions#1096`: installation-cell
      family rollout, interrupted receipt/recovery/retirement execution and
      release/live acceptance; `#1109/#1110` remain its specific consumers.

### Phase 4 — Decide the material-refresh relationship
- [x] Decide whether user-facing material refresh (docs, Picker preview
      screenshots, README capability claims) becomes a sub-stream of this
      effort or its own sibling effort. Operator chose a separate sibling,
      confirmed `adopter-material-refresh`, and its independently reviewed
      planning/execution is tracked by `#5799`.

## Validation Plan
- [x] Every vision file touched passes the superset check (no unintended
      Non-Goal/negative the subject violates; every folded-back capability
      traced to a real PR/commit/reality doc).
- [x] Every carved issue cites its vision item per `visions/README.md`'s
      convention and is deduped against open issues before filing.
- [x] No vision file records conformance/gap-list prose — that output lives
      here or in linked issues only.

## Proposal

**Settled:** user-facing material refresh is the independent
`adopter-material-refresh` sibling (`#5799`). This sweep owns vision
reconciliation and invariant-audit evidence; the sibling owns adopter-facing
documentation and assets. Neither closing a sweep slice nor publishing the
sibling plan proves that the sibling's materials have been refreshed.

## Journal

### 2026-10-09 — Source-reconciliation completion and explicit follow-on ownership
- Completion is the original sweep: reconcile the current 35-entry vision
  index as a superset and classify applicable service invariants. It is not
  completion of every runtime delta, deployed fleet, clean-room acceptance,
  native protocol convergence or independent adopter-material refresh.
- All current indexed subjects have source/owner maps. The 36-by-13 artifact
  is 468 individual source dispositions, not 468 unconditional conformance
  promises. Finite P questions were resolved to inspected source or explicit
  implementation/policy/rollout owners, preserving partial and conditional
  legacy/cell/native/companion distinctions.

  | Final source decision | Evidence / owner | Completion boundary |
  |---|---|---|
  | Package/refusal construction | Materializer and recursive nested-reference checks; promotion refuses unresolved results. | Source construction is C; artifact/release execution stays with implementation owners. |
  | Deploy-template scope | Documented engine exclusions and permanent Worktrees exception. | Known contract discrepancy `#5956`, not an invented callback or a silent invariant weakening. |
  | Hook admission | Worktrees registration reaches cold resident ensure; Bridge/Dispatch/Vault background owning installers. | Direct boot `#3761`, Index native lane `#5768`, cross-cutting installer boundary `#5964`. |
  | Registry/transition custody | Claim-provider uncertainty/findings are dropped; Dispatch withdrawal retry and passive-age reap lack complete authority. | `#1043/#396/#5754`; conforming config/namespace registries and promoted survivor guards remain valid. |
  | Persistent launch/cleanup | Owning shims/native roots protect lanes; supported direct callers can inherit payload cwd; numeric keeper retirement and sequential failed stops are concrete. | `#5958/#5953/#5954`; staging `#5820`, live-owner admission `#4309` remain distinct. |
  | Acquisition/fallback | Private uv exists, legacy Python guards precede it; Container pip branch omits shipped-library precedence. | `#5955/#4410`; no inevitable failure or all-platform claim. |
  | Cell recovery/current modes | Receipt/lock/selection/retirement source exists; Index post-deploy rollback can retire a healthy successor when old endpoint is gone. | `#1096/#1110` and Index rollback objective; no live transaction or outage reproduced. |
  | Live contracts/diagnostics | Bridge inventory is present; other internal windows remain inventoried realization. Concrete composed commands and named gaps exist. | `#1460/#1468/#1915/#5957`, task/session gap `#3555`; no forced extra native handshake. |
  | Corpus/admission/retrieval | Cross-root readers are real; chronicler gates, incomplete venue log transport and qualified cold lookup remain bounded gaps. | `#5676/#5677/#5678`; no global corpus absence or duplicate rendered-log assertion. |
  | Judge/report consumers | Setup/container/model/driver/provenance checks and scenario fixture validation exist; semantic literal judgment remains caller/independent judge conduct. | `#5921/#5922/#5924` realization; no mandatory validator or accepted contamination claim. |
  | Venue/policy consumers | Real attach defaults, provider actions and charter gates are not selected-execution/truncation/claim-extensibility or model-compliance proof. | `#5952/#5960/#5961/#5962/#5963`; existing `#954/#3657` retain live implementation. |

- Every newly carved issue was deduped and its assigned number/title fetched
  immediately. Existing owners were extended where the same source class
  applied; no competing runtime PR, production agent, live cleanup or policy
  choice was executed. New and existing negatives were not weakened to match
  lagging source.
- Reviewable documentation slices and targeted source/shape counterexamples
  establish this reconciliation. Runtime unit/live/clean-room fixes stay
  with the machine-checked transferred objectives above; no absent test or
  proposal was recast as successful acceptance.
- Status is Done for source reconciliation. The independent
  `adopter-material-refresh` remains separate under `#5799`, and all carved
  implementation/decision owners remain open on their own merits.

### 2026-10-09 — Individual-disposition publication and finite residual audit
- Individual-anchor/preference/publication slice merged as `#5950` at
  `c3d9b7e308dd`, then finalized. The owner-authored review was a clean
  Comment with zero concrete findings; its generic request for human
  validation did not invent a new merge gate. Waited for actual same-head
  CI run `37966411955` to finish successfully, separately from required
  checks and review disposition.
- Re-read the current 35-entry index against the canonical roster. Added
  the Logger session-intelligence child's explicit path rather than treating
  a shorthand parent/child mention as absent comparison or skipping it.
- Remaining P source questions are being narrowed to actual repository
  callers: package/helper closure and deployment boundaries, hook descendants,
  registry/lease retirement, current cell/companion activation and protocol/
  traversal scope. Arbitrary future operator callbacks are not an endlessly
  expanding audit target. Source-uninspected, intentionally unsupported/
  staged rollout and implementation/live validation remain distinct.
- Remaining corpus, clean-room admission/judge consumers, venue preference/
  reattachment, telemetry closing-resource and CI charter source legs retain
  their explicit owners and completion questions. The 468-cell artifact is
  evidence organization, not a claim that every P has already been resolved.
  Parent Status and global Validation Plan remain Active/open.
- Closed the bounded telemetry source leg: the complete leaf already covers
  optional start/end-or-closing records and cheap exit-resource figures.
  Dispatch/Bridge lifecycle producers and generic spools are implemented;
  procutil and the inspected cache/SSH callers expose subprocess/exit/job
  cleanup, not resource telemetry. Retained `#5900` as portable realization
  owner. Inline exception fail-open is not latency isolation, but no adopted
  spawn-site violation was proved; no per-site ticket or stronger claim.

### 2026-10-09 — Preference authority, publication recovery and cell-mode boundaries
- Source-map/subscription slice merged as `#5943` at `96385a4ba739` with
  current-head approval and zero findings, then finalized. A sparse PR-check
  rollup reported success while the same-head CI run was still active; waited
  for pinned run `37956634791` to finish successfully before merging rather
  than treating the sparse rollup as whole-CI proof.
- Review-driven corrections stay durable: the grouped map is non-completing;
  watch identity restoration is not baseline continuity (`#5946`), and
  configured timeout evaluation is not outage-independent expiry (`#5947`).
  Those are additive implementation owners, not weakened vision intent.
- Three read-only follow-ups mapped remaining Bridge, task-conversation and
  cell-source families at `4ca76936b3ed`, with no runtime or live acceptance.

  | Intent / source boundary | Evidence / retained owner | Reconciliation |
  |---|---|---|
  | Preference authority / reuse | Bridge `preference_requests.py:10-79`, `docs/session-preferences.md:3-31,95-125`; `session_preferences.py:68-75`. | Folded back explicit authority, compatibility refusal and confirmed-choice preservation. Unsupported execution receipt is not proof of target-settings inheritance. |
  | Hosting / addressing | `session_start.py:340-451,519-653,688-735`; host connection/recovery; resolver/probe routes. | Supported local/Container/CodeSpace hosting is real. Generic SSH/Elevated survivability remains `#566`; project/catalog parity `#2530`; AHP/native host `#1266`. |
  | Contract evolution | `client.py:553-615`; `protocol.py:195-207`; host `version_mux.py:40-101`; registry not runtime-consumed. | Current compatibility gates are not pre-effect envelope selection, durable pinning or old-writer retirement; `#1460/#1468` retain those goals. |
  | Cold/origin lookup | Owned transcript, exact-ID cold provider and worktree transcript routes; dispatch origin route `85-179`. | Conditional lookup is not universal latest-worktree/archive discovery or federation. Bridge session archive-filter ownership is unconfirmed; ground listing delta remains separately `#5942`. |
  | Creation / schema policy | Dispatch `queue_storage.py:289-356`; producer `185-197`; verification backfill `59-94`; owner amendment/Phases 5-6. | Existing verification fields are not the new creation-captured policy/output contract. Implementation and adapter/schema safety remain `#3681`. |
  | Missing result recovery | `queue_lifecycle.py:130-196`; CLI `435-455` -> client `333-369` -> coordinator `749-761`. | Same completing owner can fill missing structured material; existing/conflicting result/reference and ambiguous legacy ownership are guarded, identical retry emits no new result event. Folded back intent without claiming universal incarnation or reviewed-envelope fidelity. |
  | Real adapters / composer | MCP `531-545`, HTTP MCP `773-792`, result CLI/HTTP readers; Tasks pivot declaration. | Generic object/array result transport is real. Contract-kind validation, exact review/history and extended composer/follow-up remain backend/Tasks-pane owners, not proved by schemas or bindings alone. |
  | POSIX context adoption | Generator/template routes; ten context-required manifests, three legacy declarations. | Real adopted command dispatch, not thirteen universally contextual callers. Contextual peer helpers do not upgrade Dispatch's manifest. |
  | Receipt lifecycle / transition | Machines `cell_lifecycle.py:544-918`; Index `cell-runtime.py:4275-4415,5124-5198`; explicit attribution/retirement helpers. | Source integration is real; path-isolated removal is not receipt authorization, nor one exemplar every family’s repair/uninstall. `#1109/#1110` retain default-off rollout and acceptance. |
  | Named MCP config discovery | Scoped gate exports marketplace root; lookup `config.py:107-133,369-395,500-521` checks workspace before scoped roots. | Posted a contract clarification under `#1110`: distinguish explicitly authorized workspace override from implicit cross-cell named discovery before a violation/conformance claim. No foreign config was actually loaded. |

- The two leaf revisions preserve all prior intent and boundaries. Compatible
  recovery protects the accepted outcome/decision, not a blanket ban on
  missing-material publication; it does not bless a new answer after review.
  No model/permission/default decision or packaging change was made.
- Remaining individual service-anchor source/receipt decisions and final
  whole-index/Validation gates are still open; implementation/live proofs
  remain with named owners rather than being silently required or claimed by
  this documentation sweep.

### 2026-10-09 — Remaining fabric branch source and authority map
- Completed the current-stage branch comparison against machine routing,
  convergence/maintenance, fleet proposal, scoped credential/venue checks,
  telemetry and finality owners at `82c2b4590414`. No new positive intent or
  high-confidence implementation delta was established.

  | Branch intent | Source / existing owner | Proof boundary |
  |---|---|---|
  | Machine-selected routing | `related_cli.py:121-155`; `related.py:948-990,1451-1497`; `machine-transport-convergence` owner. | Declared roots/locus/delegation and registered checkout class remain authority; no universal venue substituted. |
  | Unreachable maintenance | `performing-machine-maintenance` skill requires queue locator, trusted derivation, claim/revision recheck and mutation admission; completed `#1891` owner. | Shipped conduct, not an automatic remote repair executor or issue-command interpreter. |
  | Opt-in machine convergence | `agent_machines/self_update.py:220-275,484-630`; `self_update_lock.py:230-288`; scheduled actions `self_update_tasks.py:102-105,311-364`; `#2620`. | Dirty/diverged pulls skip. An unrelated live session is not a blanket machine maintenance block; mutation-specific admission remains separate. |
  | Fleet participation | `agent_ssh/fleet.py:25-60` validates explicit targets and source digest; foundation `#5789`. | Static SSH description is not Gateway enrollment/routing. Named reconciliation/observation stay `#5861/#5862`; no proposal promoted to execution. |
  | Credential/venue trust | Vault locked/missing outcomes `service.py:582-597`; Bridge profile gates `agent_registry_relay.py:73-144`; relay `server.py:395-436`; Container resolver/lifecycle `213-280,360-490`. | Local custody/action-specific authority, not universal audience/account isolation or fleet enrollment. Restricted structural checks remain distinct from trusted projection and prompt safety. |
  | Telemetry / accountability | Dispatch `coordinator_tasks.py:327-342`, `telemetry.py:91-103,203-269`; Bridge `events.py:149-170`; Worktrees `finalize.py:1596-1625,1767-1795`. | Optional producer evidence and obligation admission are real. They do not prove every configured drain or close-out path; claim acceptance `#5842` is unchanged. |

- Remaining complete relay-cache/account and configured telemetry/updater
  acceptance stay with their owning objectives; those are not inferred from
  this map. This closes the branch's source-reconciliation leg, not the
  parent sweep or any runtime/fleet delivery gate.

### 2026-10-09 — Service-family source map and remaining UI/policy owners
- Three read-only tracks covered every current service Feature/Behavior anchor
  across thirteen installer-bearing runtimes. The grouped Phase 3 table records
  bounded family applicability and partial evidence, not individual-anchor
  conformance or Phase 3 closure. Review identified the ambiguity with the
  still-open audit item; renamed the completed artifact to a non-completing
  source map and narrowed that open item to the actual remaining source/
  receipt/consumer decisions. The map does not
  turn unchanged runtime code, owner plans or release history into acceptance.
- Deduped and extended existing owners: POSIX no-flock reclaim `#1132` across
  Worktrees/Bridge/CodeSpaces/Containers/SSH; Connection Owner live-beacon
  admission `#4309`; daemon mutation backpressure `#3761`; Index desired-set
  retention and Dispatch failed-withdrawal retry `#1043`; named registrar
  persistence `#396`; native-transport preference `#54`.
- Carved separate verified deltas: POSIX stable registration/native tracking
  `#5938`, Vault pre-bind live-owner admission `#5940`, Logger named scheduled
  config resolution `#5941`, and default archived-record exclusion with a
  retained checkout `#5942`. Every created issue's number/title was fetched
  immediately; symbolic/source branches are not executed live races.
- Corrected two overclaims before integration. Index hook startup and native
  successor tracking concern its admitted self-owned legacy/native lane, not
  a proved Dispatch-companion route. Explicit configuration/role and cell
  admission remain real guards; the reviewed heavy-companion exception is
  intact. Current mode/architecture reconciliation stays with `#5768`.
  PR-watch is a durable subscription service, not merely optional identical-work
  warmth: `on_idle=None` or absent inline subscription alone establishes no
  violation. Folded back its shared target observation, independent criteria/
  lifetimes and intended restart continuity, narrowing only the unintended generic
  notification prohibition.
- Review found a real limit to that foundation: registry polling advances
  non-firing baselines in memory, while daemon polling persists only a fired
  result and shutdown performs no final persistence. A restored review/check/
  mergeability subscription can therefore silently seed a newer value after
  downtime. Symbolic tracing confirmed this without executing a watch.
  Classified pending identity/filter/config restoration separately from
  transition continuity and carved `#5946`; closed `#5681` fixed a different
  temp-file/predecessor test race. The desired vision remains intact.
- A subsequent review found an independent lifetime partial: fetch failure
  continues before timeout evaluation, and the available `sweep_timeouts`
  has no production caller. Expired durable subscribers can remain pending
  during an outage; unknown baselines also delay successful-poll expiry.
  Carved `#5947`, retaining synchronous-wait and requested-transition
  counterexamples. Notification attempt/failure is not receipt, and no
  complete delivery guarantee was inferred from event generation.
- Remaining UI/policy comparisons found existing vision intent sufficient:
  hosting title `#3588`, represented-human answerability `#2971`, generalized
  providers `#2062`; Manager readiness/adoption/presets and dependent restart
  remain in its Phase 2/5/7. Picker core recovery/context were unowned bounded
  follow-ons, now `#5936/#5937`; contribution parity stays `#1193/#5555`,
  guided provisioning `#542/#356/#357`.
- Static/session/local guidance remains the compatibility floor. Actual offline
  budget code resolves fields by authority/time, marks stale/error/contradiction
  rather than inventing allowance, and uses optional ceilings/rates only when
  fresh. Eligibility resolver has no budget input; budget refinement is later
  composition `#2137/#2014`, not proof of missing conduct enforcement.
- No runtime fixes, fresh native-host/clean-room/model acceptance or live race
  validation are included in publication. Remaining source-mode/receipt,
  fabric and consumer coverage must still be resolved or explicitly transferred
  before the parent sweep's Plan/Validation gates close; Active is unchanged.

### 2026-10-09 — Companion action, observation and summon-source reconciliation
- Read the complete Companion vision against `mux_companion.py`, the actual
  session-option fragment and Windows launch/join call sites, with both
  `mux-bind-relay` and `mux-companion-manual-cutover-diagnostics` owners.
  Existing intent is a superset; no vision rewrite or new negative was needed.

  | Intent | Source / owner evidence | Disposition |
  |---|---|---|
  | Status / lineage / disposable popup | Companion resolves current status and keyed session rows, displays head markers and pending-baton headline, and is an on-demand Textual app (`mux_companion.py:103-194,270-354,398-403`). | Real foundation, not a new state authority. Per-fact freshness and complete provider behavior are not inferred from a coarse closure label or architecture description. |
  | Explicit ordinary cutover | `_cut_over` calls the owning helper only from a direct button action, reports errors/returned head transition, then refreshes (`357-396`). | Preserves normal manual cutover; it is not the raw reassignment already tracked as `#5833`. |
  | Prior-session recovery | `compose` and `on_button_pressed` offer only Cut over/Refresh/Exit; keyed lineage rows have no resume-selection action. | Deduped exact feature and Companion/resume searches, then carved `#5935`. The Picker/engine may already recover sessions; this is the missing in-pane consumer action only. |
  | Unavailable lineage | `_load_current_worktree` catches history `EngineError` and retains `sessions=[]`, then renders an empty table (`124-130,311-327`). | Extended existing failure-fidelity owner `#5908`; preserve otherwise usable status while distinguishing unavailable from empty success. No live lost-history incident claimed. |
  | Expected-head verification | Current `is_head` and immediate returned head-before/after are displayed; no independent most-recently-selected expectation is compared after reopening. | Posted the narrower unproved comparison under existing `#4369`, preserving read-only detection and no auto-repair. |
  | Windows summon | `session-options.ps1:111-170` generates only Ctrl+K and sources it per session. Launch/join apply it after passthrough (`launch-session.ps1:1776,2128`). | Real registration, not our own live keypress proof. Mouse dispatch is deliberately withheld after an owner's documented key-table corruption finding; tmux and popup rendering remain explicitly owned follow-ups. |
  | Popup lifecycle / general relay | `mux-bind-relay` retains its rendering/click/platform obligations; `#2696` retains exit/quoting findings. | Historical empirical records are owner evidence, not a new live acceptance claim. No unscoped tmux bind, machine pin change, alternate execution path or live session displacement. |

- This is source-audit closure for the current leaf, not completion of its
  runtime realization. Existing owners retain summon/other-provider acceptance
  and the deliberate raw-head and ordinary-cutover distinctions. No policy,
  process, pane, lineage or installation was changed.

### 2026-10-09 — Agency, evidence and portfolio owner reconciliation
- Final-leaf cohort merged as `#5931` at `82c2b4590414` with current-head
  approval and actual CI, then finalized. The reviewer carried its original
  native-floor finding into the approving overview after the fix; replied
  with the correcting commit and resolved that exact thread. The native-floor
  gate is explicit in the vision, Phase 1 and matching Validation Plan, but
  no native version was selected or claimed tested.
- Three read-only source comparisons retained existing intent and owners.
  A narrower follow-up corrected an initial agency report before accepting it:
  function names and direct `save_record()` calls inside a registered handler
  do not establish a daemon bypass.

  | Subject / intent | Source result | Disposition / retained owner |
  |---|---|---|
  | Current head / succession | `tracking_lifecycle.py:436-549` separates head transition, conclusion and succession; `session_catalog.py:617-666` restores a protected live-catalog head, while `health.py:756-787` revalidates explicit orphan recovery under lock. | Existing head/succession intent retained. These legitimate recovery callers are not an unauthorized-head finding; broader admission/consumer coverage remains open under the head-hardening owner. |
  | Finalization / contribution roles | `finalize.py` retains live-resume rollback and provider-owned obligation handling; `pr_config.py:180-254` distinguishes configured role, provider permission and publication-only resolution. | No new source-proven role or teardown delta. Existing claim-transfer `#5842` and finality/role owners remain intact; no complete live-race acceptance is inferred. |
  | Daemon write authority | `tracking_write.py:269-333,454-482,567-690` registers real verbs, dispatches through discover/boot/capability validation, permits direct identical-handler fallback only before an ambiguous send, and refuses post-send retry. Production claims/follow-up/handoff/session callers use `dispatch`. | Withdrew global design-only/bypass claim. Phase 3 migrations exist; remaining Manager stamps and read/reconcile paths retain their respective control-plane/authoritative-daemon owners, including `#3761`. |
  | Archived listings | `list_cli.py:68-80,248-267` separates tracking-status selection from existing-checkout filtering; absent archived checkouts need `--all`. | This narrower predicate is not proof of every consumer's default archive exclusion. Complete retained-checkout/archive presentation remains a source-audit question, not a new violation asserted from the partial trace. |
  | Rescue / cold evidence | Logger architecture and sync rescue/replacement modules retain provenance, CAS/high-water and identity guards. `cold_store.py:207-241` resolves catalog candidates; `sessions.py:457-493` provides catalog review lookup. | Real foundations retained under `#5676`; qualified retrieval and full preservation/adoption are not proved by a catalog candidate or architecture claim alone. No live replacement was performed. |
  | Digest / daily records | `segmenter/read_digest.py:1-47` exposes local/remote/fallback reader modes; `chronicle/digest.py:1-116` groups daily manifests. Default `ManifestWriter` returns no rendered-log paths and deliberately leaves deferred reservations held. | Distinguish reusable readers and manifest preparation from all-session accounting and executable revisioned aggregates. `#5677`/`#5678` retain realization; same-holder retries stay legitimate. |
  | Portfolio map / effectiveness | `test-portfolio-rationalization` Phases 2-4 leave schema/census, contract mapping and focused falsification calibration open; collection or coverage alone does not satisfy those goals. | Existing `#1303` already owns these additive deltas. No redundant census/effectiveness issues or test-removal decisions. |
  | Tier / observability / growth | Real runner exposes guards/prepare/collect/tier/resource limits. The owner Phases 5-6 retain contract-aware feedback, per-plugin budgets, contribution declarations and before/after evidence. | Existing vision is a superset of the inspected policy and runner. Attribution-only markers do not imply measured assurance or justify inventing a mandatory hard gate absent the stated contract. |

- No missing vision-level positive was established by this cohort. Existing
  implementation plans, prototype/compiled presence and source references were
  not promoted into runtime acceptance. Portfolio execution/rationalization and
  logger accounting/released adoption remain owner obligations; uninspected
  head-admission, archive consumers, cross-root evidence and service contracts
  remain explicit sweep work. The parent effort stays Active.

### 2026-10-09 — Clean-room, venue-parity, Draft and explicit-cell proof boundaries
- UI/endpoint reconciliation merged as `#5914` with current-head Copilot
  approval, zero findings and actual required CI, then finalized. Fixed the
  reviewer-identified reversed endpoint IDs (`#5903` JSON shape, `#5904`
  cleanup race) and removed a newly invented unavailable UI category rather
  than changing the existing LIVE/IDLE/blank grammar without reviewed design.
- Compared three bounded read-only source tracks against the current leaf
  visions; integration base `077ef9d6b2ec` retains the source evidence and
  existing Draft/default boundaries. The separate Vault fallback proof used
  synthetic records only, without a file, network, secret or live service.

  | Inspected contract | Scoped source evidence | Decision / owner |
  |---|---|---|
  | Clean-room verdict admission | `tools/clean-room/verdict.sh`'s embedded reducer defaults missing failed-count evidence to zero. Actual reduction of `{}` returned `ok: true` and exit zero, while valid success/failure controls remained distinct. | Carved `#5921`; report existence/parsing is not sufficient outcome evidence. |
  | Solo composition witness | Bridge-solo declares without-base coverage, but `scenario.sh` emits INFO when the base is present and does not later reject that admission. | Carved `#5922`. Rejected the separate allegation that every read verb must return zero; that is not the scenario's contract. |
  | Programmatic lane | Tier P invokes Copilot as the subject rather than an agentic judge, but those witnesses still depend on model execution. | Carved `#5924` against the declared no-model lane; preserve useful subject coverage under a truthful lane rather than weaken the invariant. |
  | Repetition / judging | Runner setup occurs once and repetitions create fresh sessions, not independent fresh machines. The judge is an instruction contract; runner output reports `judged: false`. | Shared state is established, an accepted contaminated false pass is not. No live judge artifacts were examined; no blanket contamination or judge-compliance finding. |
  | Attached venue seed | Container accepts `--seed-file` but the attached caller forwards `args.seed` without reading that file; CodeSpace and detached routes do read it. | Actual parser/caller proof carved `#5926`; retain shared launch and detached behavior. |
  | Plugin-discovery fidelity | Shared remote repo-plugin resolution turns failure into empty composition and feeds the same ACP arguments as supported-empty discovery. | Carved `#5927`; caller recovery must preserve the distinction. Preference-read failure needs narrower authority analysis; no speculative target-authority violation. |
  | CLI default / prototype | ACP + Session Host remains the headless default; CLI is explicit. The default-disabled remote-driver prototype supports authenticated send/steer/abort/events, but the bearer token does not arbitrate exclusive mutation generation. | Carved `#5930`, linked to existing Phase 2. Separate detach orchestration is partial symmetry, not universal failure. No default promotion or sibling status change. |
  | Lifecycle / native proof | Existing downstream creation is ACP; termination is owned Host/process-tree lifecycle. `acp_agent.close_session` is an upstream adapter, not proof of a downstream native close. Repo-plugin staging and SDK-extension loading are distinct. | Corrected Draft and owning plan in place. Current native callback/extension support was not independently revalidated; retain launch-time and lifecycle proof gates, not unsupported absolute capability claims. |
  | Cell provenance | Namespaced Vault gate supplies explicit identity/scoped roots/port zero. `_discover_endpoint`'s WSL Windows fallback accepts missing installation identity from unscoped legacy locations despite the expected identity. | Pure actual-body selection of an unattributed synthetic record carved `#5918`; preserve ambient legacy behavior when no explicit cell was selected. |

- Superset review retains creation/fresh context, owned termination, driver
  fencing, every escalation rung, narrow TTY scope, independent launch-time
  extension proof and all default-promotion gates. No additional positive
  clean-room or venue-parity intent was missing; their violations are additive
  implementation work, not permission to weaken the visions.
- Review identified that the supported native-version floor was stated but
  not explicit in promotion track (a). Added functional launch-time proof at
  the selected minimum supported version to the vision gate, owner Phase 1
  and matching Validation Plan. No particular version was chosen or claimed
  validated; current-client coverage alone cannot close that gate.
- Publication changes documentation only. Source counterexamples are not fixes
  or fresh runtime/unit, clean-room, model, native-host or live-venue acceptance.
  Those obligations remain with the named implementation owners. Whole-index,
  remaining service invariants and this sweep's global Validation Plan remain
  open; the independent material-refresh objective is unchanged.

### 2026-10-09 — UI ownership/fidelity and endpoint discovery source reconciliation
- Guidance/provider-proof slice merged as `#5902`, current-head Copilot
  approval with zero findings and actual required CI, then finalized. The parent
  effort remains Active. Three read-only UI/installer tracks retained source
  versus runtime distinction; no Picker, venue or installer was driven.
- Evidence started at `d05137b297dd`. Within the inspected scopes, integration
  base `27a7fe57bb98` changed Picker prompt eligibility/forwarding, not the
  reported history/unknown-state or venue/endpoint effect bodies. Verified the
  concrete diff rather than treating a supplied SHA as perpetual HEAD.

  | Inspected contract | Scoped source result | Decision / retained owner |
  |---|---|---|
  | Bootstrap/core readiness | Real setup invokes the owning core installer; Manager update delegates the unified in-plugin flow; absent-Manager fallback executes. Filesystem `core_status` intentionally means artifact presence, while lazy stubs and complete-slot resolution supply different structural checks. | Withdrew false command-readiness/global repair-suppression claims. Readiness declarations and doctor output are not full consumer execution. Closed `#352`/`#1160`/`#1278` are historical foundations; active campaign/open phase ownership remains intact. |
  | Restart/failure handling | Setup's restart advisory is late, but independent provisioning need not abort all actions; bootstrap and the real installer have their own prerequisite/PATH checks. | No blanket unsafe-continuation ticket. Unsafe dependent-tool cases remain unproved and require targeted acceptance under the existing owner. |
  | Gitless source discovery | Initial tar bootstrap retains a full nested extraction and CWD setup can work. Installed external-CWD resolution checks ancestors/flat staging; gitless self-update retains Manager/libs, not core installer sources. | Carved bounded `#5910`; closed `#540`/`#551` fixed CWD/full-flat-staging, not every later staging shape. No global source-absence claim or Git mandate. |
  | Picker observation/protection | Unknown/opaque execution state is retained but its protection Boolean becomes ACTIVE/PROC/ACP in normalization; details omit uncertainty/age. Conservative active/unknown launch protection is valid. | Carved `#5909` for presentation loss, not a demand to set protection false. `#3935` is a distinct missing positive after confirmed mux launch. |
  | Sessions history | Local engine errors and remote failed envelopes become `[]`; the worker reports no error and the overlay says no registered sessions. A separate Messages error channel does not repair this route. | Carved `#5908`; genuine empty success must remain distinct from unavailable. No real SSH/session/history mutation. |
  | Neutral contribution/search | Production still requires the engine and hard-coded Worktrees data/view/actions. Its local filter omits codename/activity/claims. | Existing exact owners `#1193`/`#3587` retained; posted source evidence instead of duplicate tickets. Alternative contract app is not production conformance proof. |
  | Venue presentation/ownership | Both providers really declare grouped columns, subtitles, session/activity joins, claims/navigation and lifecycle gates. Generic enrichment supplies a separate task column, not the required subtitle title; Container supervisor-derived navigation and original-lease claims/session paths can diverge. | Corrected stale vision framing without weakening durable intent. Title precedence `#5912`; effort/supervision/action paths remain under `#3657`. Different provider lifecycle verbs are legitimate, not parity violations. |
  | Worker action capability | IDLE may mean holder-only; Send then explicitly rejects missing binding, Watch opens general UI, and unsupported-new-window Inspect falls back to ordinary Open. | Advertisement/binding mismatch is not silent delivery success. Existing `#3657` retains binding-aware actions and non-displacing Inspect; no live worker control. |
  | Declared creation | Generic row-independent create prompting/execution/refresh exists; venue manifests do not declare it. The archived campaign and closed `#3258` explicitly delivered design only. | Carved per-venue wiring as `#5913`, not missing schema/global creation. `#3507` retains real Docker-host validation and the archived original campaign remains Done for its transferred scope. |
  | Endpoint error/ownership fidelity | Shared and Dispatch tolerant readers access decoded JSON before object validation. Cleanup checks owner twice then unlinks by path, allowing a successor publication after the final read. Current shutdown consumers call that helper. | Actual API proofs were fully mocked and process-free: array JSON raised `AttributeError` (`#5903`); owner A was checked twice while synthetic B was deleted at final unlink (`#5904`). Closed strict-readiness `#1284` and PID-check `#1424` remain completed narrower slices, not full-contract proof. |

- Checked the newer cold-prompt source/vision amendment: local New retains a
  seed with No Mux and Resume prompt eligibility is cold-only. Picker already
  states the relevant intent under `#5415`, with hosting delivery evidence a
  separate authority. UI forwarding, comments and fake-capability tests were
  not treated as actual session consumption or live-race refusal.
- Verified tracker states instead of accepting local historical references as
  current owners: `#352`/`#355`/`#1160`/`#1278` are closed; `#356`/`#357`
  and `#5415` remain open. The active control-plane campaign retains its
  broader objectives. No completed foundation or archived design-only phase was
  reopened merely to represent this audit's narrower follow-ups.
- The venue revision retains the existing row grammar, exact title precedence,
  accumulating activity, ADO-scoped PR auto-claim, all eight tunable prominence
  tiers, contribution extensibility, live/resumable Open, row-independent New,
  both supervision directions and all non-goals. Historical implementation/
  gap-list prose was removed from standing concepts; source findings live here.
- These are documentation/source-audit artifacts, not runtime fixes. No live
  registry/endpoint, process retirement, provider action, Docker fleet or
  production UI was tested. New runtime unit/component, clean-room and live
  acceptance remain implementation-owner obligations. Whole-index, remaining
  service contracts and the global Validation Plan remain open.

### 2026-10-09 — Guidance attribution and hosting/provenance proof boundaries
- Durable conversation/evidence cohort merged as `#5895` with current-head
  Copilot approval, zero findings and actual required CI; its child finalized.
  A review-transition waiter timed out after baselining an already-present
  approval. Read the actual current-head reviewer/body and required checks
  instead of treating that timeout or its stale convenience note as a verdict.
- Source snapshots were verified through `e38752be78ad` and the relevant
  inspected files remained unchanged at integration base `1b0a9bc25ad0`.
  Three bounded read-only tracks retained coordinator ownership; no runtime,
  process, check-in or live hosting operation was used as a reproduction.

  | Subject / contract | Scoped source evidence | Decision and retained owner |
  |---|---|---|
  | Guidance delivery/ownership | Projection manager serializes local rendering, resolves contributors under its own lock, preserves foreign/tracked files, records errors and treats guidance budget excess as advisory within separate safety bounds. | Existing vision intent retained. Restored behavior/boundary placement after a duplicate `Non-Goals / Boundaries` heading; no policy was removed, weakened or silently reassigned. Full host/contributor acceptance remains open. |
  | Context-accounting attribution | `context_budget.py:_measure_files` catches `OSError` and drops the source; `build_context_budget` has no failed static/metadata-source entry. A fully mocked call to the real API with one unreadable synthetic source returned zero entries/bytes and no read-failure record. | Carved `#5896`. This was an in-memory shape proof, not a permission change, personal-guidance scan, dynamic hook execution or live-runtime test. Keep fail-open delivery separate from explicit audit uncertainty. |
  | Process registry | `agent-process-self-report-registry` architecture is explicitly Draft and disclaims an implemented/deployed broker. Placement, client/adopter, admission, reconciliation, journal/retention and validation remain unchecked. | Existing `#5559` owns realization. Stopped at the documented proposal gate rather than treating schemas, budgets or owner recommendations as enforced runtime behavior or searching absence into a global claim. |
  | Process telemetry | Dispatch coordinator and Bridge event reduction really publish lifecycle records. Optional sink installation and JSONL spooling are real; exception fail-open is not latency isolation. Shared procutil renderer/SSH calls provide spawn effects but no inspected connected process-publication proof. | Per-site adoption is explicitly optional. The never-delay promise concerns adopted spawn records, not every legacy lifecycle callback. `#2501` was vision-only authoring; scoped tracker searches found no realization owner, so carved `#5900` without claiming global absence or mandatory instrumentation. |
  | AHP presentation | Production `_run_launch` ensures/attaches AHP and returns through `launcher.launch`; effective default capability is unavailable despite mux requested. A process-free use of actual routing/composition bodies showed the same direct result for new/resume; the real PowerShell wrapping route is bypassed. | This is already expressly deferred by Phase 3b, not a new regression. Carved excluded scope as `#5898` and clarified the owner's injected-capability matrix proof versus production acceptance. `#5892`/`#5878` remain separate transparent Windows handoff work, not superseded. |
  | Handoff provider ownership | Direct agency-side mux creation/control/retirement remains in the inspected path. Identity guards and the deliberate zero-provider fallback remain meaningful; physical directory placement alone is not an ownership violation. | Rejected the blanket foreign-pane/ownership-violation claim. Posted the narrower provider-boundary reconciliation under existing `#2062`, preserving co-packaged owning adapters, standalone behavior, head/claims authority and safe retirement. |

- The registry source chain and full process-publication/hosting matrices are
  realization obligations, not completed by a proposal, script relocation,
  fake-capability test, opaque identity record or CLI command name. No
  SDK/App/third-party conformance or observed duplicate/foreign retirement was
  inferred.
- This publication changes documentation only. The budget reproduction proves
  the reported source omission, not its eventual fix. No new runtime unit/
  component, clean-room or live external acceptance is claimed; identified
  owners retain those gates. The original whole-index, remaining service
  invariants and global Validation Plan remain open.

### 2026-10-09 — Durable conversation/evidence and CI policy source cohort
- Host-resource reconciliation merged as `#5886` with current-head Copilot
  approval, zero findings and actual required CI; its child finalized. Replied
  on `#5875`'s low citation finding with that merged fix-forward. The Active
  sweep's whole-index and remaining service/Validation gates are unchanged.
- Source cohorts used bounded read-only inspection, not contract documents as
  runtime proof. Relevant plugin, vision and CI effect files were checked
  unchanged from `fca474463dcf` through integration snapshot `36dc5153009c`.
  Finalization could move a reference checkout's HEAD; the supplied base alone
  was not treated as a freshly observed HEAD.

  | Subject | Accepted source comparison | Retained boundary / owner |
  |---|---|---|
  | Efforts policy | `plugin.json` registers no hook; `instruction-projections.json` delivers the static completion fallback. `emit-policy.py`/`.ps1` directly validate adoption and emit bounded attributable policy; `.sh` is a staged wrapper. | Producers are not automatically hooked. The vision governs agent conduct and does not demand an executable completion validator. Archived owner: `efforts/2026/08/28 effort-driven-session-loops/README.md`. No new machinery delta was inferred. |
  | Task drafts and conversation | `queue_steering.save_card_draft/clear_card_draft`, client and coordinator routes persist unfinished answers separately from acceptance. Backend submission/review/history and outbox bodies provide concrete foundations. | Added only durable unsubmitted-draft intent. Default non-verification/self-attestation, optional exact-review fences, overwritten card/result revisions and consumption reconciliation remain with `#3681`'s Phases 5-7; source evidence was posted there. |
  | Logger association and derivation | `sessions.write_review_annotation` preserves durable association sidecars; `ReviewCatalogIndex.rebuild_from_sidecars` supplies rebuildable lookup. Sync, archive/segment readers, catalog, chronicle factory/source/writer and consumer seams were traced. | Added generic recorded work-item/session association retrieval, not a bundled tracker or workflow. Complete accounting, archive/rescue paths, aggregate execution authorization and released adoption were not declared conforming. Existing owners remain intact. |
  | Chronicle retry/admission | Default `ManifestWriter` prepares the same manifest and returns no rendered-log paths; same-holder re-reservation is intentional and tested. Independent same-holder calls can interfere through failure release, which checks state/holder rather than invocation. | Rejected the overbroad duplicate-rendered-log claim. Actual rendering and single-flight lease authority belong to the external host. Narrow active-call release/adoption evidence was posted on `#5678`; legitimate retries remain supported. |
  | Ordinary-CI targeting | `ci.yml:worktrees-smoke` runs collection and guards before invoking `coverage_guided_selection/cli.py` only in shadow mode, with a separate two-minute bound. CLI records selected/fallback/error decisions but does not execute them. | Audit complete; live activation and runtime-covered acceptance remain owner Phase 4 (`#4453`). Manual prototype integration is not ordinary PR selection or a replacement for the unchanged required gate. |
  | CI-failure response | `ci_failure_watchdog.py` detects, dedups and re-verifies signatures; trusted report/verification jobs and compiled agent-success/scope/threat/output gates preserve the ordinary draft contribution path. | Prompt intent triage is not proof of model compliance. Per-run output limits are not a signature budget. Closed-tracker recurrence can create a new issue/dispatch; existing owner Phase 3 leaves that policy undecided, carved as `#5890`. |

- Checked current tracker state rather than accepting historical references as
  ownership: `#5701` is the merged steer retry/idempotency PR, `#5668` the
  merged attention PR; neither owns the amendment implementation. `#3681`,
  `#1817`, `#5677` and `#5678` remain open canonical owners.
- Source-first superset review retained every existing positive, negative and
  ownership boundary in the two edited leaves. New passages record deliberate
  existing capabilities at intent altitude, not wire/schema/port details.
  No runtime changes, source cleanup, task mutation, live chronicle execution,
  concurrent render, or production CI-agent dispatch occurred.
- Documentation-only structure/whitespace and real PR gates cover publication.
  No new runtime unit/component, clean-room or external acceptance is claimed;
  identified implementation owners retain those obligations. Partial source
  paths above and the full parent completion gate remain open.

### 2026-10-09 — Host-resource current-stage and relay ownership reconciliation
- Read the complete host-resource vision against the credential relay README,
  `CredentialSource`, `RelayBuilder`, server actions and startup, and Bridge's
  provider-profile process boundary. Source snapshot `fca474463dcf` retained
  the inspected effect-producing files from the preceding slice. The vision
  already preserves provider independence, one authorized shared channel,
  session-neutral reach, bounded public surfaces and provider lifecycle
  ownership; no unsupported capability was promoted into a realized claim.

  | Vision / applicable service contract | Scoped result | Evidence / delta |
  |---|---|---|
  | Named pluggable capabilities | Partial: concrete credential-source foundation | `sources/__init__.py` defines credential supports/resolve and a source name; `registry.py:add_source` deduplicates that internal name. This is not a remotely discoverable noncredential capability catalog. `#5877` owns generalization. |
  | Shared channel and session-neutral reach | Credential foundation retained; generic acceptance unproved | Bridge's provider-profile CLI seam registers sources without importing provider packages on the primary path; relay actions and advertised capabilities remain credential-specific. The general resource contract must reuse the selected authorized channel without requiring a particular session-host mode (`#5877`). |
  | Provider-owned lifecycle and scoped public surface | General realization remains ahead | Credential policy, request-scoped token authorizers and provider-owned issuance are not a generic resource ensure/idempotency/release protocol. `#5877` preserves those authorities and requires synthetic-provider acceptance; `#5775` retains its distinct session-retirement work. |
  | Discoverable endpoint and collision-free live-owner preservation | Partial; pinned-port reclaim violates ownership | Server startup publishes the actually-bound endpoint and supports dynamic binding/fallback. But `server.py:100-120,288-335` calls PID termination after pinned-port contention without proving relay identity, staleness or yielded authority; only the current PID is excluded. Added this evidence to existing `#580`, whose expected live-occupant fallback already covers it. |

- The pinned-port defect is a reachable source branch, not an observed foreign
  process termination. Existing `test_port_reclaim.py` labels a generic
  listening child stale without an ownership proof. The requested fix must
  preserve live unrelated non-self listeners, verify any owned stale recovery,
  and retain safe fallback/actual-endpoint publication. No host service was
  evicted and no credential or noncredential provider was deployed for this
  comparison.
- Dedup retained `#580` for its explicit live-occupant fallback requirement;
  `#4011` remote-forward cleanup and `#4309` connection-owner architecture
  remain separate. `#5877` is north-star realization, not global absence
  inferred from search or an order to extend credential wire framing per
  capability. No custody policy or standalone operation was removed.
- Corrected the previous preparation table's citation from `88-181` to
  `88-170`, addressing the low finding on merged `#5875`; the source file has
  170 lines. Its substantive source-effect findings and open whole-leaf gate
  are unchanged. Preparation reconciliation merged and its child finalized;
  neither it nor this reconciliation completes the Active parent sweep.
- This slice changes audit documentation only. No new runtime unit/component,
  clean-room or live-provider proof is claimed; those obligations remain with
  the identified implementation owners.

### 2026-10-09 — Preparation, collection and assurance boundaries
- Traced current `pytest_portfolio_guard.py` declarations into
  `run-plugin-tests.py:run_plugin` and `plugin_test_containment.py`.
  The guard validates declared tier/effect combinations and explicit-tier
  access, while untagged tests remain permitted and contract attribution is
  informational. None is a complete contract map or effectiveness measurement.
  The owning triage framework keeps unknown evidence distinct from deletion
  proof and places census/calibration/removal gates under `#1303`.
- Folded back separable dependency/environment preparation and the distinction
  between executable collection and read-only inventory. Kept bootstrap
  capability, editable-source testing, existing tiers and safe cached reuse;
  did not weaken host/state/resource ownership to bless the preparation path.

  | Inspected contract | Scoped result | Evidence / delta |
  |---|---|---|
  | Declared tier/effect validation | Embodied, bounded to declarations | `pytest_portfolio_guard.py:88-170` rejects malformed/forbidden declarations and gates T3/T4; it does not prove actual effects or unique family value. |
  | Separable preparation | Embodied capability; ownership/budget violation | `_ensure_venv` uses direct unbounded `subprocess.run`; `run_plugin` calls it before sandbox, isolated environment, contained process and aggregate clock. `#5868` owns the gap. |
  | Contained execution/collection | Scoped source support, not whole-host proof | `run_plugin` loads the policy plugin inside `run_contained`; the worker waits for assignment, Windows uses Job ownership, POSIX uses group accounting/termination, and registry drift is detected without rolling back another actor's state. |
  | Cleanup-result fidelity | Violates on the suppression path | `TemporaryDirectory(ignore_cleanup_errors=True)` can suppress failed sandbox removal while pytest success survives; no subsequent cleanup verification establishes a clean host. `#5869` owns the gap. |

- Complete OS descendant/effect ownership, reproducible family census, runtime/
  reliability history, mutation evidence and new-growth metadata policy remain
  under the existing portfolio effort. No source-absence claim or redundant
  runtime ticket was manufactured from a missing marker or planned inventory.
  `#4123` fixture symlinks and `#2214` long-run resource accumulation retain
  their distinct ownership.
- These are source-effect paths, not unsafe live repros. No test family was
  removed, moved, or reclassified and no fixture/process storm was executed.
  No fresh unit, admitted component, clean-room or live portfolio proof is
  claimed; actual implementation fixes retain those validation obligations.
- Current-stage fleet reconciliation merged as `#5865` after quota-aware
  review recovery and real required CI. Neither that planning comparison nor
  this scoped source trace completes the parent sweep.

### 2026-10-09 — Machine-fleet current-stage ownership and excluded scope
- Compared the complete machine-fleet vision with the owning
  `machine-fleet-routing-foundation` README/architecture and current tracker
  `#5789`. The proposal explicitly makes no implemented-runtime claim and
  gates code behind plan review; source searches and catalog absence were not
  used as proof that no controller exists anywhere.
- Existing intent covers the inspected driver, service, credential, role,
  bounded-resource and lifecycle boundaries. No capability was removed and no
  spec-level wire/package choice was promoted into the vision. Controller/
  connector execution conformance is not established by a proposed contract;
  standalone, process, clean-room and live proofs remain in the foundation.
- Carved only genuinely untracked scope expressly excluded by that campaign:
  named deployment/config reconciliation (`#5861`) and subscribed coherent
  observation/recovery (`#5862`). Snapshot and replay are both valid recovery
  shapes; no replay-only requirement or rival task/worktree/session ledger was
  invented. Provider-specific enrollment, credentials and live migration stay
  adopter-owned, not new public implementation scope.
- Verified `#5771` is the provider-confirmed merged vision-authoring artifact
  for `#5767`; authoring is distinct from foundation/runtime delivery.
  Reported incorrect source-relative vision/lifecycle links cooperatively on
  `#5789`, then corrected only those paths and added the vision's realization
  planning link. The Draft plan's scope, status and review gates are unchanged.
- No runtime code, service, enrollment or venue was changed by this slice.
  Documentation structure, relative-target checks and whitespace are the
  applicable validation here, not a claimed Gateway process or deployment test.
  The original sweep's remaining indexed/source/service coverage and global
  Validation Plan remain open.

### 2026-10-09 — Installation boundary intent and thirteen-runtime matrix
- The source-effect comparison exposed a genuine scope question: source
  packaging cleanup versus higher-level declared anchor synchronization.
  The operator chose the two literal boundaries recorded in Request.
  Clarified the standing service vision without removing source installation,
  clean packaging, explicit integration, or source synchronization.
- Accepted concrete checkout cleanup, ambient hooksPath, and chosen-setting
  effects as `#5851`, `#5852`, `#5853`, and `#5855`. Rejected the higher-level
  updater's separate source-sync stage as an automatic installer violation.
  Generated-artifact deletion was not represented as observed tracked-file
  deletion; partial helper/backend routes were not marked conforming.
- Source citations are pinned and the effect-producing files were unchanged
  after pull-forward. Historical comment-only issue numbers were not accepted
  as ownership proof; an apparent migration reference resolved to an unrelated
  PR. Dedup used current trackers. Existing `#3444` packaging-fidelity work is
  preserved by staging, not superseded or weakened.
- Evidence agents executed no installers, tests, live configuration, or remote
  venue operations. Separate required consumer deployment follow-through is not
  a conformance test. Documentation structure/whitespace gates are appropriate
  here; real source-install, configuration, clean-room, and live platform
  acceptance remains required for the eventual implementation fixes.
- One fourth-issue posting command stalled without an acknowledgement. Stopped
  only that owned CLI process, verified non-creation through the authoritative
  unindexed issue listing, then performed one successful retry (`#5855`);
  no blind duplicate write or shared-service reset occurred.
- The original sweep remains Active, with the partial paths, every other
  applicable service invariant, remaining indexed visions, and global
  Validation Plan still open. The independent material effort remains Draft
  and execution-unassigned.

### 2026-10-09 — Claim acceptance atomicity and carrier retry corrections
- Traced the actual finalization gate through nonterminal handoff bundles,
  pending creation identities, offered reservations, and explicit transfer.
  Rejected a candidate based on the legacy `obligations.gate_mode` helper:
  the actual gate no longer lets that legacy mode release creator obligations.
- The acceptance path does violate existing `agent-fabric` resource-claims
  intent: `claim_handoffs.accept` calls `accept_source` before
  `_finish_accept_consumer_side`; source claims are removed and terminal
  accepted is persisted before consumer existence/conflict, resource ownership,
  lease transfer, and record-save checks can fail. Accepted is excluded from
  `active_bundle_ids_for_source`, so this can open source finalization without
  consumer responsibility being committed. Carved `#5842` after dedup and
  cross-linked closed contract `#1090` and distinct readiness gate `#1602`.
  No vision weakening, live claim manipulation, or implementation PR occurred.
- Review of `#5840` identified a separate carrier effect-admission gap.
  Confirmed `RemoteOperationService._request` retries reconnectable failures for
  mutating operations; `carrier_transport.py` marks lost pending responses
  reconnectable, while `carrier_requests.py` assigns fresh request IDs.
  A remote create or unkeyed live message may have completed before its response
  is lost, so bounded reconnection is not no-duplicate proof. Carved `#5843`
  rather than treating the new intent as realized.
- Kept the uncertainty-preserving vision guarantee and clarified that it covers
  same-route retry as well as alternate channels, while retaining safe proven
  idempotent/deduplicated retries and replayable observation. The inspected
  unsupported-capability fallback remains distinct from this retry violation.
- These are concrete source-effect paths, unchanged in freshly fetched trunk,
  not fresh live reproductions. Existing synthetic acceptance fixtures were
  inspected, not presented as fault-injection passes. Broader head, role,
  daemon, hosting, and service audits remain open.

### 2026-10-09 — Bounded evidence cohorts and accepted source coverage
- Three read-only evidence tracks mapped twelve indexed visions, then compared
  owning surfaces. The initial reports were partial: document contracts,
  legacy path names, design-only proposals, search absence, and effort checkboxes
  were not accepted as implementation conformance or gap proof. Proposed
  fold-backs that merely repeated existing vision promises were rejected.
- A narrower source follow-up established the coverage-guided CI foundations:
  `baseline.py` and `validate-and-promote.yml` measure pinned source form;
  `correlation.py` and `promote_release.py` validate and publish correlated
  pointers; `ancestor_resolution.py` preserves or invalidates source coordinates;
  `debt.py`, `decide.py`, `selection.py`, and `fallback.py` retain explicit
  eligibility, uncertainty, and tier-restricted fallback at the orchestration
  boundary. These are already covered by the vision's baseline correlation,
  source-form attribution, debt, graceful degradation, and auditable-fallback
  passages. No vision edit or duplicate runtime issue was warranted from this
  scoped comparison. The actual ordinary-CI consumer activation/invocation
  source path remains to be audited separately.
- That source audit is not proof of a fresh all-plugin promotion artifact, an
  actual ancestor/asset consumer run, or measured production fallback cost and
  coverage. Those acceptance obligations stay in the owning
  `coverage-guided-ci` effort. A design or pointer present in source is not
  itself a successfully published baseline.
- Mux Companion source follow-up traced read-only engine/handoff observations,
  the explicit normal Cut over handler, the engine trigger, and refresh. The
  inspected UI has no distinct raw force-head handler or action; carved the
  additive UI feature delta as `#5833` after issue/PR dedup. `#4369` owns normal
  manual-only cutover and `#84` owns ground-layer lifecycle, not this UI action.
  Missing
  enumeration/parse results may be omitted, which does not by itself prove
  uncertainty is sufficiently visible. Live summon, raw attribution, and
  other-provider validation remain open; no whole-leaf completion is claimed.
- Installation-cell source follow-up traced `resolve_context`, canonical
  receipt validators, `_activation_result`, `attribute_legacy_state`, and
  `invoke-payload-runtime.ps1`. Foreign, stale-generation, and ambiguous
  contexts have concrete refusal paths. Endpoint selection, POSIX dispatch,
  uninstall/rollback and every runtime remain unscored. Documented legacy roots
  alone were not accepted as a cross-cell violation.
- The other nine cohort visions retain incomplete source reconciliation. No
  source was edited by evidence agents; the coordinator owns integration,
  issue decisions, review gates, and the original sweep completion judgment.

### 2026-10-09 — Bridge shared-carrier routing/protocol slice
- Traced source snapshot `389b55303a67` from the public local remote-operation
  surface through bridge-owned carrier acquisition, connectivity-layer
  connection identity/pooling, far-side request gates and hosting-owned
  replay/cursor acknowledgement. Checked aggregate observation and a concrete
  Dispatch consumer without inferring conformance from archived effort status.
- Folded back shared transport over equivalent connection/access contexts,
  independently identified logical observations, hosting-owned durable replay,
  consumer acknowledgement, explicit reconciliation on continuity loss, and
  absence versus uncertain admitted effects as standing intent. Retained the compatibility path
  for explicit pre-admission refusal, including an older carrier rejecting a
  requested charter before creating work. No existing positive or intended
  provider, relay, hosting, or protocol capability was removed.
- Evidence anchors: `agent_bridge/carrier.py:acquire_remote_carrier`,
  `ssh_manager/manager.py:_carrier_transport_identity` and `acquire_carrier`,
  `agent_bridge/remote_operations.py:CarrierRequestRouter` and
  `RemoteOperationService`, `routes/remote.py:multiplex_remote_session_events`,
  and `agent_dispatch/bridge_remote.py` with `bridge.py`/`embody.py` consumers.
  The archived `persistent-ssh-carrier` effort (`#1763`) remains provenance,
  not fresh all-venue proof.

  | Applicable invariant | Scoped status | Evidence and retained delta |
  |---|---|---|
  | `a-la-carte-installability` / `graceful-composition` | Conforms in the inspected carrier/consumer boundary | Bridge owns transport through the shared connectivity library; Dispatch uses its public service boundary and preserves unsupported-capability fallback, not sibling runtime imports. |
  | `work-coalescing-singleton` / `process-count-scales-with-services-not-sessions` | Conforms in the inspected transport-sharing path only | The connection manager pools under complete transport identity and a lock; logical subscriptions retain leases and release them. This does not score all Bridge processes or service leases. |
  | `version-skew-tolerant-contracts` / `interoperate-across-version-skew` | Partial runtime audit | Request-specific versions reject unsupported semantics before admission and preserve older supported operations; this is not proof of every session-envelope/recovery/writer-fence path under `#1460`/`#1468`. |
  | Bridge `single-stream-message-admission` / uncertainty-preserving control | Violates in the mutating retry path | Lost responses are retried with fresh carrier request IDs even after possible effects; tracked as `#5843`. Safe unsupported-capability fallback is not proof of safe in-route retry. |
  | `endpoint-discovered-not-assumed` | Conforms in the inspected consumer boundary | The client resolves the active authenticated local endpoint; explicit endpoints must be loopback. Transport does not add a public prompt socket. |
  | `minimal-network-exposure` | Partial Bridge audit | Ephemeral authenticated loopback is not proof of the invariant's preference for a native local endpoint. The inspected carrier adds no new listener; native-endpoint conformance for the existing HTTP control plane remains under the broader transport audit (`#54`). |

- Broader bridge hosting/protocol and global service audits remain open.
  Existing cutover gaps `#5750`/`#5754` and mixed-contract work retain their
  ownership; this transport slice does not close them or file duplicate gaps.
- Native mapping reconciliation merged in `#5814`, after corrected canonical
  sections, resolved review feedback, current-head approval and actual required
  CI/PR gates. The sweep remains Active. Consumer refresh was attempted and
  ended nonzero; projection synchronization still refused local ownership
  conflicts without changing tracked files. No private deployment data is
  used as public conformance proof here.
- Focused contained carrier/remote-operation verification was attempted but
  refused a live host-admission holder. No lease was cleared. Source and
  documentation gates do not constitute fresh mixed-version, clean-room, or
  live SSH/venue execution; those proof tiers remain with the implementation
  and broader service-audit owners.

### 2026-10-09 — Remaining native mapping audit and sibling publication
- Finished native root/catalog/working-boundary source reconciliation against
  snapshot `ed8c1cfee`. The owning effort now records the evidence table,
  distinguishes native identity consumption from unproved native root/catalog
  mappings, and requires proof of the proposed native layout before migration.
  The standing vision already covers the inspected intended capabilities; no
  positive capability or boundary was removed or weakened.
- Kept implementation ownership under `#985` and its existing phase trackers;
  did not reopen `#987`, manufacture native contract proof, or create a duplicate
  mapping issue. The branch itself deploys no runtime; this is not a further
  plugin-services conformance result.
- The independent material proposal merged as `#5807` with current-head
  approval and required CI/PR gates. Recorded its planning-publication closure
  in the sibling plan and on `#5799`; execution remains unassigned and the
  sweep's overall Phase 2/3 and Validation Plan remain open.
- Focused local root/trust/session-contract tests could not obtain the shared
  host test-admission lease, including a bounded 180-second wait. No lease was
  cleared and no other test run was interrupted. This documentation-only slice
  makes no fresh native integration, clean-room, or live-host proof claim.
  Those lanes require native mapping changes and contract evidence in the
  owning convergence effort, rather than being substitutes for this source audit.

### 2026-10-09 — Test-portfolio shared-host admission slice
- Audited source snapshot `8a64b81af867` against the existing test-portfolio
  vision, `TESTING.md`, and owning effort `#1303`. Folded back the admission
  contract, not implementation grammar or resource constants. Preserved every
  existing positive, tier, and boundary; no negative requires removing a real
  capability. Corrected the owning plan's obsolete implication that guards and
  collection may bypass shared-environment protection.
- Scoped source evidence: `tools/_admission_protocol.py` defines the shared
  per-user host namespace; `run-plugin-tests.py` acquires before environment
  preparation and releases in `finally`; `_devcontainer_host_admission.py` and
  `run_tests_in_devcontainer.py` use that same host authority before container
  work. Existing admission tests cover contention, bounded retry, all-target
  lifetime, and guard/collection/preparation modes. Reading those tests is not
  a fresh execution result.
- Simple direct-host entry-point evidence: `run-plugin-tests.py --all --list`
  returned its twenty-suite inventory successfully without requiring admission.
  The prior focused run refused contention and its requested
  180-second wait expired; neither result proves successful admitted execution,
  stale-owner recovery, descendant cleanup, or full portfolio conformance.
- This tooling leaf deploys no runtime of its own; installer/service invariants
  are not newly scored here. Shared admission is embodied intent already using
  the common lease primitive, not a new service authority or a missing
  plugin-services invariant. No runtime gap was established or duplicate issue
  filed; broader portfolio work remains under `#1303`.
- The attempted focused contained-suite run was blocked by host admission.
  Clean-room and live Linux devcontainer execution were not exercised for this
  documentation-only slice; the present Windows host and source comparison do
  not substitute for those lanes. Whole-leaf and global sweep validation remain
  open.

### 2026-10-05 — Kickoff + Phase 1 slice
- Effort created as the tracker for the operator's vision-backport +
  material-refresh handoff directive; filed and claimed the umbrella issue
  `#5456` for the sweep.
- Opened a PR for Phase 1's three session-evidenced targets: folded back
  `#5400`'s run-waiter surfacing into the tasks-pane-ux vision (filing
  `#5452` for the documented delegated/relay implementation gap rather than
  describing it in the vision), folded back the reviewer recipe's standing
  stagnation-escalation behavior into the reviewer vision, and ran the
  Direction-1/Direction-2 invariant audit for agent-dispatch's runtime
  (added `inherits-runtime-service-invariants` to the agent-dispatch vision,
  since it hadn't cited the applicable `plugin-services` invariants; linked
  the pre-existing `#5356` as the Direction-2 conformance gap).
- The full invariant audit (Phase 3) and the wider vision sweep (Phase 2) are
  explicitly deferred to future sessions per the handoff's own sequencing
  note — this effort stays open across many slices.

### 2026-10-05 — Phase 3 slice (cutover/immutable-runtime audit)
- Audited every `agent-*` plugin's runtime-deploy path against two
  `plugin-services` invariants — `immutable-versioned-runtime` and
  `zero-downtime-cutover` — scoped to one specific case: a same-version
  update whose content changed. (`register-once-cutover-on-update`'s own
  distinct registration-stability requirement was **not** evaluated.)
  Recorded the conformance table in Phase 3 above.
- **First-pass result (superseded below):** opened PR #5469 classifying 6
  plugins as conforming based on their use of a versioned-slot build path,
  without checking the specific case that actually matters — a same-version
  update whose content changed, while a live daemon is running out of that
  exact slot.
- **Review caught the over-generous classification** (PR #5469 review,
  "Reclassify same-version rebuilds that violate immutable runtime"): the
  shared `versioned_runtime.py` primitive's incomplete-slot cleanup does
  *not* stop a live daemon before a same-version rebuild — that guard is
  per-plugin opt-in, and most plugins hadn't added it. Re-audited each
  plugin's actual same-version-changed-content path and corrected the
  table: only `agent-bridge` and `agent-index` actually implement a
  stop-before-rebuild guard; `agent-vault`, `agent-codespaces`, and
  `agent-worktrees` (including the very `#2174` fix that originated this
  whole pattern) are missing it — filed as a consolidated `#5472`, initially
  (and, per the next round, wrongly) citing agent-bridge/agent-index as the
  reference-correct pattern to port. `agent-mcp` was flagged as needing
  confirmation rather than asserted, since its earlier venv-build step
  wasn't fully traced.
- **Review went a round deeper and corrected a second over-generous call**
  ("Stop-before-rebuild still violates immutable runtime and zero-downtime
  rules"): stopping a daemon before rewriting its active slot makes the
  rewrite *lock-safe*, but the vision's `immutable-versioned-runtime`
  invariant is "once a version's venv is built it is never edited in
  place" — full stop. Lock-safe is not the same claim as immutable, and the
  stop-rebuild-start cycle is itself a no-service window, so it ALSO
  violates `zero-downtime-cutover`. Re-audited once more and found the
  actual reference-correct plugin: **`agent-pull-requests`**, which refuses
  to rebuild a completed same-version slot with changed content at all
  ("bump the plugin version instead... exits 1 even under `-Force`") —
  confirmed `agent-mcp` as a violation (not "needs confirmation") per the
  reviewer's precise citations, and reclassified `agent-ssh`,
  `agent-containers`, and `agent-machines` from blanket "N/A" to
  **violates immutable-versioned-runtime** (no-daemon only makes the
  *cutover* invariants N/A, not immutability itself — they unconditionally
  reinstall into an existing slot with no refusal). Net: almost every
  `agent-*` plugin violates `immutable-versioned-runtime` for this case;
  `agent-pull-requests` is the one plugin to actually copy from. Updated
  `#5472`'s framing to match (dropped the "port agent-bridge/agent-index"
  fix suggestion, which review correctly identified as prescribing a
  non-conforming pattern) and flagged that `#5468` (agent-logger) needs a
  similar correction — its tracked fix only closes lock-safety, not
  immutability.
- **Review caught a third, more subtle misclassification** ("agent-index
  mutates live slots before cutover"): agent-index's `$targetWasActive`
  stop-then-remove-and-rebuild branch only runs when the existing slot is
  *stale/incomplete* (`$slotReady` false); when the slot is healthy and
  marked complete — exactly the "nothing to rebuild" case — that branch is
  skipped, but the subsequent `uv pip install --reinstall-package`/
  `--refresh-package` steps run **unconditionally** into that same live
  venv regardless, with `Invoke-ServiceCutover` called only afterward.
  agent-index was never actually lock-safe at all — reclassified alongside
  the unsafe-race group (agent-dispatch/vault/codespaces/worktrees/mcp),
  leaving `agent-bridge` as the *only* plugin that is lock-safe (while
  still non-conforming); updated the net finding, `#5472`'s issue body, and
  this Journal's own prior entries accordingly. Also narrowed an entry
  above and the PR description that still implied
  `register-once-cutover-on-update` was evaluated — it was not; only
  `immutable-versioned-runtime` and `zero-downtime-cutover` were scored.
  This is now the third round of review catching a genuine scoping or
  classification error in this same audit — a useful reminder that
  "appears to stop before rebuilding" needs to be traced all the way
  through the function, not inferred from the presence of a stop call
  somewhere in it.
- Checked for an invariant-vision blind spot (Direction 1, upward fold):
  none found — the vision's existing wording already states the correct
  contract precisely (a built slot is never edited in place, full stop,
  not "edited in place unless lock-safe"); the gap is conformance across
  nearly the entire ecosystem, not an imprecise or missing invariant.
- **Not yet done:** the rest of `plugin-services`'s ~20+ other behaviors
  (self-contained-runtime, single-instance-lease, work-coalescing-singleton,
  discoverable-local-endpoint, etc.) were not audited this slice — only the
  cutover/immutable-runtime angle `#5356` originally surfaced. A future
  slice should widen Phase 3 to the rest of the invariant list before
  calling it fully done.

### 2026-10-06 — Fix-direction correction: point at the existing `dev` slot
- The operator flagged, after PR #5469 merged, that the fix direction in
  `#5472`/`#5468` ("port `agent-pull-requests`' refusal pattern") was
  incomplete: a refusal alone leaves no sanctioned fast inner loop for
  genuine same-version iteration, and a separately-corrupted (not just
  content-mismatched) slot needs its own repair story.
- This repo already has exactly the mechanism the operator described for
  the iteration side: `efforts/active/mutable-dev-slot/` (design doc
  `docs/patterns/mutable-dev-slot.md`) built a protected, claim-gated,
  genuinely mutable `versions/dev` slot per plugin — piloted on
  `agent-codespaces`, not yet rolled out to the other 12 vendoring plugins
  (13 total, including the non-`agent-*` `budget-guidance`; its own
  Phase 3, previously unprioritized).
- Added the operator's corruption-repair instinct as a new doc section —
  but a first draft of it (stop, delete the whole slot, rebuild fresh at
  the *same* version) was itself flagged by that PR's own review as
  violating `immutable-versioned-runtime`'s rollback guarantee (reusing a
  retired identity for a new build is still a rewrite of what that identity
  means). Reworked to the correct shape: a broken-slot repair is an
  ordinary generation cutover — build the replacement under a **distinct**
  generation identity, promote it only once health-gated, then drain and
  retire the broken slot through the exact same serialize/promote-before-
  retire/drain discipline `graceful-daemon-cutover` already requires for
  every other cutover path — not a delete-and-reuse shortcut.
- Updated `#5472`/`#5468` to point at both pieces; prioritized
  `mutable-dev-slot`'s Phase 3 rollout list with the specific plugins this
  audit found. This is a fix-direction correction only — the Phase 3
  conformance table and classifications above are unchanged.

### 2026-10-06 — Phase 2 slice: `agent-codespaces` vision reconciliation
- Picked Phase 2 (widen the vision sweep) off the four-option Next Slice
  menu from the prior handoff. Ranked candidate visions by staleness ×
  commit traffic since each one's own last-revised date (`git log
  --since=<last-revised-date> --oneline -- plugins/<name>` counts, a
  single consistent metric); `agent-codespaces` (last revised 2026-07-31,
  313 commits since) ranked above the other stale candidates (`agent-ssh`,
  last revised 2026-07-22, 139 commits since; `agent-containers`, last
  revised 2026-08-27, 123 commits since — both legitimate follow-ups for a
  future slice).
- Read the vision in full against the plugin's current `README.md` and
  `docs/patterns/codespace-repo-provenance.md`. Found two substantial,
  already-shipped capabilities the vision never stated at all: the
  active-plugin-sourced venue-policy seam (`codespaceConfig` declaration,
  lower precedence than adoption, identity-verified root resolution) and
  in-venue plugin injection (the governing harness plugin's
  `codespacePlugins`, staged from a local marketplace when needed). Folded
  both back as new Concepts + Features (*repo-sourced-provenance*,
  *in-venue-plugin-injection*). Also found the already-shipped interactive/
  headless dual-mode CLI sessions (`copilot <name>` / `--detach`), the
  orchestrator-blind `--ref-file` hand-off, and the `--reverse-forward`/
  `--forward` port bridging entirely unstated under
  *coordination-layer-provider* — folded back as *dual-mode-session-reach*.
  Ran the superset check on each addition: all are reality already doing
  this; nothing contradicts an existing Non-Goal or scales the vision back.
- Opportunistically corrected a now-stale "agent-containers (when
  authored)" sibling-leaf reference (that vision has existed since before
  this slice) and widened the Reality-docs skill list to include
  `recovering-codespaces` and `cleaning-codespaces`, both of which already
  exist and already operationalize behaviors the vision states
  (*recover-not-lose*, *credential-readiness-verified-end-to-end*).
- No conformance gap was found requiring a new issue — this slice is
  fold-back only, distinct from Phase 3's separate `install.ps1`
  conformance audit (`#5472` already covers agent-codespaces there and is
  unaffected).
- **PR #5575 review, round 1:** caught two real contradictions in the first
  draft: *dual-mode-session-reach* over-claimed "never two divergent code
  paths" when reality actually routes a detached session through
  `copilot_detach.cmd_detach` and an attached one through a separate
  `interactive_ssh` path; and *repo-sourced-provenance*'s "no adoption
  step" directly contradicted *config-by-adoption*'s pre-existing "adoption
  is the one act" wording. Fixed by scoping the adoption claim to the
  repo-owned policy lane specifically (the two provenance lanes compose,
  they don't compete) and by narrowing the dual-mode-reach claim to the
  shared contract rather than a shared implementation.
- **PR #5575 review, round 2:** the round-1 fix for the code-path finding
  overcorrected — it added a "not yet fully realized... north-star-ahead
  refinement" sentence that (a) pinned implementation wiring the vision's
  own "Not a specification" boundary explicitly excludes, and (b) quietly
  turned a no-gap fold-back slice into one with an untracked vision→reality
  delta, contradicting the PR's own no-gap claim. Also flagged the PR
  description was missing this repo's required **Documentation impact**
  statement (`CONTRIBUTING.md`'s pre-PR checklist). Fixed by dropping the
  offending sentence entirely (the behavior now states only the should-be
  contract guarantee, leaving the implementation-path question genuinely
  unpinned) and by adding the statement to the PR description.
- **PR #5575 review, round 3:** caught a genuine cross-vision contradiction
  the first two rounds missed: `visions/remote-interactive-sessions/
  README.md`'s own Purpose & Intent framing still claimed (stale against
  its *own* later provenance) that a remote venue "is reached only through
  venue-parity's headless... dispatch core" and an operator "has no
  first-class path" to an attended remote session — directly contradicting
  *dual-mode-session-reach*'s now-documented `copilot <name>` /
  `--detach` venue-launch primitive. Scoped *dual-mode-session-reach*
  explicitly to the venue-launch layer, deferring the deeper
  coordination-layer integration (reservation, `live_sessions`
  discoverability, honest marking) to `remote-interactive-sessions`. Also
  reconciled that vision's own stale framing in place (its Purpose & Intent
  had not caught up with its own 2026-09-20/09-22 provenance entries that
  already added the venue-launch primitive) and added a two-way See Also
  cross-link naming the ownership split. This is itself a small, in-scope
  Phase 2 fold-back on a second vision, carried by the same review round
  rather than deferred to a separate slice.
- PR #5575 merged; worktree finalized.

### 2026-10-07 — Operator follow-up: attended sessions must stay bridge-driven
- Operator confirmed, after reviewing this slice's summary, that attended
  (human-interactive) remote CodeSpace sessions should still be driven via
  agent-bridge, not treated as a bridge-invisible raw SSH mux escape hatch
  — exactly the coordination-layer gap `remote-interactive-sessions`
  already defers to from `agent-codespaces`' `dual-mode-session-reach`.
- Searched for an existing tracked issue before filing (dedup discipline):
  found `#3346` ("Detached (agent-facing) CLI-mode venue sessions..."),
  which proposes the matching daemon-port-reverse-forward +
  `live_sessions` registration machinery but is explicitly scoped to the
  **detached**/agent-facing launch only — no issue covered the
  **attended**/human-interactive counterpart.
- Filed `#5614` for the attended case, cross-referencing `#3346` and the
  two visions (`remote-interactive-sessions`'s stated gap,
  `agent-codespaces`'s `dual-mode-session-reach` deferral). Left a
  cross-link comment on `#3346` pointing back.
- No vision text changed this entry — the visions already correctly state
  this as the should-be contract; only the tracked-issue gap needed
  closing.

### 2026-10-07 — Self-correction: #5614 was a phantom gap, both visions were stale
- While scoping the next Phase 2 vision (`agent-ssh`), its plugin README's
  `copilot <ssh-target>` description ("if another process already holds the
  bridge reverse-forward route, the attached command reuses that route")
  read as inconsistent with the "daemon port isn't reverse-forwarded yet"
  claim just relied on to file `#5614`. Checked directly against
  `copilot_venue.py` (`cmd_copilot`/`_cmd_copilot_connect`) and the owning
  effort, `efforts/active/agent-bridge-cli-mode-sessions/README.md`: the
  attended `copilot <name>` path already places a worktree-keyed
  reservation, a Connection Owner tenant hold carrying **both** the
  credential-relay **and** a daemon-port reverse-forward, and
  self-registers into `live_sessions` marked `driven_by: cli-mode` —
  landed and **live-clean-room-validated end to end on 2026-09-20/22**,
  weeks before this slice's PR #5575 was opened.
- This means the prior entry's premise was wrong on two counts: (1) `#5614`
  described a gap that doesn't exist — **closed it** with the evidence
  above, and corrected the misleading cross-link comment left on `#3346`.
  (2) The `remote-interactive-sessions` and `agent-codespaces` vision text
  PR #5575 shipped was *also* wrong — it preserved/extended a stale
  "coordination-layer integration still missing" claim instead of
  recognizing it as already realized. Root cause: round 3's correction
  trusted `remote-interactive-sessions`' own existing prose as ground
  truth instead of checking it against the owning effort/code, the same
  verification gap that produced `#5614`.
- Corrected both vision files in place (not a revert — a second, verified
  correction): `remote-interactive-sessions`' Purpose & Intent and "CLI
  mode needs no Session Host" concept section now state the integration as
  realized, citing the owning effort's Phase 4/Validation Plan; its
  Provenance records this as superseding the 10-06 entry rather than
  silently overwriting it. `agent-codespaces`' `dual-mode-session-reach`
  no longer claims the integration is "deferred" to a sibling vision.
  Checked off that owning effort's own Phase 5 "confirm realized behavior
  against the vision" item, since this sweep is exactly that confirmation
  (its sibling Phase 5 item, plugin docs/architecture sync, is unaffected
  and still open).
- **Lesson for the rest of this sweep:** a vision's own prose is not
  sufficient evidence for "is this still true" — verify against the owning
  effort's journal/validation-plan and, when in doubt, the actual code,
  before either folding something back *or* preserving an existing gap
  claim. Apply this going forward, not only when a reviewer or a sibling
  plugin's README happens to surface the inconsistency.
- Next: continue Phase 2 by picking the next stale/high-traffic vision
  (candidates from the prior ranking: `agent-ssh` 139 commits since
  2026-07-22, `agent-containers` 123 commits since 2026-08-27) — with the
  verification lesson above applied from the start this time.

### 2026-10-07 — Phase 2 slice: `agent-ssh` vision reconciliation
- Operator clarified the sweep's standing methodology (recorded here for
  future slices): visions are the **shared, published superset** — folded
  back from reality, with gaps/contradictions never described as prose in
  the vision itself. Any genuine gap/contradiction found gets a **GitHub
  issue**, not a note left in the vision. Efforts (private in dotfiles, or
  this repo's own in-repo `efforts/active/` convention) exist to *drive*
  closing those issues or land a specific build-out — downstream of the
  issue, never a substitute for filing one.
- Picked `agent-ssh` (139 commits since its single-day 2026-07-22
  authoring, never revisited) per the prior ranking. Read the vision in
  full against `plugins/agent-ssh/README.md`. Applied the verification
  lesson from the self-correction above throughout — checked each
  candidate fold-back against the plugin's actual shipped CLI/behavior
  (not just vision prose), and before reconciling the venue-reach
  capability specifically, cross-checked `remote-interactive-sessions`'s
  own subject line (confirmed it already names "any agent-ssh-reachable
  machine" as a venue type, so this fold-back is consistent with — not in
  tension with — that vision too).
- Found and folded back five previously-unstated, already-shipped
  capabilities (see the Phase 2 checklist entry above for detail):
  *local-process reach* (`wsl` transport), fragment
  provenance/staleness detection, continuous mesh self-healing via
  `agent-machines`' watchdog, a new *Machine maintenance escalation*
  concept, and *venue-contract-reach*. The last required resolving a real
  **vision-text contradiction** (the "Not a venue provider" Non-Goal vs.
  the shipped `copilot <ssh-target>` verb) — reworded the Non-Goal rather
  than filing a bug, since the contradiction was in the vision's own prose
  against already-working reality, not a functional gap or inconsistency
  in the system itself.
- **PR #5627 review** caught two real issues the slice missed: (1) the
  *local-process reach* wording overstated the `wsl` transport as
  needing "no daemon" — it avoids the Windows→WSL network hop only; WSL's
  own `sshd` + a keepalive are still real prerequisites. Corrected in
  both the Features and Provenance text. (2) A **genuine pre-existing
  implementation gap**, exactly the kind this methodology exists to
  surface rather than paper over: `refresh_mesh` writes a fragment whose
  provenance points at a registry file inside a `TemporaryDirectory` it
  then deletes, so the audit the vision's `derived-ssh-config` fold-back
  describes reports a false `missing-target` and blocks the refreshed
  aliases. Searched before filing (dedup discipline) — already tracked
  independently as `#5478` (filed before this slice, same root cause,
  confirmed by direct code read of
  `plugins/agent-ssh/src/agent_ssh/mesh_refresh.py:113-153`). Cross-linked
  rather than refiled; left a comment on `#5478` pointing back at the
  vision sections now citing it. This is the methodology working as
  intended: the should-be text stands (self-healing is the correct
  intent), the tracked issue owns the gap in realizing it, no prose in
  the vision describes the gap itself.
- PR merged; worktree finalized.

### 2026-10-07 — Phase 2 slice: `agent-containers` vision reconciliation
- Continued Phase 2, picking `agent-containers` (123 commits since its
  2026-08-27 last revision) per the standing ranking. Read the vision in
  full against `plugins/agent-containers/README.md`, applying the
  verification discipline from the two prior corrections: checked each
  candidate fold-back against the plugin's actual shipped CLI/config
  surface, not just vision prose.
- Found and folded back three previously-unstated, already-shipped
  capabilities (see the Phase 2 checklist entry above for detail):
  *interactive-venue-reach-trusted* (the same CLI-mode venue contract
  agent-codespaces/agent-ssh already got folded back, now shipped here
  too — trusted-only, restricted venues correctly excluded),
  *restricted-venue-picker-discovery* (Worktree Picker source
  registration for a restricted venue), and host-backed
  persistence/`systemd_capable` self-maintenance extending
  `full-harness-projection-trusted` — the last notable because the
  vision's own text had already hedged it as "as the capability matures,"
  so this is the should-be aspiration catching up to reality rather than
  reality drifting from should-be.
- No vision-text contradiction this time (unlike `agent-ssh`'s Non-Goal
  rework) and no genuine code gap found requiring a new issue — every
  fold-back traces to real, working, already-documented capability.
  `init.ps1`'s nonconformance is already tracked by `#5472`.
- **PR #5631 review** caught three real nits: an `install.ps1` →
  `init.ps1` reference mix-up (agent-containers' own installer script, not
  the other plugins' `install.ps1`); an implementation-progress phrase
  ("not yet every fleet...") that didn't belong in the vision's standing
  intent (moved here, removed there); and an inaccurate claim that both
  CLI-mode reach modes share one forward-keeper owner — corrected to
  distinguish attached (forwards on the interactive SSH process itself)
  from detached (a separate host-side keeper, independent of the
  launcher's lifetime), verified against `copilot_venue.py`/
  `copilot_detach.py`.

### 2026-10-07 — Phase 2 slice: re-ranked remaining visions, confirmed `agent-worktrees/pull-requests`
- Re-ranked the remainder of `visions/README.md`'s ~29-vision index by a
  single consistent metric (`git log --since=<last-revised-date> --oneline
  -- <plugin-path>` commit counts), since the three prior stale/high-traffic
  candidates were all now reconciled. Top candidates:
  `plugins/agent-worktrees/pull-requests` (372 commits since 2026-09-20,
  though this counts the whole `agent-worktrees` plugin directory since
  the sub-vision has no narrower path of its own), `plugins/agent-worktrees`
  itself (102 commits since 2026-10-02 — very fresh but still hot),
  `plugins/agent-index` (71 since 2026-09-18), `plugins/agent-bridge` (52
  since 2026-09-28), `plugins/context-handoff` (41 since 2026-09-13).
  Picked `pull-requests` as the most tractable, clearly-scoped leaf vision
  among the top candidates (the parent `agent-worktrees` vision is a much
  larger undertaking better left to its own dedicated slice).
- Applying the verification discipline from the two prior corrections,
  checked every candidate fold-back against the real `PRProvider` protocol
  (`providers/base.py`), the real GitHub provider (`providers/github.py`),
  and the actual CLI surface (`pr_state_cli.py`) — not just vision prose.
  Found the vision already in excellent shape: two of the three gaps its
  own 2026-09-14 authoring named (`reviewer-capable-provider`,
  `conformance-verified-mock-provider`) are fully realized in code; the
  third (`foreign-repo-pr-operations`) is partially realized — five
  commands support it, four don't yet.
- Found and closed **two stale GitHub issues** that were still open against
  already-shipped capability — exactly the inverse of the `#5614` mistake
  three slices ago (that time, a vision claimed a gap reality had already
  closed; this time, tracked issues claimed gaps the vision's own realized
  state had already closed). `#2699` (reviewer-side PRProvider operations)
  and `#2691` (mock PRProvider) were both closed with direct code
  citations. The third sibling issue, `#2700` (foreign-repo addressing),
  was **narrowed, not closed** — `pr-watch`/`pr-merge`/`create-pr`/
  `pr-ready`/`pr-abandon` all support it, but `pr-status`/`pr-diff`/
  `pr-comment`/`pr-review` genuinely don't yet, a real remaining gap the
  vision's own "symmetry" north star names.
- No Feature/Behavior text needed changing — it was already accurate
  should-be prose with no gap-list language. The vision's **Purpose &
  Intent opening**, however, had genuinely gone stale (it still described
  reviewer-side operations as entirely missing) — updated that narrative
  to match reality, added the Reality-docs pointer (`pr-workflow.md`), and
  recorded a Provenance entry that correctly states two gaps realized and
  one partially realized (PR #5639 review caught an earlier overstatement
  that called all three fully realized, contradicting this same entry's
  own narrowed-`#2700` note two sentences later).
- Next: continue Phase 2 with the next vision — `plugins/agent-worktrees`
  itself (102 commits since 2026-10-02) or `plugins/agent-index` (71 since
  2026-09-18) are the next candidates by the same ranking; `agent-worktrees`
  is large enough to warrant scoping before diving in.

### 2026-10-07 — Phase 2 slice: `agent-index` vision reconciliation
- Picked `plugins/agent-index` (70 commits confirmed since its 2026-09-18
  last revision) over the much larger `plugins/agent-worktrees` mega-vision,
  per the prior slice's own recommendation to take the more tractable
  single-PR win first.
- Read the vision in full, then cross-checked candidate drift directly
  against source (`config.py`'s `read_corpus_sources()` docstring,
  `sources/git_repo.py`, `indexing/engine.py`'s `SourceSpec`, and the
  `libs/dropin-registry` package) rather than trusting commit-message
  summaries alone — most of the 70 commits turned out to be bug fixes
  (CPU-priority throttling that silently never worked, FTS rebuild
  recovery, server-venv packaging, dependency ceiling typos) or
  cross-cutting infra shared with many other plugins (peer-launch and
  CWD-compliance, generic infra out of this vision's own scope; the
  mutable-dev-slot-pattern same-version-rebuild conformance gap, which
  does apply to agent-index specifically, is already tracked by `#5472`/
  Phase 3), not new vision-worthy capability.
- Found three genuine fold-back candidates: (1) a `git:` source now
  accepts a per-source `ref:` override + `auth.account` authenticated
  fetch (#4829) — this directly sharpens `continuous-delta-freshness`'s
  prior "tracks the canonical default branch" framing, which read as an
  absolute rule, into a default with a named, deliberate exception; (2) the
  effective `corpus.sources` is dynamically **grafted from every
  locally-adopted project** (via the sibling agent-worktrees project
  registry), not a single hand-authored list — the concrete mechanism that
  was always implied by "a harness repo and its close ecosystem" but never
  actually described; (3) the `providers.d/` external-content-domain-
  provider discovery now runs on the extracted, shared `dropin-registry`
  library — confirmed agent-bridge's own namespace resolvers consume the
  same library (not merely a similar pattern), sharpening that citation.
- No new conformance gap or stale/phantom issue found this slice — the
  vision's existing Non-Goals ("not a facility-wide, all-source
  aggregator") already bounded the multi-project grafting correctly, so no
  contradiction needed reconciling there.
- Next: continue Phase 2 with `plugins/agent-worktrees` itself (needs its
  own scoping pass — one section/concept per slice, not the whole ~52KB
  file at once) or `plugins/agent-bridge`/`plugins/context-handoff` (not
  yet investigated).

### 2026-10-07 — Phase 2 slice: `context-handoff` continuity and recovery
- Read the full leaf vision and reconciled 41 commits since 2026-09-13
  against the plugin README, continuation skill, SDK-free store/trigger/
  consume core, extension pressure path, policy predicates, CLI surface,
  shared successor directive, and `context-handoff-overhaul`'s owning plan.
- Fold-back: durable storage versus live-pickup authorization, manual entry
  points independent of automatic policy, extension-free recovery,
  effort-backed compact batons, preserved objective/delegate authority,
  completeness verification, and exclusive attributable pickup. Exact
  command grammar, thresholds, lock formats, retention counts, and recovery
  procedures remain implementation detail, not new vision specifications.
- Superset check: retained every original Feature/Behavior and hosting
  boundary. Manual operation remains legitimate; emergency uncertainty is
  explicit; source-control refresh does not justify committing unrelated
  work or losing the baton. Added no unintended teardown requirement.
- Two genuine deltas, deduped before filing:
  `#5683` covers automatic force-tier transfer while owned background work
  remains active (the skill explicitly documents this exception);
  `#5684` covers `consumeFileHandoff()`/`consumeDispatchHandoffTask()`
  discarding `safePromoteHead()`'s outcome, so a failed/diverged lineage
  update is not visible in the otherwise-successful consume response.
  Guarding a diverged head is correct; hiding that outcome is the gap.
- Closed two stale delivery trackers: `#2595`'s force tier is implemented
  by `autoForceHandoff()`, the policy gate, and SDK permission classification;
  `#2596`'s explicit background inventory and standing mandate are delivered
  by the templates, shared constants, consume formatter, and sessionStart
  writer. Both are marked complete in the owning overhaul plan and backed
  by merged `#2643`/`#2663`. These closures do not close overhaul umbrella
  `#2594`, or equate content capture with automatic quiescing.
- Focused contract evidence: 36 existing Node tests pass across pressure
  thresholds, force-tier classification, manual/automatic modes, and
  successor-seed/mandate behavior. No runtime, hook, or plugin payload changes;
  no changefile or installer gate applies. No new live cutover or clean-room
  run was performed: this slice changes standing documentation and trackers,
  not execution/provisioning. Source inspection, not those pure tests, is
  the evidence for the two newly filed lifecycle gaps.
- Scoped service-invariant audit (not the remaining repo-wide Phase 3):

  | Contract | Status | Evidence / delta |
  |---|---|---|
  | Graceful composition / degrade gracefully | Conforms in inspected paths | `storeHandoff()` selects task or file storage; the CLI shares the extension's core; absent extension/host gets explicit recovery guidance. Safe storage still requires an adopted worktree/state namespace. |
  | Install/adopt boundary | Conforms | Payload-only delivery has no runtime installer; sessionStart writes session guidance, not repository adoption configuration. |
  | Relationship diagnosability | Partial | CLI lineage queries and consume predecessor/worktree identifiers exist; discarded promotion outcomes are `#5684`. |
  | Immutable runtime / daemon cutover / single-instance service / endpoint exposure | N/A for this plugin | No installed venv, resident service, or listening endpoint; those contracts apply to its runtime providers, whose full audit remains Phase 3. |
  | Exclusive handoff ownership | Conforms in inspected file-delivery path | `consumeFileHandoffOnce()` locks and re-reads, names a different claimant, and supports same-session retries; no live cross-process stress run this slice. |
  | Safe background-work transfer | Partial | Agent-driven guidance captures/quiesces before sync; automatic force-tier path cannot do so (`#5683`). |
  | Payload replacement / hook transience | Design inherited; not freshly stress-tested | SessionStart writer and non-extension CLI keep guidance/recovery independent of the live extension. No claim of runtime-reload conformance from documentation inspection alone. |

- No new cross-cutting invariant blind spot identified: the leaf cites
  composition, diagnosability, and replaceable payloads without restating
  service mechanisms it does not own. Audit/gap prose stays here and in
  issues, never in the vision.
- Next: scope the parent `agent-worktrees` mega-vision into one coherent
  section per slice, or reconcile `agent-bridge`; branch visions
  (`agent-fabric`, `native-convergence`, `plugin-services`) and the remaining
  Phase 3 invariants still await their own audits.

### 2026-10-08 — Phase 2 agent-bridge observation/attribution slice
- Read the full agent-bridge vision, then scoped this slice to one coherent
  observation contract rather than claiming the entire leaf reconciled.
  Fold-back evidence: presence and consumption (`#5568`, `#5591`,
  `peek_snapshot.py`, `session_maintenance_cli.py`); conservative sub-agent
  attribution and backward history paging (`#5279`, `acp_subagents.py`,
  `acp_client.py`, `routes/event_pages.py`, `db_events.py`); creator
  provenance (`#5361`, `caller_session.py`, session creation and successor
  propagation). Owning reality docs are agent-bridge's
  `docs/architecture.md` and `docs/delegation-contract.md`.
- Superset check: preserved all existing Features, Behaviors, and Non-Goals.
  Added positive observation capabilities and safety guarantees, not a new
  teardown order. Presence is transcript evidence, not a liveness oracle;
  consumption is reported coverage, not an authoritative bill; creator
  identity is informational, not reuse/cursor/control identity. Attribution
  remains optional and conservative; no complete attribution guarantee was
  inferred from a best-effort feed. No API names, thresholds, schemas, or
  protocol numbers were added to the vision.
- Direction 1: added `inherits-runtime-service-invariants` and a service-model
  See Also link, retaining the existing process-count and deployment promises.
  Direction 2 is **scoped to these observation additions**, not a fresh
  whole-daemon conformance audit:

  | Invariant | Status in this scope | Evidence / delta |
  |---|---|---|
  | `interoperate-across-version-skew` | Conforms for optional attribution and creator capture; full bridge contract remains partial | `SubagentAttribution.call_with_meta` retries only rejected optional metadata; absent feed preserves the ordinary stream. `gate_caller_session_id` omits unsupported capture with a warning. Backward paging is explicitly protocol-gated in architecture docs. Broader contract evolution stays tracked by `#1460`, not closed here. |
  | `degrade-gracefully` | Conforms for these observations | `_cmd_presence` exposes unknown with a reason; `_cmd_usage` preserves missing figures and per-metric coverage; attribution rejects mismatched/interleaved text rather than assigning it speculatively. These paths do not require a logger or dispatch service. |
  | `process-count-scales-with-services-not-sessions` | Conforms for these additions only | Attribution state belongs to the existing `AcpClient`; local transcript inspection is a bounded helper invocation, not a new resident daemon. This is not a census of the bridge's overall process topology. |
  | `immutable-versioned-runtime` / `zero-downtime-cutover` | Not re-audited | Existing Phase 3 findings and `#5472` remain the applicable tracked delta; this documentation slice changes no installer/runtime. |

- No new invariant blind spot identified at branch altitude and no new
  implementation gap carved from this bounded observation comparison. Other
  recent bridge commits and the remaining Phase 3 invariants are explicitly
  outside this slice. Existing source documentation already describes these
  additions; only the standing vision and this effort need updates.
- Validation: document-structure guard passes for both touched READMEs.
  The contained agent-bridge runner selected the existing peek/presence/usage,
  sub-agent attribution, creator-provenance, and backward-page contract tests:
  **119 passed, 2 skipped, 3299 deselected** on Windows. This includes real
  local transcript-helper execution and in-process HTTP/manager boundaries,
  not a new live agent run. Clean-room and external-venue tiers were not run:
  no provisioning, runtime, hook, or payload behavior changes in this slice.
  The Windows-inapplicable remote-shell scenario remains skipped.

### 2026-10-08 — Phase 2/3 agent-bridge generation-handoff slice
- Continued directly after the observation slice (`#5734`) at the operator's
  request. Read the archived `agent-bridge-unified-zdd-cutover` effort, current
  architecture, both installer update paths, CLI deploy/restart, passive
  startup gating, HostIndex custody, running-generation evidence, shared
  cutover locking, and stale-breadcrumb recovery.
- Fold-back: observable deployment freshness distinguishes installed files,
  selected runtime, serving generation, and attempt versus completed reconcile.
  Candidate readiness does not grant Session Host custody; durable metadata
  refreshes preserve ownership. Deployment and recovery share transition
  authority. Folded that last guarantee UP into `plugin-services`'s existing
  `zero-downtime-cutover` item, rather than making it bridge-only. This is pure
  intent; no APIs, marker formats, lock grammar, or platform commands were added.
- Superset check: retained every existing positive and Non-Goal in both visions.
  The existing single-deploy/no-outage intent is not weakened to bless a
  fallback. Explicit transition ownership extends the existing shared
  serialization purpose; it does not withdraw an intended recovery capability.
- Historical delivery is not full conformance: `service restart` really calls
  `_cmd_deploy`, and the archived effort records completed phased work and
  real live-turn/abrupt-termination drills. However, both installer failure
  branches still call drain/stop/start after a failed new-version cutover.
  Filed `#5750` after dedup, distinct from `#5472`'s same-version mutation.
  Left `#4477` open with a comment separating completed historical phases from
  residual canonical-deployment conformance, rather than closing it on a Done
  effort marker.
- Found a second concrete gap at the real CLI boundary: `_cmd_deploy` calls
  stale-cutover recovery and abandoned-passive reaping before
  `CutoverOrchestrator.run()` acquires its lock. `breadcrumb.is_stale()` checks
  nonterminal state, not abandoned ownership, so a concurrent caller can undo
  a live transition before waiting. Filed `#5754` after dedup; the existing
  lock and identity safeguards must remain. No observed host timeout is
  attributed to this race without a direct reproduction.
- Scoped service-invariant audit:

  | Invariant | Status | Evidence / delta |
  |---|---|---|
  | `zero-downtime-cutover` | Partial | Shared orchestrator health-gates and serializes its main sequence, and restart uses it; both installer failed-deploy branches still fall back to stop/start (`install.ps1:3098-3106`, `install.sh:2169-2181`, `#5750`). Recovery prelude is outside that authority (`venue_cli.py:265-267`, `#5754`). |
  | `register-once-cutover-on-update` | Windows conforms in inspected path; POSIX violates | `Ensure-ScheduledTask` leaves an existing task untouched. POSIX assigns `VENV_DIR=versions/$SRC_VERSION`, then emits that concrete slot into `ExecStart` and rewrites/reloads/enables the unit during every update (`install.sh:250,311,1054-1101,2161`). Added evidence to existing native-supervisor tracker `#4022`, not a duplicate issue. |
  | `cutover-coherent-service-tracking` | Partial, existing tracked gap | Detached successor versus tracked predecessor is already `#4022`/`#5225`; do not weaken the invariant by telling consumers to ignore an inactive native supervisor. No new live systemd census here. |
  | Generation-scoped Session Host custody | Conforms in inspected primitives; full provider recovery remains separate | `app.py` skips passive reattach; `HostIndex` locks/reloads, preserves ownership on metadata writes, and releases only its generation. Full provider reattach remains `#2041`; these narrow primitives do not close that issue. |
  | `immutable-versioned-runtime` | Prior violation remains | Same-version content-changed rebuild remains `#5472`; this slice adds no installer change. |

- The rest of Phase 3 remains open across the plugin set; this is not an
  all-services audit. The runtime-publication symptom initially filed as
  `#5749` was consolidated into earlier `#4547`: preserved the missing-new-slot
  entrypoint / automatic recovery-to-prior variant in a comment, closed only
  the duplicate tracker, and asserted no root-cause equivalence or repair.
- Validation: three touched READMEs pass the structure guard and the diff
  whitespace check. The contained Windows agent-bridge runner reports
  **54 passed, 1 skipped, 3436 deselected** for existing cutover, custody,
  running-generation, reconcile-outcome, and restart-delegation tests.
  These validate the inspected primitives, not either newly filed failing
  installer/CLI-prelude contract. The new issues name the corresponding
  implementation regression-test obligations. No fresh clean-room, live
  cutover, or POSIX native-supervisor run was performed: this is a vision and
  tracker reconciliation with no runtime, installer, or payload changes;
  archived live-drill evidence is historical evidence, not a new run.

### 2026-10-08 — Phase 2 agent-fabric layer/venue/memory seam slice
- Read the full branch vision, then used three bounded, read-only evidence
  tracks: ground authority; venue/connectivity; delegation/session memory.
  Each had disjoint owning source scope, source citations, an explicit stop,
  and no edit/publication authority. Coordinator retained integration,
  uncertainty judgment, issue dedup, and PR ownership.
- Fold-back: provider-owned lifecycle/reachability behind shared coordination
  semantics, and human-attended remote Copilot sessions as peers of headless
  workers. Evidence includes the providers' common CLI-mode reservation/
  registration paths (`agent-codespaces/copilot_venue.py`,
  `agent-containers/copilot_venue.py`, `agent-ssh/copilot_detach.py`).
  Restricted containers refuse unsupported attended reach rather than quietly
  inheriting trusted authority. The branch preserves headless reach and the
  existing trusted/restricted boundary; no unsupported mode is required.
- Reconciled a real parent/child terminology contradiction: the parent called
  agent-logger another fabric layer, while its current child explicitly calls
  it a consumer of delegation. Preserved all session collection, compilation,
  segmentation, and survivable-memory intent; clarified the ownership seam
  rather than removing the capability. Verified the child scope and
  `chronicle/orchestrator.py`'s use of existing scheduling/lease seams directly
  because the delegate's claim required that cross-check.
- Did not accept unsupported delegate gap claims as new issues. A deferred
  `ManifestWriter` result represents a written manifest, keeps segments
  reserved, and does not report landed output; that is not proof of a false
  end-to-end completion verdict. Broad portable catalog/accounting/fleet
  guarantees remain the existing `#5665` campaign, not newly asserted as
  implemented or redundantly filed. Linked the parent-memory reconciliation
  to that campaign.
- Ground authority already has the relevant branch-level positives. Retained
  head/succession, asserted disposition versus derived pulse, directional
  claims, and cross-layer handoff ownership unchanged. Open owning efforts
  `worktree-head-succession-hardening` and
  `agent-worktrees-authoritative-daemon`, plus existing `#3584`/`#3761`,
  retain that work. `#3761` explicitly scopes the design, not all migration
  implementation; it is not a claim that every remaining runtime obligation
  closes with that issue. No status/gap wording was copied into the vision
  from the delegate's suggested prose.
- Folded up observation-versus-authority from the directly reconciled bridge
  evidence (`#5734`): fidelity, missing meters, creator provenance, and
  transcript activity do not silently become liveness, control, or settlement.
  The service-model/host-provider boundaries remain inherited, not restated
  as endpoint or runtime specifications.
- Superset check: preserved every original Feature, Behavior, and Non-Goal.
  The memory heading clarification preserves the whole capability, and venue
  peerage augments rather than replaces headless reach. No schema, API, command
  grammar, credential-bootstrap wiring, or conformance table enters the vision.
  This is a scoped branch comparison, not a full audit of every machine,
  trust, telemetry, or resource-accountability implementation.
- Validation: all three touched READMEs pass the structure guard and the diff
  whitespace check. No new runtime tests, clean-room, or live-venue probes
  were run: the diff changes standing parent intent and journal attribution,
  not execution or installation. Prior provider/bridge implementation
  citations remain evidence, not a fresh full-venue validation claim.
- PR `#5759` review found the same terminology contradiction still present in
  the CodeSpace leaf. Extended the correction to all seven vision-tree
  references there, preserving rescue-before-teardown, comprehensive capture,
  optional-peer degradation, and logger-owned analysis. Rechecked the full
  terminology family rather than only the flagged parent heading; the final
  diff therefore touches three READMEs, not two.

### 2026-10-08 — Phase 2 agent-bridge delivery and cooperative-stop slice
- Continued after merged `#5759`. Reconciled `#5645` against
  `docs/delegation-contract.md`, `send_outcome.py`, `session_stop.py`,
  `client_session_stop.py`, CLI integration, and the prompt/handoff guards.
  Folded semantic delivery decisions, bounded cooperative wind-down,
  independent acknowledgement/final-state confirmation, idempotent stop,
  and protection against notice-triggered resurrection into the leaf.
- Superset check: all earlier positives and boundaries remain. No change to
  ordinary admission, queue, force, or stop policy is implied. A visible
  semantic refusal is not a new permission to weaken delivery guarantees.
  Admission is not task completion; notice acknowledgement is not proof all
  agent-owned work was quiesced; session STOPPED is not proof every retained
  Session Host was physically reaped. Exact outcome names, exit codes, grace
  thresholds, protocol numbers, and queue API grammar stay in reality docs.
- Evidence: `run_send` preserves semantic outcomes and distinct refusal
  handling without swallowing unclassified errors; older duplicate/successor
  evidence is not invented. `run_stop` distinguishes notice, acknowledgement,
  provider action, and observed stopped/gone state. Its notice is queued with
  no-resume intent, guards auto-handoff through prompt/stop state, and is
  withdrawn on expiry; capability gating refuses unsupported cooperation
  before sending. Plain stop and explicit force retain their existing paths.
- Scoped service-invariant comparison: optional cooperation degrades with an
  explicit unsupported result, while ordinary control remains available;
  unknown compatibility does not become silent success. This conforms in the
  inspected paths to `interoperate-across-version-skew` and failure honesty.
  No resident process, installer, endpoint, or ownership tier is added by this
  documentation slice. Full bridge contract evolution remains `#1460`, not
  closed by this smaller current-generation gate.
- No new implementation issue identified in this bounded control comparison;
  existing `#5645` source/tests already exercise acknowledgement races,
  expired notices, concurrent stop, and older-daemon refusals. This does not
  close the remaining bridge hosting/routing/protocol sweep or Phase 3.
- Validation: both touched READMEs pass the structure guard and the whitespace
  check. Existing contained Windows send/stop/auto-handoff contracts report
  **80 passed, 1 skipped, 3414 deselected**. These cover cooperative-stop
  races and current compatibility gates, not every venue or negotiated
  generation. No new clean-room or live-venue run: no runtime, hook, installer,
  or payload behavior changes in this slice. No fresh process-reclamation
  guarantee is inferred from session-state confirmation tests.
- PR `#5770` review corrected "forced retirement" to "forced stopping": even
  forced stop preserves resumable state, whereas end/retirement is a separate
  state-removal operation. Kept that lifecycle distinction explicit rather
  than broadening the stop contract accidentally.

### 2026-10-08 — Phase 2 native-convergence standards/proof boundary slice
- Read the full branch vision and used three bounded, read-only evidence
  tracks for creation/roots, native identity/boundary, and host steering.
  Current CLI help confirms worktree, session, directory-boundary, and remote
  commands, but its README was unavailable. Help presence does not establish
  a headless creation API, stable root/catalog schema, or native AHP endpoint.
- Fold-back: the concrete Worktree Manager AHP provider participates in
  standards-based hosting without proving that a particular native Copilot
  product exposes that protocol. Preserved plural hosting, feature detection,
  reversible fallback, and the native-owner/durable-agency split. Added no
  endpoints, URI schemes, field names, native flags, or root paths to the vision.
- Rejected an over-generous delegate gap claim: a harness-authored session
  relation sidecar is not itself a second session-identity owner.
  `session_metadata_cli.py:278,303` uses the native
  `COPILOT_AGENT_SESSION_ID` by default, while `session_projection.py` is
  explicitly reciprocal, rebuildable worktree metadata. This proves native
  ID use in those entry points, not complete workspace/catalog/boundary mapping.
  Absence in the inspected sidecar path does not prove native boundary
  enforcement is absent at the host edge. The attempted same-task delegate
  follow-up was unsupported for a synchronous agent, so the coordinator
  verified only that concrete contradiction rather than rereading the scope.
- Verified tracker states instead of trusting the report: `#985` and layout
  `#986` remain open; deferred creation `#988` remains open. Phase B `#987` is
  CLOSED, but its closing event names `c994ce9be777408525aac575c3b60c8b05c9e2fe`,
  a bridge/SSH process-isolation change with no native mapping implementation
  in its diff. Commented on `#987` and open `#985`; did not reopen the issue or
  assert complete non-realization from that mismatch alone. Native creation
  still uses the harness path in the inspected source; root/catalog/native
  working-boundary convergence remains unproven, not a new duplicate tracker.
- Existing bridge native-host and negotiated-contract work remains under
  `#1266`/`#1460`; neither ordinary `/remote` help nor a standards-facing
  provider is counted as a fresh native-host round-trip test. No new runtime
  gap is carved solely from missing public schema evidence.
- Superset check: retained all original constructs, positive features, and
  negative boundaries. Requiring proof before adoption strengthens the
  existing stable-surface guard without removing a fallback or a supported
  standards-based host. This is a scoped convergence audit, not completion
  of every native root/catalog/working-boundary implementation.
- Validation: both touched READMEs pass the structure guard and whitespace
  check. Existing contained Windows session-metadata/projection contracts
  report **100 passed, 1 skipped, 7448 deselected**. These validate the
  inspected native-ID-linked agency projection and compatibility handling,
  not native layout/catalog convergence or a Copilot AHP round trip. No
  clean-room or native-host launch was run: the diff changes intent and audit
  evidence, not runtime adoption or installation.
- PR `#5783` review clarified Plan accounting: marked the completed
  proof-boundary slice done and split remaining root/catalog/working-boundary
  source coverage into its own open item. The completed slice does not
  silently complete the remaining native audit or its implementation phases.

### 2026-10-08 — Phase 2 agent-worktrees account-boundary slice
- Read the full worktree-parent vision, then scoped source comparison to
  repository-account resolution and operation credentials, not a full
  head/claims/daemon rewrite. Reality evidence: `accounts.py`'s descriptive
  catalog, `repos.resolve_account`'s routing precedence, token lookup in
  `git_ops.py`, `_gh_env_for_repo`, the `repos gh` execution branch, and
  repository-local Git credential pinning.
- Fold-back: the account catalog describes identity/authentication expectations
  separately from which repositories choose those identities; the credential
  store remains owned by the authentication provider. Scoped invocation and
  local pinning avoid a machine-global account switch. The code's Git pinning
  is a selective, best-effort capability with provider/scope limits; no
  universal bare-Git pin was claimed from that implementation.
- Carved `#5791` after dedup: for an explicitly intended account whose token
  minting fails, `_gh_env_for_repo` returns an unchanged ambient environment;
  `repos_cli.py:666-669` warns and still launches arbitrary `gh` arguments.
  The warning is real, so this is not described as a silent failure. The gap
  is execution under replacement identity after explicit intent resolved.
  The vision requirement and issue's fix direction distinguish an explicit
  override/map from the derived-owner heuristic so an unconfigured organization
  is not accidentally treated as a mandatory authenticated user. No runtime
  guard was implemented here and no live wrong-account mutation was attempted.
- Existing `#2755` concerns short-name resolution and `#330` stale minted
  tokens; neither is this subsequent missing-token execution fallback.
  The issue names no-subprocess regression obligations for failed explicit
  choices and preserves ordinary unconfigured ambient behavior.
- Superset check: retained all existing positives and negatives, especially
  independent Copilot inference identity and its shared-machine idle/explicit
  correction boundary. No OAuth scopes, helper grammar, environment variables,
  file layouts, or credential data enter the vision. Refusing an unavailable
  explicit account implements the already-stated intended-identity contract;
  it does not remove unconfigured ambient operation.
- Scoped conformance: successful token injection and target account mapping
  embody per-operation identity isolation; token-unavailable explicit choices
  remain partial (`#5791`). This is not a claim that all Git helpers, SSH
  identities, Copilot identity correction, or role-derived contribution
  operations were freshly audited. The remaining parent vision stays open.
- Validation: both touched READMEs pass the structure guard and whitespace
  check. Existing contained Windows account-catalog/routing/token-environment
  contracts report **25 passed, 7524 deselected**. They preserve current
  behavior, including the token-unavailable environment result; they do not
  implement or prove the new no-ambient-execution requirement. Selective Git
  pinning is source-inspection evidence only in this slice. The new issue owns
  the missing no-subprocess regression. No live auth switch, public mutation
  probe, clean-room install, or provider credential experiment was performed.
- PR `#5793` review exposed an existing-contract ambiguity: the old universal
  no-global-account wording conflicted with the preserved unconfigured ambient
  default. Asked the operator before qualifying it. The explicit decision
  above scopes the strict guarantee to declared repository/account-map choices
  while retaining ordinary unconfigured ambient behavior. Qualified both
  affected paragraphs; independent Copilot inference identity is unchanged.

### 2026-10-08 — Phase 4 material-refresh relationship decided
- Operator selected **"Separate sibling effort"** and confirmed
  **"adopter-material-refresh"**. Created independent umbrella `#5799` after
  tracker/active-effort dedup and verified target effort adoption with the
  exact read-only probe. The new plan clears its own repository review gate
  before execution; no README rewrite or preview capture occurred here.
- Decision capture was checked against the literal selections before updating
  Request, Guiding Intent, Proposal, and Plan. No firm choice remains phrased
  as an open question. Material execution is not silently added to the vision
  sweep's remaining Phase 2/3 or Validation Plan.
