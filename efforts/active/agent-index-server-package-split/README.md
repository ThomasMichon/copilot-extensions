# agent-index — Server as a Separate, Independently-Installable Program

- **Slug:** `agent-index-server-package-split`
- **Repo:** copilot-extensions (plugin home; direct-push `main`)
- **Created:** 2026-09-27
- **Status:** Active
- **Vision:** extends [`visions/plugins/agent-index`](../../../visions/plugins/agent-index/README.md)
  (§*The embedding engine*, §warm-durable-engine) — refines the durable engine
  runtime built by `efforts/active/agent-index-engine-daemon` from an extra
  (`agent-index[engine]`) of the same distribution into a genuinely separate
  Python **program**.

## Guiding Intent

`agent-index-engine-daemon` already made the embedding engine a durable,
persistent daemon in its own venv, decoupled from the versioned service
runtime. What it did **not** do is decouple the *packaging*: the durable venv
installed `agent-index[engine]` — the very same distribution as the client and
service, just with extra dependencies. This effort finishes the separation:
the server becomes its **own installable "program"** — a separate Python
package (`agent-index-engine`) with its own `pyproject.toml`, own version, and
own dependency footprint — that the `agent-index` client wrapper still talks
to exactly as before (over the existing `engine.client.EngineClient` HTTP
client), but which installs **independently**, only when a machine's role is
explicitly configured as `host`.

## Context

`efforts/active/agent-index-engine-daemon` shipped the durable, persistent
engine daemon (a separate venv, outside the versioned service runtime) and the
dependency split into a light service and a heavy `[engine]` extra of the
*same* `agent-index` distribution. That was sufficient for the daemon's own
lifecycle goals (never rebuilt by a routine service update), but it left the
server as an extra of the client's own package rather than a truly separate
program — the durable venv installs `agent-index[store,engine]`, the identical
distribution a client installs, just with more extras. The operator asked for
the next step: make the server its own installable Python program, so it can
be versioned, tested, and reasoned about independently of the client wrapper
that talks to it.

## Request

Operator-directed: "separate agent-index's server into its own venv, as a
separate 'program' within the agent-index plugin. The `agent-index` client
wrapper will still wrap talking to it, but it will install independently,
only based on being configured to do so."

## Design

- **New package:** `plugins/agent-index/server/` — distribution
  `agent-index-engine`, module `agent_index_engine`. Depends on the base
  `agent-index` package (for `IndexConfig`, `engine.generation.
  current_engine_generation`, `capability.effective_device` — the shared,
  torch-free identity/config surface both programs read) plus `torch`,
  `transformers`, `sentence-transformers`.
- **What moved** (base → new package): `engine/app.py` → `agent_index_engine/
  app.py`; `embedding/pipeline.py` → `agent_index_engine/pipeline.py`;
  `embedding/query_embedder.py` → `agent_index_engine/query_embedder.py`. The
  base `embedding/` package is gone entirely.
- **What stayed in base `agent-index`** (all torch-free, so they load in the
  light client/service venv without the heavy stack installed):
  `engine/client.py` (`EngineClient`/`EngineUnavailableError` — the client
  wrapper), `engine/daemon.py` (durable-daemon start/stop/status/health;
  `agent-index engine {start,stop,status,run}` CLI), `engine/lifecycle.py`
  (`ensure_engine`/`stop_engine` for the `subprocess` engine_mode),
  `engine/generation.py` (the tiny shared generation-identity constant),
  `engine/shim.py` (an unreferenced, pre-existing, torch-free container-shim
  prototype — left in place, out of scope for this change).
- **Module-path-only changes** in the modules that stayed: `daemon.py`'s
  `engine_command()` and `lifecycle.py`'s `_spawn_engine()` now spawn
  `-m agent_index_engine.app` instead of `-m agent_index.engine.app`; the durable
  venv (or, for `subprocess` mode, the single opted-in service venv) must have
  `agent-index-engine` installed for that to resolve.
- **`search/engine.py`:** the `InProcessQueryEmbedder` import (used only when
  `AGENT_INDEX_SEARCH_IN_PROCESS=1`, an opt-in single-venv install) is now
  conditional on `config.search_in_process`, with a clear `RuntimeError` if
  `agent-index-engine` isn't installed in that venv — previously this import
  ran unconditionally at import time, which would have broken the (now
  default) `external`/daemon-routed path the moment `embedding/query_embedder.py`
  left the base package.
- **Installer (`install.{ps1,sh}`):** `Install-Engine`/`_install_engine` now
  installs the base `agent-index` package into the durable venv first (needed
  for its CLI + `index_config`/`daemon`/`generation` — mirrors the existing
  vendored-`agent-zdd` pre-install pattern, since neither package is on PyPI),
  then installs `agent-index-engine` from `plugins/agent-index/server`
  (pulling torch/transformers/sentence-transformers) — replacing the old
  single-step `agent-index[store,engine]` install. This also **drops the
  `store` extra from the durable engine venv** (lancedb/tree-sitter were never
  needed there — the daemon CLI only touches `engine/daemon.py`), shrinking its
  footprint.

## Plan

- [x] Design the split (this document).
- [x] Create the `agent-index-engine` package (`plugins/agent-index/server/`):
      `pyproject.toml`, `app.py`, `pipeline.py`, `query_embedder.py`.
- [x] Remove the moved modules and the now-empty `embedding/` package from base
      `agent-index`; drop the `engine` extra from its `pyproject.toml`.
- [x] Repoint `daemon.py`/`lifecycle.py`'s spawn commands at
      `agent_index_engine.app`; make `search/engine.py`'s in-process import
      conditional + fail loud with actionable guidance.
- [x] Update `install.ps1`/`install.sh` `Install-Engine`/`_install_engine` to
      install `agent-index` then `agent-index-engine` into the durable venv
      (dropping the unneeded `store` extra there).
- [x] Update `test_engine_daemon.py`'s spawn-command assertions.
- [x] Base test suite green (577 passed, 57 skipped — pre-existing GPU/platform
      skips — in a fresh venv built from the edited `pyproject.toml`).
- [ ] **Live-validate** on a real host: durable-venv provisioning installs both
      packages cleanly, `agent-index engine start` brings up the daemon from
      the new module path, and a torch-free service embeds a query through it
      (mirrors the Validation Plan `agent-index-engine-daemon` already proved
      for the single-distribution shape).
- [ ] Decide whether `agent-index-engine` should get its own version-bump
      convention/CI entry (today it free-rides on agent-index's plugin
      version bump tooling, which doesn't know about a second pyproject.toml
      under one plugin).
- [ ] Docs: extend `docs/patterns/durable-vs-versioned-runtime.md` with a note
      that "durable" now also means "separately packaged", once live-validated.

## Validation Plan

- [ ] A durable-venv `engine`/`engine-update` install action provisions
      **both** `agent-index` and `agent-index-engine` and `agent-index engine
      start` successfully launches `agent_index_engine.app` from that venv.
- [ ] A torch-free service (no `agent-index-engine` installed) still serves
      search/index normally against the external daemon (default path,
      unaffected by this change).
- [ ] `AGENT_INDEX_SEARCH_IN_PROCESS=1` on a venv that does **not** have
      `agent-index-engine` installed raises the new, actionable `RuntimeError`
      rather than an import crash or a silent fallback.

## Journal

- **2026-09-27** — Designed and implemented the split: created the
  `agent-index-engine` package (`plugins/agent-index/server/`), moved
  `engine/app.py` + `embedding/{pipeline,query_embedder}.py` into it, repointed
  `daemon.py`/`lifecycle.py`'s spawn commands, made `search/engine.py`'s
  in-process import conditional, updated both installer scripts' `Install-
  Engine`/`_install_engine`, updated `test_engine_daemon.py`. Base test suite
  green (577 passed / 57 skipped) in a fresh `uv`-built venv from the edited
  `pyproject.toml`. Live-validation on a real host (durable venv provisioning
  both packages, daemon start from the new module path) and the docs/version-
  bump-tooling follow-ups remain open.

