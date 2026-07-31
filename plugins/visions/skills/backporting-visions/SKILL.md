---
name: backporting-visions
description: >
  Derive a north-star vision from a subject that already EXISTS: reverse-engineer
  the intent its current reality embodies and write it as a deliberate SUPERSET of
  that reality, so every vision-vs-reality delta is additive (build-out) rather
  than subtractive (scale-back). Also audits the repo's design/service invariants
  -- do they guide the new (leaf) vision, and does the subject conform (each
  nonconformance becomes an additive build-out delta). Use to seed a vision for a
  built-but-un-envisioned component, repair a vision that lags what was built, or
  onboard a plugin/service into the visions system. Composes the envisioning skill
  and planning-efforts; it does not replace them.
  Trigger phrases include:
  - 'backport a vision'
  - 'backport visions from reality'
  - 'reverse-engineer a vision'
  - 'derive a vision from what exists'
  - 'make the vision a superset of reality'
  - 'the vision lags reality'
  - 'audit design/service invariants'
  - 'do the plugins conform to the invariants'
---

# Backporting Visions

A **backport** authors (or repairs) a vision for a subject that **already
exists** — a plugin, service, or tool with real docs and code but no north star,
or one whose vision has fallen behind what was built. It runs the vision engine
*in reverse*: instead of stating intent first and letting reality chase it, you
read reality, **reverse-engineer the intent it embodies**, and write that as a
vision — then *extend* it to lead reality again.

This skill governs one specialization on top of the canonical
[`envisioning`](../envisioning/SKILL.md) flow: the **superset discipline** and the
**design/service-invariant audit**. Everything generic — the schema, the detail
ceiling, deriving the delta, the generativity check, the positive/negative-intent
asymmetry — lives in `envisioning` and its
[`references/visions.md`](../envisioning/references/visions.md). Read the repo's
`visions/README.md` addendum first (organization + linkage), exactly as
`envisioning` requires.

## The governing property: the vision is a SUPERSET of reality

The point of a backport is a specific, load-bearing shape:

> The derived vision **covers everything the subject really is** (its embodied,
> intended capabilities) **and states the north-star intent beyond it** — so the
> vision→reality delta is **entirely additive**. Driving the delta to closure
> **builds the subject out**; it never scales it back.

This follows directly from the positive/negative-intent asymmetry (`envisioning`
§ *Adding, changing, and removing*):

- A **subtractive** (removal) delta exists **only** where the vision **states a
  negative** — a Non-Goal or an explicit "no X" — that reality **violates**.
- **Merely not mentioning** a real capability is **not** a removal order; absence
  is latitude, so an omitted capability is never torn out.

Therefore a vision is a superset **by construction** when you (a) **fold back
every genuine embodied intent** as a positive Feature/Behavior, and (b) **state a
negative ONLY when you deliberately want that thing removed**. A backport that
accidentally omits a real, intended capability merely *stops requiring* it (safe,
but verify it was intentional); a backport that carelessly writes a Non-Goal the
subject already violates has smuggled in a **scale-back** — the one outcome this
flow exists to prevent.

## The backport procedure

1. **Gather reality at the intent altitude.** Read the subject's reality docs
   (`docs/`, the plugin README, per-skill `SKILL.md`, config schema) and skim the
   code for behavior the docs miss. You are mining *what it is meant to do and
   guarantee*, not cataloguing lines.

2. **Reverse-engineer embodied intent → sort into three bins.** For each real
   capability/guarantee, decide which bin it lands in (same bins as the
   generativity check, read in reverse):
   - **Embodied intent → FOLD BACK.** A real capability that reflects a
     *deliberate* purpose, shape, guarantee, or user promise. Write it as a
     positive Feature/Behavior at the **detail ceiling** (the shape/guarantee/
     promise, never the wiring). *This is the bulk of a backport.*
   - **North-star-ahead → ADD.** Intent the subject does **not** yet realize but
     should. State it as pure should-be — this is the healthy vision→reality
     delta that will feed build-out efforts.
   - **Spec-level detail → OMIT.** Ports, schemas, exact APIs, file layouts, model
     names, command grammar. These belong to reality docs or a `specifications`
     layer, never the vision (`envisioning` § *Vision vs. specification*).
   - **Incidental cruft → OMIT (do not negate).** Legacy or accidental behavior
     you don't want to bless. **Leave it unmentioned** — absence is latitude and
     will not force its removal. Write a Non-Goal **only** if you *intend* an
     effort to tear it out (a deliberate subtractive choice, made with eyes open).

3. **Reconcile against the design/service invariants.** Run the **invariant
   audit** (next section) *before* finalizing — the repo's cross-cutting invariant
   vision is a design contract the leaf must inherit, and the subject's
   conformance gaps become additive deltas the vision should already imply.

4. **Draft the vision** by filling the schema (`envisioning` § *Create a
   vision*), placed per the addendum's organization. A backport is usually a
   **leaf** under an existing branch — link parent↔child, no orphans.

5. **Run the superset check (the guard).** Before committing, explicitly diff the
   draft against reality in **both** directions:
   - **Negatives vs. reality.** Every Non-Goal / "no X" / "must not" in the draft:
     does reality violate it? If yes, that is a **deliberate subtractive delta** —
     confirm the operator wants that teardown; otherwise it is an *accidental
     scale-back* — delete or restate it. **No unintended negatives survive.**
   - **Reality vs. positives.** Every real, intended capability: is it folded back
     (or intentionally left as latitude)? A silently dropped capability is a
     *stop-requiring* — legal, but verify it was intended, not an oversight.

6. **Validate generativity** (optional but recommended) via the isolated
   derive-and-judge check in `envisioning` § *Validate a vision*. A backport
   should score high on the fold-back bin by construction; a low score points at
   embodied intent you missed.

7. **Carve the additive deltas → issues/effort.** The north-star-ahead items and
   the invariant nonconformances are the **build-out backlog**. Dedup against
   existing trackers first (`envisioning` § *Extend before you regenerate*), then
   file issues that **cite the vision item**, and group them into an effort via
   [`planning-efforts`](../../../efforts/skills/planning-efforts/SKILL.md). The vision
   itself is never edited to record this cycle.

## The design/service-invariant audit

Most repos carry a **cross-cutting invariant vision** — a branch vision stating
the design/service contracts every component must honor (e.g. this repo's
[`plugin-services`](../../../../visions/plugin-services/README.md): à-la-carte
independence, discoverable/collision-free endpoints, minimal network exposure,
install/adopt boundary, immutable-versioned runtime, version-skew tolerance,
zero-downtime cutover…). A backport reconciles the leaf against it in **two
directions** — the operator's ask that the invariants "guide the vision
appropriately, and that existing plugins conform to them":

### Direction 1 — do the invariants GUIDE the (leaf) vision?

The invariant vision is the **design contract** the leaf inherits. As you draft:

- **Inherit, don't restate.** Where an invariant applies to the subject, the leaf
  either **cites** it (See Also → the invariant vision item) or **restates it in
  the subject's own terms** as a Behavior, so the leaf is a superset that *carries*
  the invariant. Don't silently drop an applicable invariant — an unstated
  invariant can't guide a build-out effort.
- **Fold a blind spot UP, not down.** If the subject embodies a design/service
  guarantee the **invariant vision itself fails to state** (a genuine blind spot
  at the invariant altitude), fold it **up into the invariant (branch) vision** as
  pure should-be — not just into the leaf. Invariants are where cross-cutting
  intent belongs; that is how the audit *improves the guidance* for every sibling.

### Direction 2 — does the subject CONFORM to the invariants?

For each invariant that applies, check the subject's reality and record status.
Produce a compact **conformance table** — invariant × status (conforms / partial /
violates / n-a) × evidence (a reality pointer) × the delta:

- A **violation or partial** is an **additive** delta: a build-out effort that
  brings the subject *into* conformance, filed as an issue citing the invariant
  item. It is **never** a reason to weaken the invariant or the leaf to match a
  lagging reality — that would invert the whole point (make the north star chase
  the code).
- Because the leaf vision **states the invariant** (Direction 1), the
  nonconformance is *already* a vision→reality delta the moment you write the
  vision — the audit just makes it explicit and files it.

Keep the audit output **out of the vision files**: visions and the invariant
vision stay **pure should-be** and never carry a conformance/gap list. The audit
is a review artifact + issues/effort, exactly like any other derived delta.

## Anti-patterns

- ❌ **Writing a Non-Goal reality already violates, unintentionally.** The signature
  scale-back bug. Every negative in a backport is a deliberate teardown order —
  run the superset check.
- ❌ **Blank-rewrite / regenerate-from-scratch.** A backport describes and extends
  the *real thing*; it never justifies tearing it down to rebuild (`envisioning`
  § *Extend before you regenerate* — the stability bias).
- ❌ **Weakening an invariant (or the leaf) to match a nonconforming plugin.** A
  conformance gap is a build-out delta, not a vision defect. Only a change in
  *intent* edits an invariant.
- ❌ **Inflating the vision with spec-level detail** dredged up from the code
  (ports, schemas, APIs) — keep the detail ceiling.
- ❌ **Recording conformance status inside the vision.** Visions carry no gap
  lists; the audit table lives in the review artifact / tracker.
- ❌ **Filing redundant issues/efforts.** Dedup against existing trackers before
  carving (two levels: no redundant issue, no redundant thing built).

## See also

- [`envisioning`](../envisioning/SKILL.md) — the canonical create/revise/
  derive-the-delta/generativity-check flow this skill specializes; its
  [`references/visions.md`](../envisioning/references/visions.md) is authoritative
  for the schema, the detail ceiling, and the positive/negative-intent asymmetry.
- [`visions-setup`](../visions-setup/SKILL.md) and its
  [vision-adherence-runbook](../visions-setup/references/vision-adherence-runbook.md)
  — wiring the "reconcile every change to the vision" habit, incl. binding
  invariants as design contracts down the guidance chain.
- The efforts plugin's
  [`planning-efforts`](../../../efforts/skills/planning-efforts/SKILL.md) /
  `efforts-setup` — carve the additive deltas into an effort.
