# Vision-Adherence Runbook (generic wiring / replay)

A repo *adopts* visions and efforts (see `visions-setup` / the efforts plugin's
`efforts-setup`) so the constructs **exist**. This runbook goes one step further:
it wires **vision-first adherence** — the discipline that **every
architectural/behavioral change reconciles to the vision** — down the repo's
whole guidance chain, so the habit is actually *encountered and enforced* as
agents work.

It is deliberately a **runbook, not a hard skill**: an agent references it to
**bootstrap** the wiring in a greenfield repo (starting from just an `AGENTS.md`)
or to **audit/repair** it in an adopted one. The adopting repo supplies the
*bindings* (its own node list and any skill-shaped entry points); this runbook
supplies the generic *pattern*.

> **Guide, not gate.** Visions guide change; they never gate *operations*. Nothing
> here authorizes or forbids an operation — it keeps *intent* the source of truth
> so reality never drifts silently. The only thing that ever gates is **merge**
> (the repo's review flow), and even that is optional teeth (below).

## The model an adopting repo installs

Every change reconciles to the vision as exactly one of three kinds:

| Kind | What it is | The move |
|------|-----------|----------|
| **Vision-closing** | reality lacks/violates a **stated** Feature/Behavior | do the work; **cite the vision item** advanced. (Most bugfixes.) |
| **Vision-extending** | the change introduces intent the vision doesn't state | **revise the vision first** (in place), which *creates* the delta the change closes. (Most features.) |
| **Below altitude** | lint, deps, typos, non-behavioral refactors | proceed; **declare "below-altitude."** The escape hatch. |

The one hard rule: **never silently introduce architectural/behavioral intent
that contradicts or bypasses the vision** — trace it to stated intent or amend the
intent. **Proportionality is mandatory:** this is a reconcile-or-declare *habit*,
not a form; only genuine architectural/behavioral change owes a vision trace.
Without the escape hatch, the discipline rots into bureaucracy.

## The guidance chain to wire (and the flow-through each node carries)

The chain runs from the **always-on** instruction file down through the
**on-demand** guidebooks an agent consults at each stage. Wire the flow-through
into every node the repo actually has:

| Stage | Node (repo supplies the concrete file/skill) | Flow-through it must carry |
|-------|----------------------------------------------|----------------------------|
| **Always-on** | the repo's agent-instructions file (`AGENTS.md` / `.github/copilot-instructions.md`) | The three-way reconcile + the hard rule + the proportionality escape hatch. This is the root — the only always-loaded node. |
| **Planning** | the efforts binding (`docs/efforts.md` / addendum) + the effort skill | An effort **traces to a vision delta** it closes, or **carries an explicit vision extension**. |
| **Architecture** | the visions binding (`docs/visions.md` / addendum) + the arch guide | The intent/spec boundary; architectural change reconciles to stated vision intent (bind the "must-hold" rules as design contracts). |
| **Implementation** | the language/impl standards | Implementation *realizes* stated intent; it must not smuggle in new architectural intent without a vision extension. |
| **Quality** | the review flow / protocol + reviewer agents | Review confirms the change **traces to a vision** (closing / extending / below-altitude). |
| **Entry points** | the "carve an effort from the vision delta" flow | Exists and files issues that **cite vision items**. |

## Two shapes of the job

### Bootstrap (greenfield — repo has only an AGENTS.md)
1. **Adopt the constructs** — run `visions-setup` (scaffold `visions/` + addendum)
   and the efforts plugin's `efforts-setup` (scaffold `efforts/` + addendum).
2. **Install the always-on principle** — add the three-kinds reconcile + hard rule
   + proportionality escape hatch to the repo's agent-instructions file.
3. **Thread the chain** — add the per-node flow-through sentence to each guidebook
   the repo has (planning -> architecture -> implementation -> quality). Where a
   node doesn't exist yet, note it; don't invent heavyweight process.
4. **Provide the entry point** — ensure a "carve an effort from a vision delta"
   flow is documented (compose the `envisioning` derive-the-delta method +
   `planning-efforts`), and that issues cite vision items.
5. **Seed** — author at least one real vision so the discipline has something to
   reconcile against.

### Audit / repair (adopted repo)
Walk each node in the table; for each, check the flow-through is present (in
substance, not verbatim) -> mark **present / weak / missing**; **report** a concise
status table and, for gaps, **propose the exact insertion** in that node's voice.
Prefer **report-and-propose** over auto-applying — landing wording is a reviewed
edit. A clean re-run (zero gaps) is the completion check.

## Optional teeth (repo decides)

The chain above is *guide-only* — it wires the habit without blocking anything.
A repo that wants enforcement can add **one** lightweight gate at **merge** time:
a PR **declares its vision relationship** in one line
(`advances <vision item>` / `extends <vision>` / `below-altitude`), and the
review flow checks the declaration is present and coherent. This gates *merge*,
never an operation, and stays proportionate (often just "below-altitude"). Keep it
optional; the habit + the entry-point flow deliver most of the value without it.

## Anti-patterns

- ❌ Turning adherence into an operation gate ("blocked: no vision") — it guides;
  merge is the only gate, and teeth are optional.
- ❌ Forcing a vision citation on below-altitude changes — the escape hatch is
  load-bearing.
- ❌ Auto-applying wiring insertions instead of proposing them for review.
- ❌ Wiring only the always-on file — the point is the *whole chain* flows through.
- ❌ Re-explaining the vision/effort pattern in every node — point at the
  `envisioning` / efforts guides; nodes carry only the flow-through delta.

## See also

- `envisioning` (create/revise a vision, derive the delta, the generativity
  check) · `visions-setup` (adopt visions) · the efforts plugin's
  `planning-efforts` / `efforts-setup` (adopt + author efforts).
