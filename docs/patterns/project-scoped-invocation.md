# Pattern: project-scoped-invocation

**Serves:** *Vision agent-fabric* §Features/`address-any-project`,
§Behaviors/`project-addressed-not-cwd-bound`.
**Exemplars:** agent-worktrees (the `--project` global + the per-project
`<repo>` binstub), agent-dispatch (the embody supervisor spawning
`agent-worktrees --project <repo> embody …`).

## Problem

The agent-* CLIs resolve *which project they act on* the way `git` does — from
the **current working directory**. Standing inside a managed repo or worktree,
you type `agent-worktrees finalize` and it just works. This git-like discovery is
excellent **for a human standing in a repo**, and it is the right default.

It has two failure modes the suite has now hit for real:

- **CWD-neutral callers have no project to discover.** A long-lived **service or
  daemon** does not run inside a repo — its working directory is its own runtime
  dir (`~/.agent-dispatch/`, a systemd `WorkingDirectory=`, a container workdir).
  So a caller that must act on a *specific* project has nothing to discover from.
  The concrete seam: the **agent-dispatch embody supervisor** (a systemd user
  service) pulls a queued task whose lane is some repo and tries to spawn
  `agent-worktrees embody` for it — and dies with

  ```
  Could not resolve a project for 'embody'. Context is discovered from the
  current directory (like git), but this directory is not inside an adopted …
  ```

  because the service's CWD is not any repo. The same wall stands in front of any
  cross-project script, cron job, or reviewer/producer daemon.

- **The per-project entry point fronts only one layer.** The per-project binstub
  `<repo>` (e.g. `aperture-labs`) is generated to run `agent-worktrees --project
  <repo> …`. It is a great "act on *this* repo" shortcut — but it is welded to
  **one** layer. There is no symmetric way to scope *another* layer
  (coordination, delegation, a venue provider, the vault) to a project without
  re-deriving the repo dir and `cd`-ing there. A caller ends up with a different
  convention per tool.

## Standard approach

**A project is an explicit, first-class address — not only an implied CWD — and
one per-project entry point reaches every layer.**

1. **Every layer accepts `--project <name>`, resolving identically to CWD
   discovery.** Explicit naming and git-like discovery must produce the *same*
   active project; `--project` simply removes the "must stand inside it"
   precondition. Precedence is explicit `--project` → CWD discovery → documented
   error. (agent-worktrees already does this: `agent-worktrees --project
   aperture-labs embody …` works from any directory.)

2. **The per-project `<repo>` binstub is a uniform namespace dispatcher.** Its
   first token selects the layer:

   ```
   <repo> <layer> <args…>   →   agent-<layer> --project <repo> <args…>
   ```

   so a single muscle-memory shape reaches the whole stack:

   | Invocation | Runs |
   |------------|------|
   | `aperture-labs worktrees finalize` | `agent-worktrees --project aperture-labs finalize` |
   | `aperture-labs bridge send …`      | `agent-bridge     --project aperture-labs send …` |
   | `aperture-labs dispatch list`      | `agent-dispatch   --project aperture-labs list` |
   | `aperture-labs codespaces ssh`     | `agent-codespaces --project aperture-labs ssh` |
   | `aperture-labs vault cache-populate` | `agent-vault    --project aperture-labs cache-populate` |

   **Backward-compatible:** when the first token is **not** a known layer
   namespace, the binstub falls back to today's behavior —
   `agent-worktrees --project <repo> <args…>` — so `aperture-labs finalize`
   keeps working unchanged. The layer-namespace set is a small **reserved word
   list** (`worktrees`, `bridge`, `dispatch`, `codespaces`, `containers`,
   `vault`, `logger`, …); a first token in that set dispatches, anything else is
   an agent-worktrees subcommand.

3. **CWD-neutral callers name the project; they never `cd` to be understood.** A
   service, daemon, or cross-project script passes `--project <name>` (or invokes
   `<repo> <layer> …`) rather than changing directory or shelling out to resolve
   a repo path. Relying on CWD from a service is the anti-pattern this pattern
   exists to remove.

## Invariants

- **Same result, two paths.** `--project X` from anywhere and being CWD-anchored
  inside `X` resolve to the identical active project. `--project` is not a
  second, weaker mode — it is the CWD-independent spelling of the same resolution.
- **Explicit wins.** An explicit `--project` overrides CWD discovery; a tool
  never silently prefers the ambient directory over a named project.
- **No ambient env var for project identity.** The project is carried as an
  argument (`--project`) or by the per-project binstub that supplies it — **not**
  smuggled through a mutated session environment variable (which would leak into
  child processes and the interactive shell). The existing binstubs already avoid
  this; keep it that way. (A scoped, restored env var is acceptable only in a
  narrow recovery fallback, as the current binstubs do for `WORKTREE_PROJECT`
  when the venv is missing.)
- **Dispatch is back-compat by construction.** Adding the namespace dispatch to
  `<repo>` must not change the meaning of any existing `<repo> <agent-worktrees
  subcommand>` invocation. The reserved layer-name set is the only thing that
  routes elsewhere.

## Why it serves the vision

`address-any-project` states that every layer is reachable against a named
project through one `<repo> <layer> …` shape; `project-addressed-not-cwd-bound`
states that a neutral working directory is never a barrier. This pattern is the
**how**: a uniform `--project` on every layer plus a dispatching per-project
binstub. It is the addressing-plane complement of `uniform-venue-reach` — that
one keeps *where* an agent runs from changing how it is reached; this one keeps
*which project* a layer acts on nameable without standing in it.

## Status / realization

- **Realized (first slice):** agent-worktrees `--project` is long-standing; the
  agent-dispatch embody supervisor now spawns `agent-worktrees --project <repo>
  embody …`, deriving the project from the task's lane — the fix that motivated
  this pattern.
- **Target (not yet built):** the per-project `<repo>` binstub as a full
  namespace dispatcher across the agent-* fleet (a change to the binstub
  generator, with Windows `.ps1`/`.cmd` parity and the reserved-word set). Tracked
  as a follow-on; this guide is the design it will realize.
