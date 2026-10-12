# Emitter election diagnosis and migration

An emitter process being alive does not prove that its command executes.
`ok: true, held: false` means the election request succeeded but another
identity owns the pin. A fresh remote pin is not a dead producer.

Read-only inspection combines the live lease with local command health:

```sh
agent-dispatch emitter doctor <spec.json> --holder example-host-wsl \
  --declaration <source-declaration.yaml>
```

The optional source declaration checks local and pinned-holder machine
eligibility. Inspect the current source on each host: a stale checkout can
still declare work removed by a newer filter. An environment tag does not
replace the machine identity used for election and registrar filtering.

`production_state` distinguishes a successful local command tick, a fresh
remote pin, a stale lease, and unknown/failing command health. The stale
threshold includes two declared intervals plus the command timeout.
Staleness is evidence for investigation, not proof that the holder died.

After independently confirming the intended migration and that no old command
is in flight, explicitly authorize recovery using the observed holder:

```sh
agent-dispatch emitter doctor <spec.json> --holder example-host-wsl \
  --declaration <source-declaration.yaml> \
  --apply --expected-holder example-host
```

Recovery only releases a stale foreign pin for an eligible target. It refuses
a fresh lease, a changed holder, or any renewal after diagnosis. It uses a
separate fenced endpoint, so an older coordinator fails closed rather than
silently ignoring the renewal check. It never kills a process, acquires a
replacement lease, or executes the emitter command.

Run the target tick through its usual command, then verify a later ordinary
supervisor-driven renewal. `lease-released` is not restored production.
Pinned leases never auto-transfer, even when an observability TTL expires.
Do not automatically release pins because one contender is denied.

See [producer lease semantics](../README.md#managed-registry--single-producer-job-lease)
and [host migration](repository-issue-loop.md#host-migration).
