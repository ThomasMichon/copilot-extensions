# Task conversation direction - 2026-10-07/08

Back to the [canonical effort](README.md).

## Operator request (verbatim)

> I'm thinking about a state where a user makes a manual task like "What is 2 + 2?" and provides a completion criteria as "Provide the answer" or something like that. Ideally, the agent can post SUBMIT and format the answer as standard markdown. The agent isn't *expecting* follow-up, since it's pretty certain it got the answer. But I *could* reject it and provide steering. If I confirm, the task becomes completed. I should then be able to use the completed task as the basis for a new, follow-up task, but I can't "un-complete" a task.
> Just as an agent is allowed to request input by providing structured input, we should allow an agent to emit structured/formatted output in a task as part of a SUBMIT or steer request. This way an evaluator could deterministically review an agent's output, assuming the schema was agreed on upfront. A manual task would have such schema, though I guess the prose of a task could specify it `{ answer: number }`.
> See what it would take it layer this in there.
>
> We'll need logging on delivery attempts and outcomes (not into the task, just diagnostics as normal).

> We need a flag for a task emitter which indicates whether it "auto-confirms" tasks. We'll make it so agents may uniformly only SUBMIT tasks, but the "default evaluator" could just immediately "confirm" it, putting in COMPLETED>
> Manual tasks can have the operator decide per-task for whether to require review (default is requiring manual approval)
> Rest of the plan looks good
> Is it better to use TypeScript or JSON-schema to enforce the contract?

After the recommendation to use JSON Schema as runtime authority, with optional
derived TypeScript types:

> Great. Let's get this codified as the vision and build out and adjust relevant efforts.

## Earlier delivery direction (verbatim excerpt)

> When we Confirm a steering card, the task should immediately go back to CLAIMED or QUEUED, and then we wait for the pool to re-wake the worker after the confirmation. Confirming the card shouldn't block waiting for the worker to wake.

## Settled summary

The worker proposes an output, independent review policy confirms it, rejected
submissions retain their history, and accepted completions never reopen.
Formatted output and structured data serve humans and evaluators respectively.
Steering acceptance is durable and fast; asynchronous delivery must ensure
affirmative receipt. The backend and UI plans extend existing machinery.
Implementation safeguards recommended by the agent are labeled in the
[amendment](task-conversation-amendment.md), not silently attributed to the
operator.
