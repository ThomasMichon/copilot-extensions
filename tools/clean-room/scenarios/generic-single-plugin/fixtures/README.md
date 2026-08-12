# generic-single-plugin fixtures

Optional seed files for this scenario. Kept minimal on purpose.

## uv-index (opt-in, runtime, not a file)

The scenario's **uv-index fixture** is applied from the environment, not a seed
file, so it mirrors the runner's npm build-arg exactly:

- Supply an internal index with `-UvIndex <url>` (`--uv-index` on `run.sh`, or
  `$env:CR_UV_INDEX`). The runner forwards it into the container as
  `CR_UV_INDEX`, and `scenario.sh` applies it at the deploy stage (phase 3) by
  exporting `UV_INDEX_URL` / `UV_DEFAULT_INDEX` / `UV_EXTRA_INDEX_URL` and
  writing `~/.config/uv/uv.toml`.
- **Default: unset.** With no fixture, uv keeps its public PyPI index — which is
  TLS-blocked on a governed box — so the deploy stage fails and the scenario
  classifies it as a `toolchain-uv` jam. That surfacing is the point (design
  Sec.3 / Sec.7): the fixture is the *unjam*, opt-in only.

The derived value is the host's `pip config get global.index-url`
(`…/pypi/simple/`) — the governed policy configures pip but not uv.
