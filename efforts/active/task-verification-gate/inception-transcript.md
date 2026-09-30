# Inception transcript: task-verification-gate's producer/evaluator redesign

This sidecar captures the full design-hardening exchange that shaped this
effort's Plan beyond its original request. It's long and is kept verbatim
(the operator explicitly asked for it to be preserved) rather than folded
into the README's own Request section, which stays a navigable summary.

Facility-specific identifiers (host names, live counts) are generalized here
exactly as they are in the README itself -- this file is public.

---

## Round 1 -- the operator's critique of the initial Phase 1-2 draft

> Yeah, I see "design bugs" already.
> *All* tasks should use emitters. "emitter" is not a "kind", it is the
> *only source* of auto-injected tasks. "kind" would point to "a specific
> emitter".
>
> When `agent-dispatch` installs locally, it should bring a set of
> "globally-available emitters". "reviewer-loop", "repository-issue-loop",
> "plugin-companion" are all "unfinished emitters": they need a
> repo-provided registrar to finish them out.
>
> I'd expect, in a consuming repo, to see:
> ```
> .agent-dispatch/registrar/
>   intelligence-dampener-reviewer.yaml
>   permanent-record-writer.yaml
>   coherency-adjudication-board-worker.yaml
>   ...
> ```
>
> Each YAML file should provide (example)
> ```
>   extends:
>     <ref to a global recipe, plugin-provided recipe, recipe in this repo, or recipe from another repo>
>   emitter:
>     <template injections into extension, or direct values>
>   evaluator:
>     <template injections into extension, or direct values>
> ```
>
> The global recipe can provide all the core scripts, and just need some
> variables filled in. Some of those variables can themselves be script
> references, to fill in missing pieces.
>
> If we do things right, most "reviewer-issue-loop" registrations will just
> need to be pure-yaml, no extra script, and just ref the agent-dispatch
> one, mention the target repo, provide a schedule, specify pool criteria,
> and it's done.
>
> A "schedule" is part of an emitter. A bare "schedule-only" emitter might
> take as variables the particular prompt and payload references, and just
> re-fire the given task once every X with the same prompt, or support
> minor templating. A Webhook receiver, or a WebSocket subscriber, are also
> emitter primitives.
>
> For Evaluators, agent-dispatch reaches out to them; they don't need to
> subscribe to anything unless it's for caching. Evaluators should be able
> to COMPLETE, ABANDON, or REQUEUE a task, not emit "replacements". They
> should be permitted to apply a reason for their action, so an agent being
> wakened to look at the task again knows what's different about last time.
>
> Missing in our model is dealing with SUSPENDED states, or agents ending
> their turn without posting updates. Agents just ending their turn
> probably just need to be re-nudged automatically and reminded of their
> goal: post a status update. However, I wonder if the evaluator should
> take responsibility for reviewing SUSPENDED states, too. For example, if
> we say that a reviewer agent should SUSPEND instead of COMPLETE when it
> posts a verdict, to sleep and wait for author updates, then if a reviewer
> does a SUSPEND but didn't actually post a verdict, the evaluator should be
> able to REQUEUE and say "nope, try again".
>
> Alternatively, if we want the posting of the verdict to represent the
> "SUBMITTED" state, and we're willing to have a task's description get
> updated to reflect the new phase of a multi-phase job and then re-queue
> it, we could consider that as an alternative.

**Agent response (gist):** agreed the `kind` taxonomy conflates trigger
mechanism with template-completeness; agreed `extends:` + param-fill is the
natural generalization of the existing recipe-render templating; agreed
evaluators should be pulled, not subscribed (already matched the Phase 2
draft); flagged that `Requeue` was a genuinely missing decision (the
existing framework only had `Emit`/`Confirm`/`NoOp`); could not resolve the
SUSPENDED question unilaterally -- asked the operator to choose between
"evaluator also audits SUSPENDED tasks" (A) vs. "fold suspend/resume into
the SUBMITTED->evaluator->Requeue cycle entirely, task description rewritten
each round" (B), and separately asked whether "agent ends turn silently, no
update" was a distinct unbuilt gap or the same SUSPENDED problem restated.

## Round 2 -- leaning toward B, plus the PR-reviewer worked example

The operator leaned toward B specifically for its "an agent always posts
SUBMITTED when it believes the current ask is done" property, worked through
a PR-reviewer example under both models, and posed the actual decision
criterion: **express each real recipe scenario under both models; whichever
model best fits the full scenario set wins.**

**Agent response (gist):** walked three scenarios (PR reviewer, a
conflict-resolution-shaped recipe, and a one-shot self-tracked worker with no
external wait at all) under both models, and surfaced a directly relevant
existing mechanism the operator hadn't referenced yet: `agent-dispatch run`
(hibernate-the-wait: a detached, cheap OS-level waiter that tears down the
expensive agent session and wakes it only when the wait resolves) plus
`agent-dispatch doctor` (which already audits "is this hibernating task's
wait genuinely still pending, or did the waiter die and this will never
self-resume"). Proposed a synthesis: adopt B's submit-every-round semantics,
but reuse the *existing* hibernate-the-wait/doctor machinery as the reactive
resume path instead of inventing a new emitter-subscribes-per-target
mechanism, with the evaluator's periodic sweep generalizing doctor's
existing liveness-only audit to also check "has the domain condition
already resolved, independent of whether the waiter noticed."

## Round 3 -- the bug-fixer scenario and the real crux

> New scenario: Automated Bug Fixer. Literally takes a backlog of bugs and
> submits fixes for them.
>
> Version A: Task goal is literally "Bugfix pushed through PR and bug marked
> resolved, or bug marked no-repro/duplicate and abandoned". [...] Agent can
> submit BLOCKED if needing guidance from the operator, based on charter
> rules, or to wait after submitting a PR that might require human review.
>
> Version B: Task goal could be literally the same, or it could get *way
> more fine-grained*. [...] Each time the task is re-queued, we *could*
> iterate a state machine, and if the state of the bug ever updates live,
> the emitter could update the bug in place with the next phase [...]
>
> With A, we put a lot of trust into the agent, but we also avoid
> over-constraining it. With B, we risk agents hitting the SUBMIT button way
> too eagerly. On a work machine, I saw agents merely load a skill and then
> "end turn". We've seen with ID that when the Task updates to be "Update at
> commit X", agents may literally say "I already did" and then
> immediately-repost. But if the task (for version A) is always, durably
> "Take next appropriate steps to drive PR X to resolution", with nudges
> only coming in as status updates, agents can re-assess based on current
> state and their own history to decide what to do next.

**Agent response (gist):** identified that the real axis of disagreement
wasn't submission *frequency* but whether the task's own **goal text stays
constant or gets rewritten into a narrow next-step instruction** each round
-- the latter is what produces the rubber-stamp "I already did that, submit
again" failure mode, independent of how often submission happens. Proposed
decoupling the two: keep the goal durable (A's virtue), allow frequent
submission (part of B's cadence) with external state changes arriving as
appended status notes rather than goal rewrites, and have the evaluator
additionally check for detectable new evidence since the last submission
(not just valid-end-state markers) to guard against genuinely eager/no-op
submissions -- `Requeue("no detectable progress; last known state: X")` as
the mechanism.

## Round 4 -- the operator's final decision: Version A, in full

> With version A, an agent can handle its own blockers. And in fact, the
> "BLOCKED" status is intended just to be a user-facing state when an agent
> invokes `request-steer` or `await-steer`, which as far as the agent is
> concerned should be no different than a `pr-wait` or `sleep 500`. We
> *want* agents to wrap their potentially-indefinite blocking calls with
> `agent-dispatch run` so that `agent-dispatch` executes the command on
> behalf of the agent, and shuts down its process in the meantime. The
> underlying process still runs, "ends" at some point, and then
> agent-dispatch wakes the agent and provides the result as the wake-up
> prompt, which is very elegant (can provide a file with the buffered stdio
> and exit code). Two problems can arise:
> 1. command supplied to run deadlocks, hangs, or just takes forever
> 2. agent-dispatch processes all terminate, such as a machine restart, a
>    bad update, or other weird issue
>
> For #1, I'm not sure what defense we have. Only the submitter agent knows
> what to expect.
> For #2, after agent-dispatch recovers, we just sweep any last-known
> callers of `run`, wake the agents, and say "Sorry, something went wrong.
> Infrastructure failure. Try again." (equivalent)
>
> There *is* an option to provide "blessed" `run` requests, which could be
> routed back to the emitters to deal with instead. We could also awaken an
> agent prematurely from its sleep if an emitter posts an update to a task.
> Like with B, emitters could supply not just goals, but also live-updates
> relevant to active tasks.
>
> With version B, we'd still have `run`, but SUBMITTED would formally "put
> the ball back in the emitter/evaluator's court", suspending the task until
> the scripts provide the next state. On one hand, we get that regular
> checkin, but on the other hand, there's a bit of "what are you waiting
> on?" diagnostics that every emitter/evaluator pair needs to provide. If an
> emitter live-subscribes to PR states, then it can just re-awaken a task
> from SUBMITTED state, but that would imply the evaluator got stuck. I feel
> like "SUBMITTED" should mean "only the evaluator owns this now", but then
> it's unclear what nudges the task from its spot.
>
> Long-story short: Let's go with A. Tasks have the full goal upfront, tasks
> can have a carefully-updated event journal (don't go too nuts) to deal
> with nudges and previous rejections, agents only SUBMIT when they truly
> believe the whole goal is done, or agents use `run` to voluntarily yield
> when performing work which might take a (long) while, like posting
> reviews and waiting for response, posting PRs and waiting for response,
> etc. We want agents to drive most work inline, but `run` allows lanes to
> free up when waiting on "events".
>
> For heartbeats, we have the worktree-disposition nudger as part of
> agent-worktrees anyways, and agent-bridge can be used to keep tabs on turn
> count, context usage, tool calls, etc.. Emitters only create tasks,
> evaluators only determine whether the whole goal checksums up, and agents
> take full responsibility for the work in-between.

## The settled architecture (supersedes the Phase 1-2 draft this effort
started with)

- **Emitters only create tasks.** Never rewrite an existing task's goal.
  (The "emitter reshapes the task in place" idea from Round 1/2's Version B
  is dropped entirely.)
- **A task's goal is stated in full upfront and never narrowed into a
  literal next-step instruction.** A bounded, "carefully-updated" event
  journal on the task carries nudges and prior rejection reasons -- status
  notes the agent reassesses against its own judgment, never a rewritten
  goal it can comply with mechanically.
- **Agents submit only when they believe the *entire* stated goal is met**
  -- not per-phase, not per-round. This is a stricter, simpler rule than
  either A or B as originally posed in Round 1.
- **`agent-dispatch run` is how an agent voluntarily yields** for a
  potentially-long wait (posting a review and waiting on response, posting
  a PR and waiting on response, a plain `sleep`) -- the command runs
  detached, the expensive agent session tears down, and agent-dispatch
  wakes it with the buffered result (stdout/stderr + exit code) as the
  resume prompt. `BLOCKED` (`request-steer`/`await-steer`) is just one more
  `run`-wrapped wait from the agent's own point of view -- not a distinct
  mechanism it needs to reason about specially.
- **Two `run` failure modes, two different (non-)answers:**
  1. The wrapped command itself hangs/deadlocks -- no system-level defense;
     only the submitting agent knows what a reasonable bound looks like.
  2. agent-dispatch itself goes down while `run` calls are outstanding
     (restart, bad update) -- on recovery, sweep the last-known `run`
     callers, wake each one, and deliver an explicit
     "infrastructure failure, try again" result (the same shape as a normal
     `run` wake-up, just carrying a failure payload instead of a real one).
     This is the **narrow, correct scope for a `Requeue` decision**: not
     per-round progress auditing (explicitly rejected -- see below), but
     specifically recovering a task whose `run` waiter is confirmed dead
     due to an agent-dispatch-side outage.
  A **future, optional refinement** (not required by this effort, noted for
  later): "blessed" `run` requests routed to a registered emitter instead of
  an arbitrary command, and an emitter being able to prematurely wake a
  sleeping `run` call by posting a task update -- both explicitly deferred,
  not part of this effort's Plan.
- **Evaluators only determine whether the whole goal is met.** Not a
  per-round progress auditor (Round 3's "detect no-op submissions" idea is
  superseded -- the durable, un-narrowed goal plus the agent's own judgment
  is the guard against premature submission instead). Concretely: `Complete`
  (the goal's stated end-state is verifiably true), `Abandon` (a valid
  abandon-condition marker is present), or the narrow `Requeue` above for
  `run`-outage recovery. No general-purpose "did anything change" check, no
  goal-rewriting, no per-round audit loop.
- **Liveness/heartbeat concerns are already covered by orthogonal existing
  mechanisms** -- agent-worktrees' worktree-disposition nudger, and
  agent-bridge's turn-count/context-usage/tool-call tracking -- and are
  explicitly out of this effort's scope to rebuild.
- **`SUSPENDED` is not a status the evaluator manages or audits under this
  design at all** -- it's purely `run`'s own mechanism, recovered only by
  the narrow outage-sweep above, not by any general per-task audit.

This is materially simpler than the Phase 1-2 draft this effort started
execution against (which had a broader `Confirm`/`Abandon`/`NoOp` decision
set with an implied per-interval progress-style sweep). The in-flight
implementation was paused before merging anything, specifically because of
this redesign, and needs to be re-scoped against the settled architecture
above -- see the README's own Plan and Journal for the concrete revision.

## Round 5 -- correction: emitters can update an existing task, narrowly

> Minor correction: Emitters, when subscribed to the things they are
> monitoring, can also update existing tasks, mostly to note important
> changes-of-direction or major events. For example, if a PR merged, a bug
> got fixed or rejected, etc. A running agent should be awakened and told to
> handle the ramifications of the task update, which will allow it the
> opportunity to finalize before posting SUBMITTED or ABANDONED.

This corrects an overstatement in the round-4 summary ("emitters only ever
create tasks -- never rewrite one in place"). The precise rule: a
**subscribed** emitter (one actively watching its target, e.g. a webhook on
PR events) MAY append an **event note** to an existing task it's monitoring
-- never a goal rewrite, only a record of a major external event (merged,
closed, bug fixed/rejected) -- and doing so wakes the task's current
agent (whether actively running, or hibernating via `agent-dispatch run`) so
it can handle the ramifications and decide whether/how to finalize (post
`SUBMITTED` or call `abandon`) with that new information in hand, rather
than finalizing against stale state. This is exactly the "carefully-updated
event journal" the goal-durability principle already called for -- it was
just previously scoped too narrowly to "nudges and prior rejections" instead
of also covering "the emitter's own live event notes." It does not reopen
the rejected "emitter rewrites the goal/description each round" design --
the goal text itself still never changes; only the journal gains an entry
and the agent gets woken.
