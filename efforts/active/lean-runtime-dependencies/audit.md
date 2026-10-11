# Dependency Audit Motivation

[Effort and completion gate](README.md).

## Snapshot and method

Source audit: `dev` commit `d68014f65363b682d5a96cb244a19d87163793fc`,
2026-10-11. Canonical TOML manifests, optional extras, installer selections and
actual production API use were inspected; checked-in vendored copies and
development/test requirements were not counted as independent runtime consumers.
Execution must refresh evidence against its own snapshot.

There are 36 canonical pyprojects, 36 external base requirement occurrences,
16 non-development optional occurrences and 25 distinct external packages.
Declaration frequency is not installation count or dependency closure.

## Widely repeated declarations

| Package | Canonical surfaces | Assessment |
|---------|--------------------|------------|
| PyYAML | 15 | Genuine public YAML configuration/frontmatter; small footprint |
| FastAPI / Uvicorn | 4 each | Substantive server/lifecycle contracts |
| Pydantic | 3 | Additional transitive consumers have real validation use |
| Textual | 2 | Real UI in Worktree Manager; unused in agent-worktrees |
| httpx / MCP / cryptography | 2 each | Real capabilities; profile placement matters |

Internal shared-helper consumer counts: procutil 14; drop-in registry and ZDD
10 each; plugin resolution 8; activation and single-instance lease 7 each.
Wide use alone is not evidence of unnecessary code.

## Highest-cost direct and transitive packages

Representative public compressed wheels, CPython 3.11, MiB (2^20 bytes):

| Package/version | Windows x64 | Linux x64 | Direct declaration |
|-----------------|-------------|-----------|--------------------|
| torch 2.14.1 | 118.3 | 528.9 | One embedding engine |
| lancedb 0.40.0 | 79.5 | 71.7 | One index store extra |
| pyarrow 26.0.0 | 27.2 | 51.4 | One index store extra |
| scipy 1.17.1 | 34.9 | 33.7 | Transitive ML stack |
| numpy 2.4.6 | 12.0 | 16.1 | Store; also directly imported by engine |
| transformers 4.57.6 | 11.4 | 11.4 | One embedding engine |
| scikit-learn 1.9.1 | 7.9 | 8.9 | Transitive ML stack |
| sentence-transformers 5.7.0 | 0.58 | 0.58 | One engine; large transitive closure |

Four Torch-declared NVIDIA wheels alone total approximately 953 MiB compressed
on Linux, excluding remaining accelerator dependencies and models. Neither this
nor the table is a complete resolved fresh-install total.
Source: public PyPI release JSON, e.g.
`https://pypi.org/pypi/torch/2.14.1/json`.

An existing-runtime RECORD inventory measured 924.7 MiB logical package files
across 32 retained environments; pip/setuptools represented 600.1 MiB (65%).
This includes historical versions and hardlinked files and does not imply
physical reclaimable storage. The model stack was not installed in that
inventory. Existing versions differ from the audited source snapshot.

## Actionable evidence

Paths and line numbers refer to the audited source snapshot:

1. **Unused UI dependency:** `plugins/agent-worktrees/pyproject.toml:17`.
   `src/agent_worktrees/manager_launch_cli.py:542-563` dispatches to the real
   Manager process. Existing Textual/Rich/Pygments/markdown-it packages account
   for at least 8.6 MiB logical files per worktrees environment.
2. **Duplicate index profiles:** `plugins/agent-index/scripts/install.sh:1177-1179`
   and `1447-1467` install `[store,server]` in primary and sibling environments.
   The fallback primary launch must be replaced before removing its dependencies.
3. **MCP placement:** `plugins/agent-index/pyproject.toml:70-77`;
   `src/agent_index/mcp_app.py:25,45-68` is a remote HTTP client, not store code.
4. **Optional SSH installed by default:** `plugins/agent-containers/scripts/init.sh:562-573`
   and `init.ps1:804-826` always attempt `[provider-exec]`. Paramiko implements
   server-side restricted SSH over stdio; subprocess OpenSSH is not equivalent.
5. **CPU comments without wheel enforcement:** `plugins/agent-index/scripts/install.sh:1642-1676`.
   The default Torch feed is not explicitly CPU-only; subsequent unversioned
   `--no-deps` replacement assumes compatibility.
6. **Overlarge statistics execution:** `plugins/agent-index/src/agent_index/store/content_store.py:988-1007`
   materializes all columns in pandas to count two metadata fields.
7. **Syntax extras:** `plugins/agent-index/src/agent_index/chunking/code.py:57-116`
   already lazily loads grammars and supports fallback chunking.
8. **Existing stdlib-only seam:** customizing-copilot's activation helper loads
   `state.py` directly to avoid package-level YAML dependencies.

## Keep unless a separately reviewed replacement proves parity

Do not build homemade YAML, crypto, WebSocket or ACP implementations.
PyYAML handles real public config contracts. FastAPI/Pydantic and pooled/async
httpx serve meaningful contracts; Uvicorn already avoids its `standard` extra.
Arrow/LanceDB support persistent schemas, vector/lexical queries, maintenance
and concurrent readers/writers. The engine's transformer inference is genuine,
not replaceable by a small helper.

ONNX inference and SQLite/NumPy storage are exploratory backend alternatives,
requiring retrieval quality, performance and migration evidence. They are not
prerequisites for the low-risk dependency reductions.
