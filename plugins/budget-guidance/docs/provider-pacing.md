# Provider cycle-average estimates and pacing advice

The opt-in [provider quota reader](provider-quota.md) can estimate pace from
the selected quota's `monthlyUsage` in the
[SDK contract](https://github.com/github/copilot-sdk/blob/main/nodejs/src/generated/session-events.ts).
It still reads only the incumbent SDK projection; this does not refresh a
provider, collect charge history, or establish a ledger settlement watermark.

## Evidence qualification

The underlying account reading must already be finite, session-owned, recent,
authoritative, denominated in AI credits, and carry a validated future reset.
Unlimited, denied, stale, ambiguous, non-credit, and caller-declared capture
readings never enable a forecast.

The monthly reading must additionally report:

- `state: available`, `scope: user`, and `unit: ai_credits`;
- nonnegative finite `consumedQuantity` equal to the account consumption;
- an explicit `cycleStart`, `queriedAt`, and `resetOn`;
- the same reset instant as the account budget;
- a cycle start strictly before the service read time, and a read time before
  reset, no later than account observation or evaluation, and within the same
  configured freshness bound.

Missing starts are not inferred from calendar months, reset dates, or a previous
period. Independent counters or reset dates that disagree withhold the estimate
without replacing the valid account balance. Unsupported, missing, stale, or
conflicting rate evidence remains explicitly unavailable, never a zero rate.
Absent or null monthly state, scope, and unit fields are unknown evidence that
withholds only the rate, not the otherwise valid account posture.
Malformed values produce modeled errors rather than arithmetic.

## Calculation and labels

`rate_evidence` records the service's cycle endpoints, consumption and read time,
the elapsed window, `kind: cycle-average-estimate`,
`cycle_start_inferred: false`, and `settlement_watermark: false`.

The average is service consumption divided by elapsed days from `cycleStart`
to `queriedAt`. Existing posture arithmetic projects that average over the
remaining horizon from evaluation time to reset. For example, 500 credits over
10 days gives a 50-credit/day average. With 21 days left, the conditional
projection is 1550 credits; against a current 1000-credit allocation, that is
550 credits of projected overspend.

This is a **cycle-average estimate**, not a measured daily/weekly trailing rate
and not a guarantee that the latest inference charge is included. Changes in
workload and charge settlement can change the result. Allocation is taken from
the current account reading; an increased period allowance is never persisted
as a recurring constant.

## Advice-only boundary

The returned `advice` block identifies estimated overruns or observed overspend
and recommends reducing autonomous aggressiveness when appropriate. An
on-track estimate recommends maintaining with caution, not automatically
increasing parallelism.

`concurrency` remains null: account totals alone do not establish per-worker
cost, a safe worker count, or model eligibility. Advice never blocks
operator-directed work, launches or changes workers, sleeps, suspends,
qualifies a model, clears a hold, or weakens a product/review/safety gate.
Session-start delivery and routing integration remain separate effort items.

Cooperative pacing is an explicit operator or autonomous-owner decision, used
sparingly after considering the task and current evidence. A warning does not
authorize a suspension, create a timer, or turn a freshness bound into a sleep
interval. This helper offers no worker-control interface.

See [budget-guidance](../README.md) for offline/manual posture support.
