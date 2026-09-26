---
applyTo: "**"
---
<!-- copilot-extension-instruction-projection {"applyTo":"**","customizationKind":"instructions","destination":".github/instructions/copilot-extensions-harness/commented-review-verdict.instructions.md","plugin":"copilot-extensions-harness@copilot-extensions","pluginVersion":"0.1.2-dev2","renderedBytes":1423,"schema":"copilot-extensions.instruction-projection","sourceId":"commented-review-verdict","template":"instructions/commented-review-verdict.instructions.md","templateBytes":835,"templateSha256":"c1fcb6814822bc490735ee8a275b55f192d5a748bbd0f5c1f82d80943b73533b","version":1} -->

# Commented-verdict review fallback

**Fallback policy `[owner: copilot-extensions-harness]`:** A PR review whose
verdict is a plain **comment** (not an approval or a change-request) is not a
stuck or ambiguous state -- it is Copilot's normal non-blocking review shape on
many hosts (this repo's own ruleset requires zero approving reviews; a
`COMMENTED` state never gates a merge). Read its findings as advisory: address
genuinely valuable ones, explain or dismiss the rest, and land the change --
do not wait for some further verdict that was never going to arrive, and do
not re-request review in a loop chasing a clean pass. If the repo you're
actually operating in has its own explicit review-gating policy documented
(check its own `AGENTS.md`/contribution docs first), that policy overrides
this default.
