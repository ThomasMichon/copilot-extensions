# budget-guidance architecture

The plugin is an independently installable runtime CLI with four first-slice
layers:

1. `models.py` parses strict, versioned, inert configuration and reading data.
2. `resolve.py` performs deterministic field-level resolution and preserves
   source, capture time, freshness, authority, and contradictions.
3. `math.py` contains pure balance, horizon, sustainable-rate, ceiling,
   projection, and warning-band calculations.
4. `posture.py` builds the single machine-readable posture used by `cli.py` for
   both JSON and human output.

The static adapter is intentionally complete enough for offline operation and
partial enough to exercise multi-source composition. It performs no I/O beyond
reading the selected JSON file.

The fast installer stamp copies the exact owning payload into an immutable
versioned snapshot. Both compatibility and payload-local wrappers provision
only from that recorded snapshot, serialize through the runtime root's
`.provision.lock`, and re-resolve the runtime after acquiring the lock. They do
not search other marketplace installations for a same-named plugin.
Session bootstrap also reads deployed and payload versions without ambient
Python, so an older valid runtime cannot suppress reconciliation to a newer
owning payload on a Python-less host.

Reset horizon and projection are anchored to the posture's `evaluated_at`
instant, while each selected field retains its own source `captured_at`. A
reading whose reset has already elapsed is stale until an explicit future
rollover contract supplies the next budget period. Projection uses the selected
trailing-rate field and the shortest supported trailing window in that reading.

Warning precedence is: stale, contradictory, overspent, reset due, no trailing
rate, projected overspend, rate above the effective daily limit, on track.
Missing required fields produce no calculated block.
