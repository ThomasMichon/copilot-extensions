# Measurement contract inception

Back to [effort](README.md). Operator text below is preserved with only private
repository/product/organization identifiers replaced by bracketed placeholders.
This record is intent provenance, not evidence that any instrumentation exists.

## Operator - measurement request

> Great. Now, what we need to do is decide what metrics we want to be able to capture. I have a small list, but by no means should we limit to this:
>
> 1. Startup time and health of loading Worktree Manager:
>   a. Does it render *anything*
>   b. Time to load current machine's worktrees
>   c. Time to load worktrees after switching machine pivots
>   d. Time and success of loading an actions menu
>   e. Time loading any other pivot, differentiated by provider
>   f. Time to load the machine list (and success)
> 2. Execution times of agent-worktrees worktree commands:
>   a. Time and reliability to create a new worktree, minus update time
>   b. Time and reliability to resume a worktree
>   c. Time and reliability to open a live worktree
>   d. Time and reliability to stop/reclaim a running worktree
>   e. Time and reliability to reconcile plugins
>   f. Time and reliability to perform instruction injections
> 3. Occurrences of extension and hook calls
>   a. agent-worktrees
>   b. agent-bridge
> 4. Execution times of agent-bridge calls:
>   a. Time and reliability of transport connection
>   b. Time and reliability of target workspace setup (e.g. copying session-host, installing plugins, installing agent-worktrees or agent-bridge, etc.)
>   c. Time and reliability of session-host startup
>   d. Time and reliability of launching Copilot
>   e. End to end time and reliability of launching bridges
>   f. Time and reliability of sending input
>   g. Time and reliability of reading states
>   h. Occurrences of session-not-found
>   i. Occurrences of transort disconnections
> 5. Execution times of agent-dispatch calls:
>   a. Time and reliability of emitter startup
>   b. Time and reliability of evaluator startup
>   c. Time and reliability of reservation spawn
>   d. Time and reliability of steer confirmations
>   e. Time and reliability of tas insertion, updates, etc.
>   f. Total queue time of dispatch tasks
> 6. Accumulated stats:
>   a. Byte size and file count of instruction set on session start
>   b. Byte size and file count of skill description set on session start
>   c. Byte size and file count of sub-agent description set on session start
>   d. Context percent at handoff trigger (whether native or not)
>   e. AIC of session upon completion or finalization
>   f. Turn count of session upon completion or finalization
>
> There is probably a ton more. We can prioritize based on what people have complained about in terms of performance or reliability of our own issues list, in [downstream harness] of copilot-extensions.
>
> The goal is to put the start/end hooks in all the necessary places to capture this information, choosing event names and reasonable schema. Honestly our QoS and Engagement schema systems from [product] are pretty good, and we shold just reuse that concept:

```text
Qos {
  name,
  startTime,
  endTime,
  duration,
  resultCode: string,
  resultType: 'Success' | 'Failure' | 'ExpectedFailure'
}
Engagement {
  name: string;
  time,
  ...params
}
```

## Operator - delivery scope

Selected `contract`: measurement contract, catalog and prioritized
instrumentation plan first, not runtime hook implementation.

## Operator - OpenTelemetry clarification

> I don't know what OpenTelemetry specifically uses but I guess try to align with that system

## Operator - effort name

> telemetry-measurement-contract

## Operator - failure classification

> Does OTel have a failure-bucketing system? At [organization] we split Failure into Expected and Unexpected regularly

The design answer preserves the classification as custom data. Both actual
failure buckets use OTel Error; a successful no-op is not an ExpectedFailure.
The catalog/contract distinguish that semantics from changing a product result.

## Operator - creation phases clarification

> We can split the worktree creation flow into phases, reporting an end-to-end all-inclusive span, and an update span, separately. No need to make an exception

This supersedes the initial "minus update time" requirement. The accepted design
uses ordinary nested spans with no subtraction-based measurement exception.
