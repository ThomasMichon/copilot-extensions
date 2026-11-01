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

## Optional monthly economy advice

Status emits `economy_advice`, a version `1`
`copilot-extensions.budget-economy-advice` object. The budget owner calculates
`availability`, `evaluated_at`, `valid_until`, `warning_band`, `reason` and
optional `suggestions`. An available advice interval ends at the earliest
selected field's freshness deadline, the reset instant, or 300 seconds after
evaluation. It is valid only before `valid_until`, not at that instant. Future
captures, elapsed reset/freshness boundaries, missing fields, errors, and
contradictions withhold the band and deadline (`null`); the source posture
continues to retain its full diagnostic evidence.

This is bounded **point-in-time** information, not continuous provider refresh.
Expired or uncertain evidence is not a current reading. Unavailable evidence
withholds suggestions, never requested work. Suggestions may encourage reuse of
valid results or bounded speculative detours; they do not select models,
delegation strategies or worker counts. No lookup is required before honoring
a request.

The former optional `routing_advice` envelope and model-resolver budget
integration are retired. Existing posture documents still carry useful status
fields, but their saved availability does not establish current evidence.
Quota capture and access validation are unchanged: denial or missing evidence
withholds the reading, not the user's unrelated work.
