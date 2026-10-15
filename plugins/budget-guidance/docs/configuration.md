# budget-guidance configuration

The configuration schema is
`copilot-extensions.budget-guidance-config`, version `1`. Unknown keys,
unsupported versions, duplicate keys, unsupported adapter types, naive
timestamps, negative values, and non-finite values are errors.
Supported adapter types are `static` and `external-command`. External entries
are inert references; the [external-reader contract](external-readers.md) owns
explicit operator grants, command bounds, attribution, and failure semantics.

Allowance, consumption, daily ceilings, trailing rates, and trailing-window
lengths are bounded to `1000000000000000000` (`1e18`). This provider-neutral
ceiling is far above practical usage quantities while keeping every supported
subtraction, division, and reset projection inside Python Decimal's reliable
arithmetic domain. Freshness is bounded to 315576000 seconds (ten average
years), and source authority ranks are bounded to 1000000. These limits keep
integer conversion constant-space while leaving ample room for long-lived
manual readings and deterministic source layering.

```json
{
  "schema": "copilot-extensions.budget-guidance-config",
  "version": 1,
  "adapters": [
    {
      "type": "static",
      "id": "manual-example",
      "authority": 10,
      "reading": {
        "schema": "copilot-extensions.budget-reading",
        "version": 1,
        "source": "manual-example",
        "captured_at": "2030-01-10T12:00:00Z",
        "freshness_seconds": 86400,
        "availability": "available",
        "allowance": 1000,
        "consumption": 320,
        "reset_at": "2030-02-01T00:00:00Z",
        "daily_ceiling": 40,
        "trailing_rates": [
          {
            "window_days": 7,
            "rate_per_day": 34
          }
        ]
      }
    }
  ]
}
```

Lower numeric `authority` is stronger. Resolution occurs independently for
`allowance`, `consumption`, `reset_at`, `daily_ceiling`, and `trailing_rates`.
Within one authority, the newest `captured_at` wins, followed by source and
adapter id as stable tie-breakers. Every different losing value remains in that
field's `contradictions` array; a lower-authority value never silently replaces
a higher-authority value.

`freshness_seconds` is evaluated against the status command's current instant
(`--at` may supply a deterministic instant). Error and unavailable readings
carry no budget values. An `error` reading requires a non-empty `error` string.

All numeric output is serialized as decimal strings so consumers receive exact,
stable values. The posture schema is `copilot-extensions.budget-posture`,
version `1`.

## Optional routing advice

Status also emits `routing_advice`, a version `1`
`copilot-extensions.budget-routing-advice` object. The budget owner, not a routing
consumer, calculates its `availability`, `evaluated_at`, `valid_until`,
`warning_band`, and `reason`. An available advice interval ends at the earliest
selected field's freshness deadline, the reset instant, or 300 seconds after
evaluation. It is valid only before `valid_until`, not at that instant. Future
captures, elapsed reset/freshness boundaries, missing fields, errors, and
contradictions withhold the band and deadline (`null`); the source posture
continues to retain its full diagnostic evidence.

This is bounded **point-in-time** advice, not a claim of continuous provider
refresh. Consumers reject future evaluations and expired advice rather than
recalculating budget arithmetic or trusting a saved `availability` forever.
Older posture documents without this optional envelope remain useful as status
but cannot supply routing advice. Neither a warning band nor an ordinal model
cost rank establishes the cost or affordability of an individual task.

The delegation module's [optional consumer](../../agent-conduct-guidance/README.md#optional-budget-advice)
accepts an explicitly supplied status JSON file. No routing plugin is required
to use budget status or session guidance.
