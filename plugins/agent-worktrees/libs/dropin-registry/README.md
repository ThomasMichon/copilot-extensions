# agent-dropin-registry

Dependency-free primitives for consumer-owned plugin contribution registries
such as `providers.d`, `config.d`, Picker pivots, and `registrar.d`.

The library owns only the behavior every registry shares:

- **scan authority** is `complete`, `absent`, or `indeterminate`;
- each entry is independently `active`, `active-with-advisory`, `inactive`, or
  `indeterminate`;
- an authoritative scan reconciles current desired state, while an
  indeterminate scan retains the last-known set;
- findings are structured and fingerprintable for runtime warnings and doctor
  output;
- operational warnings are aggregate-capped and repeat-deduplicated; and
- writers can atomically replace one entry without touching peers.

Consumers retain their own codecs, provenance/eligibility rules, conflict
semantics, doctor rendering, and optional managed-entry receipt stores. This is
not a generic configuration format.

## Example

```python
from pathlib import Path

from dropin_registry import (
    EntryDecision,
    Finding,
    WarningTracker,
    scan_directory,
)


def classify(path: Path) -> EntryDecision[str]:
    target = path.read_text(encoding="utf-8").strip()
    if not Path(target).is_file():
        return EntryDecision.inactive(
            Finding(
                registry="config.d",
                entry=str(path),
                status="inactive",
                reason="missing-target",
                target=target,
            )
        )
    return EntryDecision.active(target)


previous = {}
snapshot = scan_directory(
    Path("~/.example/config.d").expanduser(),
    classify,
    registry="config.d",
)
desired = snapshot.reconcile(previous)
warnings = WarningTracker().select(snapshot.findings)
```

The full suite contract is
[`docs/patterns/drop-in-registry-hygiene.md`](../../docs/patterns/drop-in-registry-hygiene.md).
