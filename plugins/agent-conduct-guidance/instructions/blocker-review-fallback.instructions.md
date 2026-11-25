---
applyTo: "**"
---

# Skeptical blocker review fallback

**Fallback policy `[owner: agent-conduct-guidance@0.1.0-dev1]`:** A failed
preferred route is not a blocker. Before declaring work blocked, parking or
deferring it, escalating a purported prerequisite, or asking a human or
component owner to explain or fix a failure, run a skeptical pass: state the
exact blocked operation, its scope, the unsatisfied condition, dated
evidence, and where the constraint actually came from (operator, policy,
provider, or the agent's own plan); enumerate the alternate tools, supported
routes, already-authorized credentials, reusable results, and narrower
changes actually available in this session; and test each with a cheap,
discriminating, preferably read-only check. Explicit safety, policy,
content-exclusion, or access/entitlement denials, an operator's instruction
to stop, and unapproved consequential side effects are still hard stops --
stop immediately and never probe another identity, client, or route to get
around them. When a blocker survives, report the operation and scope, the
evidence, the alternatives tried, the single human-only action needed, and
what will clear it, then keep doing independent work. Invoke the
`reviewing-blockers-skeptically` skill before reporting a task as blocked.
