# Native-Construct Convergence — Vision

- **Subject:** How the harness converges onto Copilot CLI's **own native
  constructs** for agent working-context — worktrees, workspaces, session
  boundaries, catalogued working locations (projects), source/worktree roots,
  and cloud session steering over the agent-host protocol — treating each native
  construct as the substrate and reserving the harness for the durable value the
  CLI does not itself provide.
- **Scope:** branch (a cross-cutting capability that spans the fabric, the
  installer/control-plane, and the picker)
- **Status:** Active
- **Last revised:** 2026-09-04
- **Reality docs:** [`docs/architecture.md`](../../docs/architecture.md) ·
  [`plugins/agent-worktrees/docs/architecture.md`](../../plugins/agent-worktrees/docs/architecture.md)

## Purpose & Intent

The harness pioneered a **spatial model** for many agents: per-worktree
isolation, session identity, source and worktree roots, adopted projects, and a
front door that presents them all. Copilot CLI is now growing native constructs
that cover the same ground — it can create and switch worktrees, records
per-session workspace metadata, carries an explicit session/working boundary,
catalogues working locations, understands where clones and worktrees live, and
exposes cloud steering of a task→session→environment over its agent-host
protocol.

When the platform beneath a tool absorbs one of the tool's primitives, the tool
should **converge onto the platform's construct**, not maintain a parallel one.
The north star: as the CLI makes a construct native, the harness treats that
construct as the **substrate** — it **delegates the primitive** to the CLI,
**aligns its own vocabulary and on-disk layout** to the CLI's shape so the two
are mutually discoverable, **rides** the CLI's identity and steering surfaces
instead of shadowing them, and keeps its own effort for the **durable value the
CLI does not provide**: the finalize/cleanup lifecycle and worktree disposition,
cross-machine reach, the resource-claim ledger, the picker/front-door, review
(PR) gating, clone/source policy, and cross-agent coordination.

Success looks like: there is no second, divergent implementation of a primitive
the CLI owns; a user's worktrees, workspaces, and projects are the **same
objects** whether the CLI or the harness created them; the harness reads one
canonical session identity rather than a shadow copy; and every convergence step
is **staged and gated** so it never removes a capability and never binds the
harness to a construct that is not yet released and stable. Convergence is a
subtraction of *duplication*, never a subtraction of *capability*.

## Concepts & Components

The native constructs the harness converges onto, and its intended role toward
each. Each is a component of this cross-cutting vision; the layering that hosts
them is the [agent-fabric](../agent-fabric/README.md), and the front door that
presents them is the [picker](../picker/README.md).

### Native worktree lifecycle
The CLI natively **creates and switches** worktrees (auto-named, base-ref
selectable, folder-trust handled). The harness aligns its worktree **layout** to
the CLI's so worktrees made by either are mutually discoverable, delegates
**creation/switch** to the CLI where the native primitive suffices, and retains
the parts the CLI has no notion of — **finalize/cleanup**, prune-safety, and
asserted **disposition**.

### Native session/workspace identity
The CLI records **per-session workspace metadata** (working directory, git root,
repository, branch, name). The harness treats that record as the **canonical
identity** for a session and aligns its own session identity to it, so state is
**derived from one owner** rather than kept as a parallel copy.

### Native working boundary
The CLI carries an explicit **session/working boundary** (the allowed working
location(s) for a session). The harness treats the native boundary as
authoritative rather than inventing a separate boundary of its own.

### Native project / catalogued working location
The CLI has a first-class notion of a **catalogued, adopted working location**.
The harness maps its **project/adoption** concept onto it, so "which project"
resolves to the native construct instead of a harness-only registry.

### Native source & worktree roots
The CLI understands where **clones** and **worktrees** are rooted. The harness
maps its **source root** and **worktree root** onto the native roots and, once
those surfaces are released and stable, **delegates** root policy to them —
keeping only the clone/source *policy* (what to clone, where, under what
identity) that the CLI does not decide.

### Native cloud steering over the agent-host protocol
The CLI exposes **cloud steering** of a live agent — a task→session→environment
that can be shared and steered remotely over its **agent-host protocol**. The
harness adopts that surface as one session-host provider rather than making it
the only execution model. Live-session coordination and handoff use the common
hosting boundary, contributing cross-machine, durable-agency, and claim
semantics the native surface does not carry while remaining compatible with
CLI/mux, SDK, App, ACP, and third-party hosts.

## Features

### native-construct-as-substrate
When the CLI provides a construct natively, the harness **builds on that
construct** as its substrate rather than standing up a competing implementation
of the same primitive.

### vocabulary-and-layout-alignment
The harness's names and on-disk layout for a converged construct **match the
CLI's shape**, so the two systems' objects are mutually discoverable and a user
never has to reconcile two spatial models.

### delegate-the-primitive-keep-the-value
For each construct, the primitive itself is **delegated** to the CLI while the
harness keeps the **differentiated value** the CLI does not provide
(finalize/cleanup, cross-machine reach, claims, picker, PR-gating, clone policy,
coordination).

### mutual-discoverability
Worktrees, workspaces, and projects created by **either** the CLI or the harness
are the **same objects**, visible to and usable by both.

### ride-native-identity-and-steering
The harness reads the CLI's **canonical session identity** and rides the CLI's
**cloud steering / agent-host** surfaces for live-session presentation and
handoff when that provider is selected, instead of shadowing them with a second
identity or channel. Other execution providers participate through the same
host-neutral agency model.

## Behaviors

### no-capability-regression
No convergence step may remove or weaken a capability the harness provides
today. If delegating a primitive to the CLI would drop a behavior (e.g.
finalize, disposition, cross-machine), that behavior is **retained on top of**
the native construct, not lost.

### feature-detected-convergence
The harness **never hard-depends on an unreleased or unstable** native
construct. Reliance on a native construct is **feature-detected** and staged
behind the **released** surface; when a native construct is absent or older, the
harness falls back to its own implementation with no loss of function.

### staged-behind-released-surfaces
Convergence advances **construct by construct**, each stage adopted only once
the corresponding native surface is released and stable, so the harness's
correctness never rides on a moving or private target.

### one-owner-per-primitive
Once a primitive is converged, the **CLI owns** it and the harness **derives**
from it — the harness does not keep a second authoritative copy of a converged
construct's state.

### reversible-and-gated-adoption
Each convergence step is **reversible**: because reliance is feature-detected and
the harness's own implementation is retained as the fallback, a regression or a
change in the native surface can be backed out without stranding users.

## Non-Goals / Boundaries

- **Not a re-implementation of what the CLI now provides.** Where the CLI owns a
  primitive natively, the harness does not maintain a competing one.
- **Not a dependency on unreleased or private CLI internals.** Convergence binds
  only to **released, stable** native surfaces; unreleased constructs are tracked
  but not depended on.
- **Not a subtraction of harness value.** The finalize/cleanup lifecycle,
  cross-machine reach, resource claims, the picker, PR-gating, clone/source
  policy, and coordination are **kept** — layered on top of the native
  constructs, never dropped in the name of alignment.
- **Not a specification.** This vision fixes *which native constructs the
  harness converges onto and the guarantees of that convergence*, not the
  concrete flags, paths, schemas, or command grammar. The concrete worktree
  layout, flag wiring, and identity mapping belong to the effort that realizes
  this and to the reality docs.

## See Also

- Parent vision: [visions index](../README.md)
- Related visions:
  [agent-fabric](../agent-fabric/README.md) — the layered fabric whose
  isolation/coordination/venue layers host the constructs converged here;
  [plugins/agent-worktrees](../plugins/agent-worktrees/README.md) — the ground
  layer whose worktree + session state converges onto the native worktree and
  workspace constructs;
  [session-hosting](../session-hosting/README.md) — the provider boundary that
  treats native host steering as one execution option rather than a universal
  process model;
  [installer](../installer/README.md) and [picker](../picker/README.md) — the
  out-of-plugin control plane and front door that present the converged
  constructs.
- Reality docs: [`docs/architecture.md`](../../docs/architecture.md) · each
  plugin's `docs/`.

## Provenance

- **2026-08-23** — Initial authoring. Intent mined from the observation that
  Copilot CLI is natively absorbing the spatial model the harness pioneered
  (native worktree create/switch, per-session workspace metadata, an explicit
  working boundary, catalogued working locations, source/worktree roots, and
  cloud session steering over the agent-host protocol). Generalized the
  operator's direction — "align with Copilot's own native constructs" — into a
  standing cross-cutting mission with two firm guardrails expressed as Behaviors:
  **no capability regression** and **no hard dependency on an unreleased or
  unstable native construct** (convergence staged behind released,
  feature-detected surfaces).
- **2026-09-04** — Clarified that native cloud steering converges as one
  session-host provider inside a plural execution ecosystem. Native constructs
  remain preferred substrates when selected and stable, without collapsing
  worktree-lifetime agency identity onto one Copilot product or host.
