# Clean-room validation — Vision

- **Subject:** clean-room validation of the copilot-extensions plugins and the harnesses assembled from them
- **Scope:** leaf (concrete cross-cutting capability)
- **Status:** Active
- **Last revised:** 2026-08-13
- **Reality docs:** [`tools/clean-room/README.md`](../../tools/clean-room/README.md) (operator guide), [`tools/clean-room/ARCHITECTURE.md`](../../tools/clean-room/ARCHITECTURE.md) (rig architecture)

## Purpose & Intent

Every claim about how a plugin **installs, bootstraps, provisions, and behaves**
should be a **hard PASS/FAIL line**, produced on a disposable **fresh machine**,
not a belief. The clean room is that fresh machine: a throwaway box that
reproduces what a naive operator experiences standing up a brand-new harness —
so "I *think* it does X" and mixed field reports become reproducible verdicts.

The north star is a **living validation set** that a new plugin, or a new harness
assembled from plugins, is measured against — and that **grows with the suite**:
each contribution that touches install/bootstrap/provisioning/behavior extends or
re-runs the set, so the guarantees ratchet forward instead of eroding. The
ultimate acceptance is **turn-key assembly**: a fresh harness, bound to an
*empty* knowledge repo, carries out a real end-to-end task with **zero manual
setup** — proving the whole assembled chain, not just its parts.

## Concepts & Components

- **The clean room (fresh machine).** A disposable, isolated box with a stock
  login-shell PATH and none of the operator's runtime state — the substrate under
  which everything else is measured. It reuses the operator's *login* (auth is not
  what is under test) but nothing else. It comes in **fidelity variants** from
  "stock dev toolchain present" down to "the harshest fresh internal box" that
  forces the harness to provision its own toolchain, so provisioning jams surface
  instead of hiding.
- **Two validation tiers** (*how* we test). **Tier P — programmatic**:
  deterministic, agent-free, CI-able assertions on the plugins' real CLI
  surfaces. **Tier E — eval**: an agent driven through a scenario and judged,
  covering what no deterministic check can — under a mandatory **literal-mode**
  discipline so a capable model reports the first gap instead of heroically
  self-healing and masking it.
- **Subject families** (*what* is under test). The **copilot-extensions suite**
  itself; **downstream plugins layered on bare Copilot + the suite**; and a
  **fully-assembled harness**. Each asks its own question — self-provisioned and
  working? fails fast and guides when unconfigured? resolves assembly-requiring
  requests?
- **Scenarios.** Self-describing, name-free units of validation (composition ×
  base-plugin axes: solo → reasonable combinations → full assembly, each with and
  without the worktree base). The **rig is generic and public**; scenarios that
  name specific repos live with the **harness that consumes them**.
- **The diagnostic layer.** A failure is never just "red": it is a **classified
  jam** (a category + evidence + an unjam hint), so a failure is *legible* and,
  where a fix is deterministic and safe, *repairable* — turning the clean room
  into a repair-discovery engine whose confirmed fixes flow back to the owning
  plugin.
- **The judge.** Tier-E outcomes are scored by a dedicated, personality-neutral
  **evaluator** that renders PASS/FAIL + evidence under literal-mode rules
  (credit only the literal task; never credit improvised self-repair).

## Features

### fresh-machine fidelity
The clean room reproduces a genuinely fresh box — no inherited runtime, binstubs,
marketplace, or feed governance — with selectable fidelity from "stock toolchain
present" to "Copilot + git only." The harsher the box, the more provisioning
assumptions it falsifies. Feed governance (the corp-box **asymmetry** where policy
configures one package manager's internal feed but not another) is reproducible as
an opt-in fixture, never baked in.

### two-tier coverage
Every subject is coverable **programmatically** (deterministic, CI-able, no model
in the loop) where its surface allows, and by **agent eval** where only a driven
agent can answer the question — with the programmatic tier as the cheap gate the
eval tier sits behind.

### the solo self-sufficiency contract
Every extension, installed **alone**, must (a) **self-provision** its runtime on
first session and (b) **affirmatively confirm readiness and guide the next step** —
fail-closed, so the *absence* of an explicit "ready" is read as "not set up," never
as "fine." A solo-installed plugin must never present as a silent dead end. This is
the cross-cutting property the clean room exists to enforce, per plugin.

### the scenario matrix
Validation is a matrix, not a single flow: **solo** plugins → **reasonable
combinations** → the **full assembled harness**, each runnable **with and without
the worktree base** (does an enhanced feature degrade safely to ambient behavior
when its base is absent?). The set is meant to **cover the suite** and grow with it.

### classified diagnostics & unjam
Failures are emitted as **classified jams** with evidence and an unjam hint; safe,
deterministic unjams may be applied and the stage re-run idempotently. The taxonomy
makes "why did it fail" answerable at a glance and feeds fixes back to owners.

### validation as a contribution norm
The set is a **standing asset the suite is measured against**: contributions that
affect install/bootstrap/provisioning/behavior **run or extend** the relevant
scenarios when practical, so coverage ratchets forward and regressions are caught
on a fresh box rather than in the field.

### turn-key assembly acceptance
The program's ultimate gate: a fresh assembled harness, bound to an **empty**
knowledge repo, completes a real end-to-end task with **zero manual setup** —
exercising provisioning, binding-when-empty, cross-repo delegation, venue auth, and
landing, as one whole.

## Behaviors

### falsifying, not self-healing
A validation run **surfaces the first real gap** rather than papering over it. Tier
E runs under literal mode (the driven agent does exactly the named step and stops
to report the first obstacle verbatim); the judge credits only the literal outcome.
A run that "passed" only because a capable agent improvised around a broken setup is
a **false pass** and is treated as a defect of the scenario.

### fresh every time, host-safe
A run starts from a genuinely clean state and never mutates the host: everything
under test lives in the disposable box, and run artifacts are written **outside any
repo tree** (a machine-local results dir), never into an anchor checkout.

### legible verdicts
Every run yields a structured verdict — per-stage PASS/FAIL, an environment
snapshot, and classified jams with evidence — reproducible and reviewable, so a
result is actionable (which link broke, and the hint to unjam it) rather than a bare
red/green.

### the rig stays generic; scenarios carry the specifics
The runner and shared library are **name-free** of any operator's repos and evolve
independently; anything that names a specific repo or internal venue lives in a
self-contained scenario owned by the consuming harness, mountable verbatim.

### auth is borrowed, not tested
The clean room reuses the operator's existing login by design and does **not**
validate auth itself; a missing credential surfaces as a classified auth jam, not as
the thing under test.

## Non-Goals / Boundaries

- **Not an auth test.** It reuses your login; validating the auth systems
  themselves is out of scope.
- **Not a unit-test replacement.** It validates the *install → bootstrap →
  provision → behave* experience on a fresh box, not a plugin's internal logic
  (that is each plugin's own suite).
- **Not a home for operator-private scenarios.** The public rig stays name-free;
  repo-naming scenarios live with the consuming (downstream) harness.
- **Not a mandatory blocking gate (today).** Running/extending the set is a
  contribution **norm**; wiring the programmatic tier into enforced scheduled CI
  is a realization step, not part of the standing intent.
- **Does not own the fixes.** Confirmed gaps and unjams flow back to the owning
  plugin/effort; the clean room discovers and proves, it does not house the repairs.

## See Also

- Parent vision: none
- Child visions: none (leaf)
- Reality docs: [`tools/clean-room/README.md`](../../tools/clean-room/README.md),
  [`tools/clean-room/ARCHITECTURE.md`](../../tools/clean-room/ARCHITECTURE.md)
- Related visions: [`installer`](../installer/README.md) (the out-of-plugin
  turn-key installer the clean room acceptance-tests),
  [`plugin-services`](../plugin-services/README.md) (the self-provisioning service
  model the solo self-sufficiency contract enforces)
- Guidance: the **`validating-in-clean-room`** skill (run / evaluate / author more)
  and the **`clean-room-judge`** sub-agent, both in the `copilot-extensions-harness`
  plugin.
