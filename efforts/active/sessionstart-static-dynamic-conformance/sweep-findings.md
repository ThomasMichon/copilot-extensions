# Phase 1 Sweep Findings

Back to the [effort README](README.md).

Full marketplace roster: 14 plugins register a `sessionStart` hook
(`agent-bridge`, `agent-codespaces`, `agent-containers`, `agent-dispatch`,
`agent-index`, `agent-logger`, `agent-machines`, `agent-mcp`, `agent-ssh`,
`agent-vault`, `agent-worktrees`, `ai-attribution`, `budget-guidance`,
`context-handoff`). Seven plugins have no `hooks.json` at all
(`copilot-extensions-harness`, `customizing-copilot`, `delegation-guidance`,
`efforts`, `harness-knowledge`, `visions`, `wsl-setup`) and are out of scope
for this sweep.

Investigated via three parallel read-only sweeps, the two plugins classified
directly in the triggering conversation (`agent-worktrees`, `ai-attribution`),
and a direct follow-up pass (this document's final revision) that corrected
two overclaims the initial sweep made — see § Revision history at the end.

## Two distinct compliance tests — do not conflate them

1. **Stdout output-freedom** (what `scan-customizations.py`'s
   `proven-output-free` classification actually checks): does the hook's
   direct JSON stdout stay `{}`/empty? **Every plugin in this roster passes
   this test.** The dominant pattern is "hook stdout is always `{}`; real
   content, if any, is written to a session-scoped
   `instructions/<plugin>/session-guidance.instructions.md` file instead."
2. **Content-shape conformance** (this effort's actual concern): of whatever
   content a plugin *unconditionally* materializes each session — via hook
   stdout `additionalContext` **or** via the side-effect guidance file — how
   much is static prose byte-identical across sessions/cwd/repo state, versus
   genuinely dynamic per-session facts?

**Passing test 1 does not imply passing test 2.** But — important correction
from the initial sweep pass — a **conditional fallback message that only
appears when there is no dynamic content to show** is a materially weaker
case than unconditional static prose. Severity should track *how often* and
*under what condition* the static text actually renders, not merely whether
it exists in the source.

## Confirmed violations (severity: unconditional static prose)

These two are the real findings — static prose that renders **every single
session**, unconditionally, riding alongside a comparatively small dynamic
fact:

| Plugin | What's static (unconditional) | What's genuinely dynamic | Where it lands |
|---|---|---|---|
| `agent-worktrees` | `scripts/conduct/worktree-conduct.md` and `scripts/conduct/account-conduct.md` — both 100% static prose (literal command names, no substitution), included verbatim in **every** session's `additionalContext` via `conduct.py`'s `KNOWN_FRAGMENTS`. | `AW_CONDUCT_DEFINITION` (state-root definition), `AW_CONDUCT_RELATED` (cross-repo related-guidance resolution), `AW_CONDUCT_HISTORY` (worktree effort-binding/succession chain). | Hook stdout `additionalContext` directly (the `session-conduct` hook). |
| `agent-index` | `scripts/emit_scope_binding.py:36-62` (`render()`) — a ~25-line static usage-guidance essay (search/similar/clusters/status syntax, "prefer catalog over `grep`/`glob`" policy, read-only warning) emitted **unconditionally whenever the repo is opted in with ≥1 configured source** — i.e. every session in every repo where this plugin is actually configured, not an edge-case fallback. | Only the per-source `rows` list (repo label, source name, trust domain) is genuinely dynamic — a few lines out of ~30. | Session-scoped `instructions/agent-index/session-guidance.instructions.md` file (hook stdout stays `{}`). |

**Verdict for both:** clear Phase 2 candidates — large, static, human-authored
guidance prose that belongs in a checked-in `.instructions.md` projection
(declared per-plugin, or per-repo-adoption for `agent-index` since its
content is gated on repo opt-in), leaving the hook to inject only the small
genuinely dynamic remainder (definition/related/history for
`agent-worktrees`; the source-list `rows` for `agent-index`).

## Lower-severity finding: shared conditional fallback boilerplate (11 plugins)

Every plugin using the generated `scripts/write_session_guidance.py` template
— confirmed to be **all 11**: `agent-bridge`, `agent-codespaces`,
`agent-containers`, `agent-dispatch`, `agent-index`, `agent-logger`,
`agent-machines`, `agent-mcp`, `agent-ssh`, `agent-vault`, `context-handoff`
— shares byte-identical (apart from the plugin-name substitution)
boilerplate:

- An always-present one-line header, e.g. `# Agent Index session guidance` —
  trivial, squarely within the operator's allowed "explanatory header"
  carve-out. Not a finding.
- A **conditional** fallback paragraph — `"No current <plugin> session
  guidance was available. Treat guidance as unavailable for this
  session-start invocation."` (plus a matching size-limit-exceeded variant —
  see `agent-index/scripts/write_session_guidance.py:111-123` for the
  canonical copy) — that renders **only when the plugin's own dynamic
  producer(s) return nothing** (or exceed the byte budget). In the common
  case where a plugin actually has dynamic content to report, this text never
  appears at all.

This is a real duplication (11 near-identical copies of the same
error-handling string, confirmed by direct diff between `agent-index`'s and
`agent-vault`'s copies), and worth fixing **once, in the shared generator**
(same code-generation family as `emit-command-catalog.sh`'s `# Generated by
libs/payload-invocation/generate.py` header) rather than in each plugin. But
its severity is materially lower than the two confirmed violations above: it
is conditional, short, and arguably legitimate informational text (telling
the agent guidance is unavailable this session) rather than restated policy
prose. **Recommendation for Phase 2:** fix the shared generator template
(dedupe to one constant, or move the message into a tiny checked-in fragment
the generator reads) as a low-priority cleanup pass, separate from and lower
priority than the two confirmed violations.

**`emit-command-catalog` `purpose` strings — not a violation.** The
per-command `purpose` label (e.g. `"Search and operate the semantic index"`)
is a static constant already baked into the checked-in, generated
`emit-command-catalog.{sh,ps1}` script itself (see the
`payload-command-catalog-contract` header comment,
`agent-index/scripts/emit-command-catalog.sh:3`) — fully static and
version-controlled already, not recomputed or restated per session. No
Phase 2 action needed. (Whether it duplicates `plugin.json`'s own description
is a minor generator-hygiene question, out of scope for this effort.)

**`emit-command-catalog`'s framing sentence — not a violation.** "Invoke the
exact `argv` below. Do not search `PATH`..." is within the operator's
explicitly allowed "explanatory headers and sub-text" carve-out for framing a
dynamic value (the resolved `argv[0]` path) — keep as-is.

## Fully clean — no static-content finding at all

`budget-guidance` (no `write-session-guidance` hook registered; only
`bootstrap-check`'s dynamic install/version/manifest facts) and
`ai-attribution` (`sessionStart.context: "none"`, no `additionalContext` and
no guidance-file write at all — everything lives in checked-in
`.instructions.md` projections) need no Phase 2 work of any kind.

## Summary for Phase 2 scoping

| Priority | Target | Scope |
|---|---|---|
| High | `agent-worktrees` conduct fragments | Migrate `worktree-conduct.md`/`account-conduct.md` to a checked-in projection; trim the `session-conduct` hook to the dynamic definition/related/history remainder. |
| High | `agent-index` scope-binding usage essay | Migrate the static usage-guidance prose in `emit_scope_binding.py`'s `render()` to a checked-in projection (repo-opt-in-gated); trim the hook output to the dynamic source-list `rows`. |
| Low | Shared `write_session_guidance.py` fallback boilerplate (11 plugins) | Dedupe the generator template's fallback/size-limit strings to one shared source; low priority, conditional-only impact. |
| None | `emit-command-catalog` purpose strings and framing sentence | Already correctly static-and-checked-in / correctly framing a dynamic value. No action. |
| None | `budget-guidance`, `ai-attribution` | Already fully conformant. No action. |

## Revision history

- **2026-09-08, initial pass:** three parallel explore sweeps classified all
  14 hook-registering plugins; flagged 5 as confirmed violations
  (`agent-worktrees`, `agent-index`, `agent-logger`, `agent-machines`,
  `agent-mcp`) and 3 as pending a follow-up question (`agent-ssh`,
  `agent-vault`, `context-handoff`), largely on the strength of the
  `write_session_guidance.py` fallback boilerplate.
- **2026-09-08, follow-up pass (this revision):** direct source inspection
  found the fallback-boilerplate finding applied identically to **all 11**
  `write_session_guidance.py`-using plugins (not just the original 4-7), and
  — more importantly — that the boilerplate is **conditional** (only renders
  when a plugin's own dynamic content is absent), which downgrades its
  severity well below the two genuinely unconditional violations
  (`agent-worktrees` conduct fragments, `agent-index`'s scope-binding usage
  essay). Restructured this document around severity rather than a flat
  VIOLATION/CLEAN split to keep Phase 2 correctly prioritized.
