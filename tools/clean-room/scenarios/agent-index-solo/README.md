# Released Index solo installer witness

This Tier-P/F1 scenario installs only the released `agent-index` payload.
It clones the public marketplace's `main` inside the disposable container,
optionally fetches an exact released commit (`CR_RELEASE_REF`), and checks
installed installer bytes and both vendored installer-engine files against
that release. `CR_EXPECT_VERSION` optionally pins the plugin version. Neither
the development checkout nor a host runtime is mounted as the product.
For an unshipped candidate, pass a **materialized public release-form directory**
with `--harness-mount`, `--pass-env CR_CANDIDATE_HEAD` and the exact source HEAD.
The scenario copies it into its own filesystem and records
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

## Documented contract replacements

| Previous expectation | Current released contract and preserved coverage |
|---|---|
| No config reports `setup_required` | Non-opted-in repositories report `inactive`; no runtime or service may appear. A canonical corpus-only repo config then reports `setup_required`/`absent`. |
| Session start deploys or stamps a runtime | Non-opted-in startup remains inactive/dormant. Opted-in startup may stamp only metadata/binstubs; `setup_required`/`stamped` is not runtime readiness. No venv, deployment manifest, service, engine or autostart may appear. |
| Global binstub on login PATH | The installed payload's documented `bin/agent-index` is invoked by exact path on stock login PATH. No PATH injection or expectation of machine-global command ownership. |
| Copy system Python and forge completion | A materialized payload snapshot with genuine provenance precedes the shipped owned-slot primitive; `uv venv` makes a real incomplete venv with no Index import, completion or selected marker. Status must report `setup_required`/`broken`, never ready or absent. First-use setup must repair/build it through the actual cell installer. |
| Role from legacy machine config | Canonical repo designations prove client/host roles before runtime creation. Corpus-only config proves no-role setup refusal. This does not claim a remote client's transport or installed client profile. |
| Complete/no-role reports ready runtime | The current immutable host dependency profile rejects no-role dispatch with exit 126 and the specific dependency-profile mismatch. The genuine completion remains intact; no fake receipt is accepted as deployment evidence. |
| Legacy `ensure` starts the host | Explicit OS-profile installation-mode opt-in and generation-pinned activation precede first-use setup, which must reach the real runtime gate's cell installer. Default policy retains legacy mode without activation. Existing legacy metadata is truthfully retained inert, not declared absent. No authorization override or legacy-probe bypass. |
| Endpoint exists, or health returns anything | The real venv must import from its slot and report the exact released version. `/health` and `/status` must identify that installed host profile, one live owned instance, PID and token; health must be promoted, non-passive and `ok`. |

The direct MCP check registers the five shipped tools but never invokes reindex.
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
