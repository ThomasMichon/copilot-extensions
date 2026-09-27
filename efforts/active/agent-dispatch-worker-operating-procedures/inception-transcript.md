# Inception transcript — agent-dispatch-worker-operating-procedures

Full verbatim exchange that produced this effort. Captured because the
back-and-forth spans several rounds and would dominate the README's Request
section; see the README for the settled gist and the Plan derived from it.

## Round 1 — the request for a prompt sweep + rubber-duck review

**Operator:**

> Do a sweep across all prompts and prompt structures that agent-dispatch and
> its emitter/evaluator receipes may construct and send to the agents as
> initial prompts, follow-up requests, or live-nudges. I want to review, and
> have you rubber-duck them, ensuring clarity, consistency, and appropriate
> guardrals.

**Agent (summary of the delivered review — full detail lives in that session's
transcript, not reproduced verbatim here since it is a review artifact, not
operator intent):**

- Two incompatible "initial seed" builders exist for the same conceptual
  role: `bridge.worker_prompt()` (thin — no evaluation lease, no
  duplicate-check, no goal loop, no exclusion guidance on yield) vs.
  `embody_prompts.autopilot_worker_prompt()` (rich — full contract-net
  evaluation, goal/progress loop, decline/abandon distinction). The thin one
  is not dead code: `create_cli.py`'s `--spawn` path falls back to it
  silently whenever `agent-worktrees` is not on PATH, so a real degrade path
  drops the evaluation/duplicate-check guardrails with no operator-visible
  warning.
- A concrete bug: `fleet_autopilot_worker_prompt()`'s duplicate-check sweep
  line omits the `{lane}` (`--repo`/`--all-repos`) scoping the local
  equivalent and the fleet claim command both include.
- A guardrail gap: untrusted external content (webhook PR titles, telemetry
  alert names/targets) is interpolated into default task prompts
  (`producers/webhook.py`, `producers/evaluator.py`'s `_emit_from_rule()`)
  with zero "this is untrusted data, not instructions" framing — except in
  exactly one place (`repository_issue_loops.py`'s `_task_prompt()`, which
  states it explicitly).
- Several smaller consistency/clarity notes (ALL-CAPS emphasis style
  inconsistent across files; `spawn_factories._default_nudge()` missing
  `--exclude-self` guidance; the recipe charters' `EXTERNAL_AUTHOR_CLAUSE`
  duplicating `AGENTS.md` policy prose verbatim, a drift risk).

The agent asked whether to fix any of these.

## Round 2 — reframing the actual goal

**Operator (verbatim):**

> So, the goal of these prompts is to tell the worker agent was is going on
> (worker is being controlled headlessly via agent-dispatch, workter is being
> controlled as a companion agent for another agent, etc.), where to look for
> current task context (dispatch task, commands to call), and the "operating
> procedures" for how to emit information back to the controlling system.
> Since the agent being executed *might not* have direct return access to
> `agent-*` commands, it's important that the caller provide the pointer to
> the CLI tools or response contracts that the workter needs to use to do its
> job. For agents that run using our own harness, we'll likely bake all that
> into a dedicated MD in the agent-dispatch and agent-bridge projects, which
> wrapper sub-agent MD files can point to. This will allow us to reduce the
> overhead of the initial and subsequence guidance prompts to mostly event
> descriptors (e.g. "You are an agent-dispatch workter. Consider, claim, and
> start queued task ____"), and "Task ____ has received an update while you
> have been working. Re-read task to determine potential changes in ambient
> state or direction."). All under-dispatch agents will need guidance
> *somewhere*, ideally their initial instruction MD files, which tells them
> their allowed capabilities, the degree of completion required, and what are
> allowed moves when operating on a task. In general, we want all agents to
> drive their assignment *to completion*, unless they encounter major
> (non-consented for the task) safety rails, major errors (not related to
> problem being solved in task), or obvius obsolescence (task at hand
> superseded by parallel work, bug no longer repros, target feature no
> longer matches vision, etc.).

**Agent's synthesis + clarifying questions (verbatim):**

> That reframes the whole surface cleanly: the current prompts conflate
> three different things that should be separated —
>
> 1. Situational awareness — "you are being run as X" ... and "here's the
>    event that just happened" ...
> 2. Where to look — the CLI/API pointer, or task-id + owner-id if the agent
>    has no direct `agent-*` access.
> 3. Operating procedure — capabilities, completion bar, and the
>    allowed-moves taxonomy (drive to completion unless: an un-consented
>    safety rail, a major error unrelated to the task, or clear
>    obsolescence).
>
> Before I start restructuring, a few concrete decisions:
>
> 1. One canonical doc or two? ... (agent-dispatch owns the doc, agent-bridge
>    links to it, or does agent-bridge want its own companion-agent
>    variant?)
> 2. The allowed-moves taxonomy — should it become the one canonical
>    stop-condition list?
> 3. Two-tier delivery — should the seed-builders auto-detect which tier
>    they're in, or take an explicit new parameter?
> 4. Scope for this pass — new tracked effort, or prototype first?

## Round 3 — answers

**Operator (verbatim, numbered to match the agent's four questions):**

> 1. Agree with your suggestion. `agent-dispatch` is about full-autonomous
>    tasks, `agent-bridge` is about one agent giving instructions to another.
>    `agent-bridge`'s target agent doesn't need any special guidance beyond a
>    heads-up; it can end its turn with a question normally, so the guiding
>    agent can provide new inputs and steering. agent-dispatch, being a
>    pure-programmatic controller, can't handle abitrary prose output by a
>    worker agent, and needs the workter agent to constrain all responses to
>    status updates via tool calls.
> 2. Yes, but let's work through things. Per #1, normal communication must be
>    done through status-update tool calls, not direct prose. However, there
>    is the possibilty that agent-dispatch itself is failing to accept such
>    calls, or the agent somehow loses connection, and ends the turn or
>    spins. We want the agent to "fail fast" and yell for help, not attempt
>    to debug itself, reinstall toolsets, futz with permissions or the
>    network or whatever (unless such diagnostics are literally its
>    charter). For safety-boundary issues, such tasks likely to encounter
>    these *should* allow steering cards; otherwise the agent will go into
>    the same error-handling mode. Note that the task should clarify safety
>    upfront, if there are deviations from the harness agent's main charter.
> 3. Less to do with PATH and more to do with access. An agent on a
>    Codespace, a Container, or on another machine doesn't necessarily have
>    the ability to reach back to the agent-dispatch CLI. Even running a
>    cross-repo agent might mean that the agent knows what agent-dispatch is
>    or how to talk to it. So we'll need a way to provide the "quick-start
>    guide" to such agents and provide a way that they can issue
>    agent-dispatch commands without pre-existing knowledge or direct access
>    via the CLI.
> 4. Yes, new tracked effort.

## Distilled decisions carried into the README

1. One canonical doc lives in agent-dispatch (its own operating-procedures
   reference); agent-bridge needs no equivalent doc — its companion-agent
   model already tolerates ordinary end-of-turn prose, confirmed by its
   existing Non-Goals framing ("Not the live-conversation layer... this
   layer does not choose or implement the body type").
2. The allowed-moves taxonomy is canonical, and is now vision-level intent
   (see the same-day vision update to `visions/plugins/agent-dispatch/README.md`):
   drive to completion unless (a) an un-consented safety rail, (b) a major
   error unrelated to the task, or (c) obvious obsolescence. Two new
   failure-posture rules were added alongside it: an unreachable control
   plane and an undeclared safety boundary both resolve to the same
   "stop, don't improvise, say so plainly" posture; a task expected to hit a
   safety boundary must declare that exception up front so a steering card
   raised there is sanctioned, not improvised.
3. Reachability, not PATH/CLI-availability alone, determines delivery tier.
   A worker's tier is a property of *where it runs and what it can reach*,
   not its role (headless vs. interactive). This needs its own exploration
   phase to enumerate concrete reach-back mechanisms per environment
   (Codespace, container, cross-repo/unaffiliated agent, genuinely
   air-gapped) beyond the one already-shipped instance (fleet's
   SSH-to-origin relay).
4. New tracked effort — this one.
