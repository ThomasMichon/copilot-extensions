---
applyTo: "**"
---

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
