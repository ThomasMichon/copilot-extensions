---
name: reviewing-blockers-skeptically
description: >
  Runs a skeptical review before an agent declares itself blocked, parks or
  defers work, escalates a purported prerequisite ("needs X installed /
  granted / fixed first"), or asks a human or component owner to explain or
  fix a failure. Separates real hard stops from assumed ones, enumerates the
  alternate tools, routes, already-authorized credentials, and workarounds
  actually available, tests each cheaply, and -- only when still blocked --
  reports the remaining human-only action with evidence. Use when about to
  write "blocked", "cannot proceed", "waiting on", or "needs someone to", or
  to park, escalate, or hand off because something failed. Not for explicit
  safety, policy, content-exclusion, or access denials (those still stop
  immediately, without probing for a bypass), not for a decision genuinely
  reserved for the operator, and not for routine progress reporting or an
  ordinary suspend while waiting on an expected external event.
---

# Reviewing Blockers Skeptically

A failed preferred route is not a blocker. Agents routinely stop on the first
error, a tool that seemed missing, a credential that failed once, or a
constraint they wrote into their own plan -- and then hand a human a problem
the agent could have solved with what it already had. This skill is the short,
deliberate pass to run **before** that hand-off.

It is stateless guidance. Any record of a verdict belongs wherever the
operator's own environment already keeps task state (a task record, a tracked
issue, a knowledge repository) -- this skill does not define one.

## When this applies -- and when it doesn't

Run this pass when you are about to:

- declare the task (or one step of it) blocked, or stop with "cannot proceed";
- park, defer, or yield a work item because of a failure or missing piece;
- escalate a prerequisite -- "someone needs to install / grant / enable /
  fix X first";
- ask a human, an expert, or a dependency's owner to explain or fix a failure
  you observed;
- repeat a blocker reported earlier (by you or a previous session).

It does **not** apply to:

- **Hard stops** (next section) -- they stop immediately; this pass only
  shapes the report.
- A decision genuinely reserved for the operator (a design crossroads, a
  consequential or irreversible action that requires approval).
- A normal suspend on an expected external event (a scheduled build, a review
  that has not landed yet) -- that is waiting, not being blocked.
- Routine progress reporting.

## Step 1 -- Separate hard stops from assumed stops

**Hard stops end the attempt now.** Do not look for another route, identity,
client, endpoint, or tool to get around them:

- an explicit safety refusal, policy denial, or content-exclusion denial of
  the agent's own operation;
- an explicit access or entitlement denial (the system said "this identity is
  not allowed to do this"), as opposed to a missing or expired credential;
- an explicit operator instruction to stop, or an approval gate the operator
  or policy placed on the action;
- an action whose side effects are consequential and unapproved (publishing,
  sending, deleting, spending) -- ask, don't route around.

Everything else is an **assumed stop** until tested: "the tool isn't
installed", "auth failed", "the service is broken", "the owner must fix it",
"my plan said not to touch that", "I'm still waiting for evidence". An error
returned by the system you are building, testing, or diagnosing is evidence
to investigate, not a denial of your own operation.

## Step 2 -- State the blocker precisely

Write it down before trying to clear it -- vague blockers survive because
nobody can test them:

- **Operation** -- the exact command, call, or step that failed.
- **Scope** -- does it block one operation, one item, or the whole outcome?
- **Condition** -- the exact unsatisfied condition (not "it doesn't work").
- **Evidence** -- the error text or observation, and **when** it was observed.
- **Origin** -- where the constraint comes from: the operator, a
  higher-priority policy, the provider/system, or the agent's own plan. An
  agent-chosen safeguard (a self-imposed cap, an earlier agent's plan step,
  a copied assignment) is not operator authority -- check the actual
  instruction it claims to come from.

## Step 3 -- Enumerate what is actually available

List the alternatives **you can actually use in this session**, verified by a
working call rather than by a catalog listing or memory:

- **Other tools for the same capability** -- a CLI vs. an API vs. an MCP
  server vs. a library vs. a plain HTTP read; a different shell or runtime
  already on the machine.
- **Other supported routes** -- a documented alternate install path, an
  already-running service, a different working directory or checkout, an
  existing session or host that already has the capability.
- **Credentials already authorized for this operation** -- a documented
  same-account renewal, or a token with the right scope that an approved
  route already uses. Never a new identity, a borrowed one, or a different
  client or tenant chosen to get past a denial.
- **Reusable results** -- cached data, an earlier successful output, a
  previous run's artifact, a sibling task that already proved the point.
- **A narrower change** -- do the part that isn't blocked; shrink the
  operation to what the available access permits.
- **An ordinary workaround within existing authority** -- a longer timeout,
  a retry with backoff for a transient error, a smaller batch, a read-only
  check instead of a write.

## Step 4 -- Test each one cheaply

- Prefer read-only, reversible, and bounded checks; cap time and cost per
  attempt.
- Make each check **discriminating** -- it should change what you believe,
  not just restate the error. Rerunning the same failing command unchanged is
  not a test.
- Stop testing a route as soon as it hits a hard stop (Step 1).
- Never replay an uncertain write, retry "until green", or loosen a test to
  make a failure disappear.

Common assumed stops and the cheap check that usually clears or confirms
them:

| Assumed stop | Cheap check |
|---|---|
| "Tool X is not installed / not available" | Locate and execute it through the supported route on this host (a version or help call); look for an equivalent tool. A missing catalog entry is not a missing tool. |
| "Authentication failed" | Classify first: expired or missing credential (one documented same-account renewal, then verify the identity and the exact operation); wrong token precedence or scope (use the approved route that has the scope); explicit entitlement denial (hard stop); unknown or transient (one bounded, discriminating retry). Not every 401/403 is a permanent denial. |
| "The service / dependency is broken; its owner must fix it" | Compare against peers: do comparable cases on the same dependency, environment, and time window fail the same way? If peers pass, the difference is in *this* case and finding it remains your work. If peers fail too, record the correlation -- it still does not by itself prove the cause. |
| "Waiting for more evidence" | Name the next discriminating check you can run now. "Waiting" without a next check is a stall, not a blocker. |
| "My plan / a previous agent said not to" | Find the original instruction. A constraint that is agent-authored and not backed by the operator or policy does not block a proven necessary step. |
| "This was blocked last time" | Re-check the current state. An old report, checkpoint, or verdict is not current evidence; newer successful access invalidates it. |
| "Someone else owns this" | Look for current, dated activity by that owner on the same scope. A stale claim is not active ownership -- but never override an active owner or close their work; narrow your change or coordinate. |

## Step 5 -- Challenge the conclusion

When a separate read-only reviewer (a sub-agent or another session) is
available, give it the intended outcome, the precise blocker from Step 2, the
governing instruction, the dated evidence, the current authorization, and the
alternatives tried, and ask it to **try to disprove** the blocker -- not to
endorse your summary. It should answer:

- Is this an observed external restriction, an inference, or an agent-chosen
  safeguard?
- Is the evidence current and specific to this resource, identity, and
  environment?
- Does a more recent operator instruction override an older restriction?
  (Check provenance -- a relayed or copied message is not automatically the
  operator's.)
- Is there a safe authorized alternative, a narrower change, or a reusable
  result?
- Does it block one operation, one item, or the whole outcome?
- What exact human-only action is indispensable, and what shows the agent
  cannot supply it?

Verdict: **upheld**, **overturned**, **narrowed**, or **unresolved**, with
evidence and the next safe action. A reviewer cannot grant missing authority.
If no independent reviewer is available, do an explicit counterargument pass
yourself and say so -- do not invent a new approval gate just to obtain one.

## Step 6 -- Act on the verdict

- **Overturned** -- act now, in the same run. Don't turn it into another
  planning loop or ask for permission you already have.
- **Narrowed** -- do the unblocked part; report only the remaining narrow
  dependency.
- **Upheld / unresolved** -- report it (below) and continue any independent
  authorized work rather than idling.

## The blocked report

When a blocker survives, report it so the reader can act without
re-investigating:

1. **Operation and scope** -- what is blocked, and what is not.
2. **Exact condition** -- the unsatisfied condition, in one sentence.
3. **Evidence** -- the error or observation, with when it was observed.
4. **Origin** -- operator, policy, provider/system, or agent.
5. **Alternatives tried** -- each route and its result, or why it was unsafe
   or out of authority to try.
6. **The human-only action** -- the single, concrete thing only a human or
   owner can do, phrased as one answerable question or request.
7. **Resume trigger** -- the observable event that clears it, and the next
   action the agent will take when it does.
8. **Continuing work** -- what the agent is still doing meanwhile.

Asking a person a question transfers that question only; ownership of the
investigation stays with the agent.

## Revalidating a retained blocker

Before repeating a blocker or parking again, re-read the current shared state
(the task, the review, the latest attachments or messages) and re-run the
cheapest check from Step 4. When the condition is satisfied or superseded,
clear the wait, keep the history, and take the next authorized action. Age or
repeated no-op continuations are a reason to escalate the *decision*, never a
reason to bypass a hard stop.
