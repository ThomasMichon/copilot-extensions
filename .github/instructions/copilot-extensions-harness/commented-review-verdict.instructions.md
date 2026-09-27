---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","customizationKind":"instructions","destination":".github/instructions/copilot-extensions-harness/commented-review-verdict.instructions.md","plugin":"copilot-extensions-harness@copilot-extensions","pluginVersion":"0.1.0-dev46","renderedBytes":1424,"schema":"copilot-extensions.instruction-projection","sourceId":"commented-review-verdict","template":"instructions/commented-review-verdict.instructions.md","templateBytes":835,"templateSha256":"c1fcb6814822bc490735ee8a275b55f192d5a748bbd0f5c1f82d80943b73533b","version":1} -->
<!-- NOTE: template content edited without a live projection-reflect sync
     (see PR fixing the PR-review-wait protocol); the metadata above is
     stale pending the next real sync pass, but the body below matches
     the current template source. -->

# Commented-verdict review fallback

**Fallback policy `[owner: copilot-extensions-harness]`:** **Before applying
anything below, check the repo you're actually operating in for its own
explicit review-gating policy** (its `AGENTS.md`/contribution docs) --
if one exists, follow that instead; this is only a default for repos with
no such policy of their own. A PR review whose verdict is a plain
**comment** (not an approval or a change-request) is not automatically a
stuck or ambiguous state on hosts with no stricter policy -- it can be
Copilot's normal non-blocking review shape (a `COMMENTED` state never gates
a merge where the ruleset requires zero approving reviews). Absent a
repo-specific override, read its findings as advisory: address genuinely
valuable ones, explain or dismiss the rest, and land the change -- do not
wait for some further verdict that was never going to arrive, and do not
re-request review in a loop chasing a clean pass.
