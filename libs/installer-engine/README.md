# Installer engine source and derived payloads

Edit canonical implementation files here. Contributors do not refresh committed
developer copies to publish a change: the Dev-to-Main composer materializes the
declared installer family in its candidate tree before freezing that tree.

`tools/materialize_installer_engine.py` uses `installer_engine_ref.py`'s existing
shell, Python and standalone-native consumer registries. It replaces stale or
absent derived slots only in existing declared consumers. Candidate content must
match either canonical bytes or its source-tree preimage; foreign replacements,
links, shared inodes and nonordinary paths are refused before copying.
Native canonical-reference conversion retains its existing source checks.
Standalone native SDK copies also refresh the shared settings and retained-input
support family through the same canonical/source-preimage checks.
The separate invocation owner generates admission-toolkit copies.
Already-local native references receive the same verified writer refresh.
The normal and transitive library copy owners also pass the actual canonical
consumer and copied destination to this verifier. A packaged Update Driver
therefore receives current Installer assets before its caller can consume it;
its reviewed library source, not a missing developer mirror, supplies the
admitted preimage. This works with shared or separate source/candidate roots.

Native human-output declarations bind the original entrypoint and initializer
preimage, not equality with today's canonical initializer. An absent initializer
retains its declared digest; an existing one must match that digest. Promotion
refreshes these declared helpers, including standalone installers, through the
same owned-preimage checks, then rebinds the declaration after candidate rewrites.
Malformed declarations and foreign candidate replacements still refuse copying.

The single-consumer wrapper used by source preparation and previews invokes the
same verified refresh. Canonical references refresh missing or stale support
assets after reference validation and before native conversion; foreign assets
refuse conversion. Already-local writers and standalone Python consumers are
covered after conversion succeeds. The whole-tree composer keeps its existing
single refresh pass.

Validation phases are explicit:

- `sync-installer-engine.verify(phase="development")` checks canonical producers,
  declarations and ordinary owned layouts. Stale or absent derived bytes are not
  packaged qualification. The development install-contract check uses this phase.
- The default `verify()` / `phase="materialized"` retains exact-copy checks.
  Publication must validate and test the actual composed candidate, not infer
  packaged behavior from a successful development-layout check.

The reached composition owner also verifies every file in its derived-file plan
against admitted canonical bytes, checks ordinary single-link identity before and
after reading, and refuses changed source or candidate identity. A refusal is a
`SKIP` result, which the promotion composer rejects before freezing its tree.
The standalone exact-copy API is not itself evidence that publication called it.

Canonical bytes and stable file identity are snapshotted once per unique source
for one composition call, reverified before any writes and again after all
destination checks. Every destination retains its own preimage, ordinary-path,
single-link and postwrite byte/identity checks. The snapshot is call-local, not a
cache across candidates. A source change during copying refuses composition even
when destinations contain the originally admitted bytes.

The legacy sync CLI remains a maintenance tool, not a contributor publication
step. Raw developer copies can still be stale; do not make a failing copied
consumer pass by substituting a canonical dependency. Qualify its real composed
payload instead. Materialization does not install or update a running runtime.

The separate composed Vault consumer regression builds a private payload through
the production materializer, then executes its copied remover and command
recognizer without candidate-side canonical libraries. It preserves the original
foreign-comment refusal assertions and checks exact owned-template recognition.
This does not rerun or relabel the historical raw-Dev copied-consumer failure.
