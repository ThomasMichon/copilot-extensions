# AI Attribution — Audience-Based Disclosure Policy

- **Slug:** `ai-attribution-audience-policy`
- **Repo:** copilot-extensions
- **Branch(es):** per-phase (three separate PRs)
- **Created:** 2026-09-19
- **Status:** Active
- **Vision:** below-altitude fix to existing `ai-attribution` plugin behavior;
  no repo vision currently covers publication/attribution policy specifically.
- **Umbrella issue:** #2965

## Guiding Intent

Today `ai-attribution` keys its disclosure decision on repo **ownership**
(operator-owned repos omit disclosure by default; third-party repos require
it). The operator wants the decision keyed on repo **audience** instead:
public and internal repos disclose by default (even when operator-owned);
private repos are exempt, but private status must be positively verified, not
assumed. Per-repo overrides (e.g. "skip disclosure when opening an issue/PR in
a repo I maintain, but still disclose on replies to others' threads") are
supplied via `agent-worktrees`' `related.yaml` — an operator-authored, local
manifest — never the target repo's own tracked config, which stays untrusted
for policy-weakening. Separately, live person-to-person channels (chat,
email) get a stricter rule: disclosure is required unless the recipient is
clearly another agent.

## Participants

| Participant | Role in this effort | Reached via |
|-------------|---------------------|-------------|
| Primary driver | Design + implement all three phases | This worktree |

## Request

> ai-attribution is decently important.
>
> We need to ensure:
> 1. Whether to apply attribution is determined on a per-repo basis, with the
>    default behavior being to always apply attribution to bug-filing, Pull
>    Request submission, and all comments made on Pull Requests or filed
>    issues. This applies to public or internal repos, unless otherwise
>    configured. Private repos do not require attribution, but it has to be
>    clear that it's private.
> 2. AI attribution must be governed by per-repo config, supplied via the
>    same related-repo system we have using agent-worktrees. Repos mostly opt
>    *out* of attribution, since the default (when in doubt) should be to
>    apply attribution.
> 3. AI attribution is 100% required if interaction with normal
>    person-to-person communication channels. Teams, Outlook, and other chat
>    should always have attribution unless it's clear we're sending to
>    another *agent*.

## Coordination

- **Topology:** independent per-phase PRs, landed serially.
- **Host (owns PRs):** the driving agent/session.

## Context

- Current behavior: `plugins/ai-attribution/skills/ai-attribution/SKILL.md` §2
  ("Apply attribution") — omit disclosure by default for a "verified
  operator-owned repository."
- `agent-worktrees`' `related.yaml` already carries an `ownership:
  owned|internal|external` field whose doc comment explicitly says it drives
  "the AI-attribution decision" (`plugins/agent-worktrees/src/agent_worktrees/related.py`
  around `VALID_OWNERSHIP`) — but that axis conflates "who owns it" with
  "does it need disclosure," and has no private/public/internal-audience
  concept at all. This effort adds that missing, orthogonal axis rather than
  overloading `ownership`.
- Confirmed with the operator (2026-09-19): the default-flip is intentional;
  `copilot-extensions` itself will be configured with an explicit override
  (no disclosure on **opening** issues/PRs since the operator owns and
  contributes to it directly, but disclosure still applies on **replying** to
  others' existing threads/comments — a politeness distinction, not a safety
  one).
- "Internal" = org-internal (e.g. enterprise ADO, internal Gitea) — visible to
  coworkers/org members, not the public internet.
- Chat/email channels (Teams, Outlook, etc.) are a distinct scope extension of
  `ai-attribution` itself (not a new plugin): disclosure is effectively
  mandatory there unless the recipient is clearly another agent.

## Plan

### Phase 1 — `agent-worktrees`: add the `audience` axis to `related.yaml`
- [x] Add `audience: public|internal|private` to `RelatedEntry` (new
  `VALID_AUDIENCE` tuple, `normalize_audience`), orthogonal to `ownership`.
  Empty = unclassified (consumers judge for themselves, same failure-open
  posture as `ownership`). **Landed**: `related.py`.
- [x] Add an optional per-repo attribution override block, e.g.
  `ai_attribution: { disclose_on_open: bool, disclose_on_reply: bool }`
  for the open-vs-reply distinction. **Landed**:
  `_parse_ai_attribution`/`effective_ai_attribution` in `related.py` --
  defaults derive from `audience` (public/internal/unclassified -> both
  True, private -> both False); a present per-key override is honored
  verbatim (either direction relative to that default), an absent key
  stays at the audience-derived default.
- [x] Round-trip YAML read/write, `related resolve --json` surfaces both.
  **Landed**: `write_related`/`_parse_related_file`/`upsert_related` in
  `related.py`; `related_cli.py`'s `list --json`, `show --json`, and
  `resolve`/`resolve --json` all surface `audience`/`ai_attribution`
  (`related.py`'s own CLI logic moved out of `__main__.py` into
  `related_cli.py` since this effort's kickoff -- ported there instead).
  `add --audience public|internal|private` registers it.
- [x] Update `agent-worktrees-related` SKILL.md's schema section + annotated
  `references/related.yaml` example. **Landed**.
- [x] Tests: parsing, normalization (bad values dropped, not asserted),
  round-trip write, `resolve --json` output shape. **Landed**:
  `tests/test_related.py`'s new "Audience + AI-attribution override"
  section (9 model-level tests, plus a CLI-level test asserting
  `resolve --json`'s `audience`/`ai_attribution` fields across audience
  values including the unclassified fail-open case) -- round-trip,
  normalization drops unknown values, `_parse_ai_attribution` drops
  unknown keys/non-bool values, no-audience emits nothing, upsert merges,
  `effective_audience` never derives (unlike ownership),
  `effective_ai_attribution`'s audience-keyed defaults and verbatim
  per-key override (either direction, not narrow-only).

### Phase 2 — `ai-attribution`: consume audience instead of ownership-only
- [ ] Hook logic (bash/powershell) resolves the target's `audience` +
  attribution override via `agent-worktrees` when present (shelling out to
  its own binstub/API, never re-implementing YAML parsing in the hook's
  restricted grammar); falls back to today's ownership/git-remote heuristic
  when `agent-worktrees` is absent or the repo isn't registered.
- [ ] Flip the documented default in `SKILL.md` §2: disclose by default for
  `public`/`internal` audience; omit only for a **verified** `private`
  audience (verification method TBD in implementation — likely requires a
  positive signal, not silence).
- [ ] `docs/configuration.md` updated to describe the new precedence (audience
  resolution sits where ownership resolution used to, ownership keeps its
  other non-attribution uses if any).
- [ ] Update `tests/test_emit_policy.py` (40KB, extensive) for the new
  behavior; every existing invariant that still holds must still be asserted.

### Phase 3 — `ai-attribution`: live-channel (chat/email) disclosure rule
- [ ] New SKILL.md section: disclosure is required for Teams/Outlook/chat
  sent to a person, unless the recipient is clearly another agent. This is
  guidance-only (no hook mechanism — chat channels aren't git-repo-scoped).

### Phase 4 — Configure `copilot-extensions` itself (operator-side, private)
- [ ] In the operator's own (private, not-in-this-repo) `related.yaml` entry
  for `copilot-extensions`: `audience: public`,
  `ai_attribution: { disclose_on_open: false, disclose_on_reply: true }`.
  Tracked here only as a pointer; the actual config change lands in the
  operator's private control repo, not copilot-extensions.

## Validation Plan

- Phase 1: `pytest` for `agent-worktrees`' related-entry parsing/round-trip;
  `related resolve --json` manual smoke test against a fixture repo with each
  `audience` value.
- Phase 2: `pytest` for `ai-attribution` (bash + powershell parity per
  `test_emit_policy.py`'s existing structure); manual smoke test: a fixture
  "public, operator-owned" repo now emits disclosure-required, a "private"
  one does not.
- Phase 3: doc-only; no automated test surface (chat channels are outside the
  hook's reach) — reviewed for clarity only.

## Proposal

_Pending._

## Journal

### 2026-10-03 — Recovered from an abandoned worktree, Phase 1 completed and landed
This effort's Phase 1 implementation sat uncommitted in a `copilot-extensions`
worktree from kickoff (2026-09-19) that was never finalized and fell ~1,222
commits behind `dev` -- discovered only because it blocked finalizing an
unrelated session's own worktree via the resource-obligation gate. Rebased
the worktree onto current `dev`; the stashed diff applied cleanly against
`related.py` but conflicted against `__main__.py`, because `related`'s CLI
dispatch logic had since been extracted into its own `related_cli.py` module
(componentization work landed after this branch forked) -- ported the same
hunks to their new home by hand rather than fighting the conflict in a stale,
10k+-line monolith.

Confirmed the underlying ask was still live and un-duplicated: issue #2965
still open, no later commit implements an equivalent `audience` axis.
Finished the two checklist items the stashed diff hadn't reached yet (the
SKILL.md schema doc + `references/related.yaml` example, and tests covering
round-trip, normalization, and the audience-keyed disclosure-policy
resolution/override logic) and trimmed the ported docstrings for brevity so
`related.py`'s growth (1717 -> 1826 lines, after three rounds of
review-driven additions) stayed as small as reasonably possible -- still
required a deliberate, reviewed widen of its shrink-only
`module-size-baseline.json` entry (a sanctioned path per `CONTRIBUTING.md`'s
own Code Style section, not a silent bypass). All `related`-module tests
(187) plus the standard validation suite (`check-module-size`,
`check-docs-consistency`, `check-version-consistency`,
`check-effort-vision-structure`) pass clean. **Phase 1 is done.**

PR #5084's own review (3 rounds) caught real issues worth recording
candidly: the first round found that every piece of ported documentation
(this doc, the SKILL.md, `references/related.yaml`, and a `related.py`
docstring) had inherited a "narrow-only, never widens" framing for the
`ai_attribution` override that the actual implementation (and the
already-landed private-repo test) contradicts -- a present override key is
honored verbatim in either direction, not capped to turning disclosure off.
Corrected every occurrence rather than rationalizing the mismatch away. The
second round caught a real robustness gap: `normalize_audience` (and the
pre-existing, symmetric `normalize_ownership`) called `.strip()` on
whatever YAML produced for that key, so a malformed `audience: 123` raised
`AttributeError` and broke loading the *entire* related config, not just
that one entry -- fixed with an `isinstance(value, str)` guard on both
functions, plus a regression test. Also fixed the human-readable `resolve`
output unconditionally hiding the audience/policy line for the unclassified
case (it should read "unclassified," not vanish, since that case's
resolved policy is still the real, fail-open `True`/`True`), and added the
missing canonical `## Proposal` section this doc had skipped.

The third round caught the most significant gap -- a genuine security hole,
not a docs mismatch: `effective_audience`/`effective_ai_attribution` honored
*whichever* grafted entry won, including a target repo's own tracked
`related.yaml` (the `"repository"` origin layer). An untrusted repo could
therefore add a self-entry claiming `audience: private` (or a narrowing
`ai_attribution` override) to suppress its own AI-attribution disclosure --
exactly the "never the target repo's own tracked config" risk this effort's
own Guiding Intent named at kickoff, which the implementation had failed to
actually enforce. Fixed by gating any disclosure-*weakening* claim (a
`private` audience, or an override that turns a key off) behind a new
`_TRUSTED_FOR_POLICY_WEAKENING` set (`harness`/`machine`/`knowledge` --
the operator-controlled overlay layers `read_related_grafted` already
tracks via `origin_layer`) -- an untrusted source can still *widen*
disclosure freely, only narrowing requires operator-authored provenance.
Added both model-level tests (an untrusted `private`/narrowing claim is
discarded; the identical claim from a trusted layer is honored; widening
from an untrusted source still works) and a CLI-level `resolve --json`
assertion proving the same gate holds through the real dispatch path. Also
updated the two schema docs the earlier rounds hadn't reached
(`docs/config-reference.md`, `docs/configuration.md`) and added a
`resolve --json`-level assertion alongside the existing `show --json` one,
since the former is the actual Phase 2 consumer contract.

### 2026-09-19 - Kickoff
Operator requested rework of `ai-attribution`'s disclosure default from
ownership-keyed to audience-keyed, plus a live-channel (chat/email) rule.
Confirmed design specifics via `ask_user` (default-flip intentional,
`copilot-extensions` gets an open/reply override, field name `audience`,
chat scope stays inside `ai-attribution`). Filed coordination issue #2965.
Starting Phase 1.
