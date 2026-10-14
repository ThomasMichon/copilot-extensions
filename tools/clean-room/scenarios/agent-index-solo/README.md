# Released Index solo installer witness

This Tier-P/F1 scenario installs only the released `agent-index` payload.
It clones the public marketplace's `main` inside the disposable container,
optionally fetches an exact released commit (`CR_RELEASE_REF`), and checks
installed installer bytes and both vendored installer-engine files against
that release. `CR_EXPECT_VERSION` optionally pins the plugin version. Neither
the development checkout nor a host runtime is mounted as the product.
For an unshipped candidate, pass a **materialized public release-form directory**
with `--harness-mount`, `--pass-env CR_CANDIDATE_HEAD` and the exact source HEAD.
Build it after committing, using the canonical whole-marketplace materializer:

```bash
python tools/materialize_main.py --candidate --dest <new-outside-repository-directory>
```

This archives committed HEAD (not uncommitted checkout bytes), applies the same
file/lib/installer-engine/launch-wrapper expansions as promotion and plugin
preview using the materializer and helper code from that same archive in an
isolated subprocess, and writes `MATERIALIZED.json`. Dirty edits to host
transformation modules cannot change those candidate bytes. The subprocess
refuses a destination that appeared during preparation instead of deleting it.
The receipt uses the existing preview
`source_commit` vocabulary and inventories every materialized path, file SHA-256,
executable bit and in-tree symlink target. No `.git` is needed in the resulting
standalone marketplace; no hand-written receipt or preview overlay is required.
Manifest traversal explicitly does not follow directory links: it records the
link itself, prunes aliases/cycles and refuses unreadable subtrees rather than
silently omitting paths. Windows junctions and other non-symlink reparse points,
including a redirected root, are unsupported and refused before traversal.
Regular artifact files and the receipt must each have exactly one hard link;
external or in-tree hardlink aliases are refused even when their bytes match.
The scenario's separately mounted trusted library compares the receipt with
`CR_CANDIDATE_HEAD` and verifies the complete copied tree before installing any
plugin. Missing, wrong-HEAD, stale, added/removed-path or tampered-byte evidence
fails closed. The receipt binds an honestly built artifact to its source claim;
it is not a signature authenticating an untrusted builder that can forge both
the receipt and bytes. Do not replace the verifier with code from the candidate.
Archive extraction supports the repository's Python 3.10 floor without the
version-specific `TarFile.extractall(filter=...)` API. It validates all members
before extraction, accepts only regular files/directories, rejects traversal,
links and device entries, and refuses destinations resolving outside its private
temporary root. There is no unfiltered-extraction fallback.

The scenario copies the candidate into its own filesystem and records
`product_kind=materialized-candidate-not-shipped`. It never treats raw `dev` or
a candidate as a shipped release.
The Copilot CLI installs a directory marketplace live from the cloned release;
it does not copy that payload into `installed-plugins`. Only Index is enabled.

```bash
CR_RELEASE_REF=<released-commit-sha> CR_EXPECT_VERSION=<released-version> \
CR_RESULTS_DIR=<outside-repository-results-directory> \
./run.sh --scenario agent-index-solo --name-suffix <fresh-unique-suffix> \
  --token-account <entitled-account> \
  --pass-env CR_RELEASE_REF --pass-env CR_EXPECT_VERSION run
```

Check that the container name does not already exist. A failed box is evidence,
not the starting point for another proof. Retain it until cleanup is authorized.
Auth is borrowed through the runner; do not commit images containing it.
Only the existing one-word Copilot session witnesses the session hook. The
remaining checks are direct CLI/API calls, not Tier-E agent evaluation.

## Contract areas and coverage

| Contract area | Required behavior and coverage |
|---|---|
| Repository admission | Non-opted-in repositories report `inactive`; no runtime or service may appear. A canonical corpus-only repo config reports `setup_required`/`absent`. |
| Metadata-only session startup | Non-opted-in startup remains inactive/dormant. Opted-in startup may stamp only metadata/binstubs; `setup_required`/`stamped` is not runtime readiness. No venv, deployment manifest, service, engine or autostart may appear. |
| Payload-local command ownership | The installed payload's documented `bin/agent-index` is invoked by exact path on stock login PATH. No PATH injection or expectation of machine-global command ownership. |
| Genuine incomplete owned slot | A materialized payload snapshot with genuine provenance precedes the shipped owned-slot primitive; `uv venv` makes a real incomplete venv with no Index import, completion or selected marker. Status must report `setup_required`/`broken`, never ready or absent. First-use setup must repair/build it through the actual cell installer. |
| Explicit repository roles | Canonical repo designations prove client/host roles before runtime creation. Corpus-only config proves no-role setup refusal. This does not claim a remote client's transport or installed client profile. |
| Immutable dependency profile | The immutable host dependency profile rejects no-role dispatch with exit 126 and the specific dependency-profile mismatch. The genuine completion remains intact; no fake receipt is accepted as deployment evidence. |
| Governed first-use provisioning | Explicit OS-profile installation-mode opt-in and generation-pinned activation precede first-use setup, which must reach the real runtime gate's cell installer. Default policy retains legacy mode without activation. Existing legacy metadata is truthfully retained inert, not declared absent. No authorization override or legacy-probe bypass. |
| Owned runtime and promoted health | The real venv must import from its slot and report the exact released version. `/health` and `/status` must identify that installed host profile, one live owned instance, PID and token; health must be promoted, non-passive and `ok`. |

The managed host omits the optional MCP SDK. The direct MCP check proves the
SDK is absent and `mcp` refuses with exit 2 and `[stdio-mcp]` opt-in guidance.
An explicitly opted-in adapter's five-tool stdio handshake is covered separately
by the plugin suite; this solo installer scenario does not mutate a completed
managed slot to add the extra.
No embedding engine, torch, model cache, GPU access, scheduler or production
service is needed. This is Linux/base-image proof, not native Windows/macOS,
GPU/model or production deployment proof. Every failed command/assertion is
captured and classified, then the scenario stops rather than continuing to
manufacture downstream failures.

The metadata-only session contract is asserted, not assumed. The allowed stamp
contains only `payload-dir`, `stamped-version`, a publication lock and a callable
binstub. Subsequent status reads may record only validated resolver boot-trace
metadata. Any venv/deployment/service/engine/autostart artifact fails the witness.
The first admitted operational invocation must perform actual provisioning;
there is no separate hand-install step to conceal a broken runtime gate.
Interpreter evidence requires an existing venv python beneath real, identity-
checked root/versions/slot directories, with no parent traversal or directory
symlinks. A genuine venv's final python symlink to its base runtime is allowed;
lexical containment or a nonexistent interpreter is not ownership evidence.
