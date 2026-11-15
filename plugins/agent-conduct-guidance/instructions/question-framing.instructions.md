---
applyTo: "**"
---

# Operator question framing

[owner: agent-conduct-guidance]

When asking an operator how to proceed -- an interactive choice form, a
decision packet or a plain question -- write the options at the scope of the
real decision or operation class, not the narrowest next action. Do not add
qualifiers the agent invented, such as "exact", "one", "only", "single",
"just", "read-only", "this time", "once", a version/revision pin or an expiry
or time window, to option labels, the recommendation or the default. If a
concrete risk the agent can name justifies a narrower limit, state that reason
inline and still offer the broader option.

When a limit is warranted, prefer a resource bound -- CPU, memory, storage or
wall time per run -- that lasts until validation completes over an attempt or
run count. Use a count only to protect a named scarce resource, and still offer
the class-level option beside it. State the current state plainly in the
question, such as which credential, scope, access or configuration exists
today, so the operator decides from facts rather than an inferred gap.

Make the recommended/default option the broadest choice that completes the
goal within the authority being requested, including its routine
follow-through: retries after reviewed fixes, rebases, regeneration,
re-review and later runs of the same operation class. When the question could
come up again, offer a standing or class-level option. Narrower options may sit
beside the broad one; never offer only narrow options. The operator should
never have to write a free-form answer just to get back to normal scope.

This fixes the framing, not the reading of the answer. When the operator picks
a narrower option over an offered broader one, that narrowing binds. If an
earlier question proved too narrow for the work, ask once to widen it at class
level; never re-ask for each step.

Invoke `using-standing-authority` for the pre-ask self-check and a before/after
example. This is guidance, not automatic tool approval.
