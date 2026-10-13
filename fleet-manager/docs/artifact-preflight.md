# Role-selected artifact integrity preflight

Back to [fleet-manager](../README.md).

```text
python -m fleet_manager.artifacts --release release.json --sources sources.json --role client
```

This installed, standard-library-only module reads actual local artifact bytes
for exactly one role in the existing release manifest. It does not install,
extract, execute, download or activate them. The client lifecycle owner remains
#6424; release signing and Gateway/node runtime remain unresolved under #6240.
It does not replace installed package RECORD validation.

`sources.json` is strict bounded JSON:

```json
{
  "schema": "fleet-manager.artifact-sources",
  "version": 1,
  "components": {
    "cli": "cli.whl"
  }
}
```

Keys must name declared release components. Every selected component needs a
source. Sources for other declared components may be supplied but are never
opened; client preflight needs no server artifact. Paths are explicitly supplied,
resolved relative to the **current working directory**, not the source document
or a discovered installation. No ambient artifact registry or URL is consulted.
Windows drive-relative paths such as `C:artifact.whl` are refused.
Use non-secret paths and documents; argparse syntax diagnostics can echo arguments.

Artifacts must be ordinary nonempty files, at most 64 MiB each and 512 MiB in
total for the selected role. Streaming reads are at most 64 KiB. Links and
Windows reparse points in the inspected path chain are refused. Opening is
checked against the inspected file identity and size; content timestamps and
size are compared on the same opened handle before/after reading, and the path
chain/file identity is checked again. Path metadata timestamps are not compared
with handle timestamps: the two OS lookup paths can report different values
without a content change. Nonregular inputs, unreadable/missing
files, observed concurrent changes, digest mismatches and oversized sets fail
explicitly without printing paths or file contents.

Exit 0 produces a schema-v1 `fleet-manager.artifact-preflight` with:

- Canonical `release_digest`, display `release`, selected `role`, and
  `status: integrity-match`.
- Only selected `components`, each with component ID, SHA-256 computed from
  its read stream and byte count; aggregate `total_bytes`.
- `evidence: explicit-local-file-bytes`, `authenticity_verified: false`,
  `activation_authorized: false`, `live_readiness_verified: false`, and
  `mutations_performed: false`.

Exit 1 reports invalid input or failed preflight on stderr with no success JSON.
The Python API is `verify_role_artifacts(release, sources, role=...)`; its inputs
are preserved and its failures use the existing `PlanError` contract.

**Integrity is not authenticity or installation readiness.** An attacker can
replace both an unsigned manifest and its artifacts. This result is a historical
report of the exact streams read, not a reusable trusted object, frozen staging
directory, cross-host fence or permission to install. Files can change after a
read; metadata checks are best-effort race detection, not filesystem isolation.
An installer must independently authenticate the selected release against its
configured trust policy, stage owned immutable bytes, recheck those staged
bytes and compatibility, then perform its own ownership-checked activation.
No artifact size/hash assertion proves executable dependencies or API/schema
behavior.

This is a non-overlapping product-assembly prerequisite for the reviewed
machine-fleet integrated release intent. It leaves the standalone thin client
dependency-free and does not create a core-agent marketplace installation cell,
parallel installer or competing routing/auth authority.
