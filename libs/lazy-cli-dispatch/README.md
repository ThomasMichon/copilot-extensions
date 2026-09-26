# agent-lazy-cli-dispatch

Shared **two-phase lazy-dispatch** mechanism for an `agent-*` Copilot CLI
entry root whose monolithic `__main__.py` otherwise imports every CLI
submodule eagerly just to register argparse subparsers, regardless of which
single subcommand was actually requested. See the `agent-cli-lazy-dispatch`
effort for the full root-cause investigation (a hook-invoked command paying
that import cost on every invocation) and the decoupling work this factors
out of `agent-worktrees`, its first adopter.

```python
# __main__.py
from lazy_cli_dispatch import dispatch_lazy

_LAZY_DISPATCH_TABLE: dict[str, tuple[str, str]] = {
    "get": ("context_cli", "cmd_get"),
    "list": ("list_cli", "cmd_list"),
    # ... every fast-tracked subcommand
}

# Verified (per-module, runtime-exercised -- never just statically inferred)
# to need nothing from `_load_full_command_surface()`'s eager import block.
_CLUSTER_FREE_MODULES: frozenset[str] = frozenset({"context_cli"})


def main(argv: list[str] | None = None) -> int:
    args_list = argv if argv is not None else sys.argv[1:]
    if args_list and args_list[0] in _LAZY_DISPATCH_TABLE:
        return dispatch_lazy(
            args_list[0], args_list,
            dispatch_table=_LAZY_DISPATCH_TABLE,
            package=__package__,
            prog="my-cli",
            ensure_cluster_loaded=_ensure_cluster_loaded,
            cluster_free_modules=_CLUSTER_FREE_MODULES,
        )
    # ... existing eager build_parser() fallback, unchanged
```

`dispatch_lazy` imports and registers only the ONE module owning the
requested command's parser and handler -- never the caller's own full eager
surface -- unless that module isn't yet verified `cluster_free`, in which
case it calls `ensure_cluster_loaded()` first (the caller's own "run my
eager import block, if not already done" entry point). The resulting
parser/help text for that one subcommand is byte-identical to what the
eager full build would have produced for it, since it comes from the same
module.

## The cross-module reach-back hazard, and its two small fixes

A CLI submodule frequently reaches back into its own entry root for a
helper another **sibling** submodule actually owns (via a shared `_core()`
accessor), rather than importing that sibling directly. If the entry root
hasn't finished its eager import block yet (skipped by the fast path
above), that reach-back can raise `AttributeError`/`NameError` for a name
only bound during the eager block -- a live regression this exact class
shipped more than once in `agent-worktrees` (see the effort's Stage A-D
Journal) before every module was fully decoupled.

```python
# a CLI submodule (e.g. handoff_cli.py) needing a name owned by a sibling
from lazy_cli_dispatch import core_helper
from . import resolve_launch_cli

def _core():
    from . import __main__ as core
    return core

def _apply_assignment_env(*args, **kwargs):
    return core_helper(_core(), "_apply_assignment_env", resolve_launch_cli._apply_assignment_env)(*args, **kwargs)
```

```python
# an entry-root-native function shared across submodules (in __main__.py
# itself), bare-referencing a name only bound by the eager block
from lazy_cli_dispatch import self_override

def some_shared_function():
    from . import status_monitor_runtime as _smr
    _aw_runtime_home = self_override(globals(), "_aw_runtime_home", _smr._aw_runtime_home)
    ...
```

Both helpers prefer an already-bound/monkeypatched name over the direct
import, so the decoupling refactor itself never silently defeats a test
that does `monkeypatch.setattr(m, name, fake)` on the entry root or the
submodule's own accessor -- a live regression this pattern also shipped
once before this shared helper existed (see the effort's Stage D Journal).

## Not in scope here

The AST-based `_core()`-cluster classifier each adopting plugin's own
`tests/_core_cluster_scan.py` uses to compute/verify its own
`_CLUSTER_FREE_MODULES` set stays plugin-local -- it walks each plugin's own
`__main__.py` AST shape, which isn't a generic cross-plugin mechanism.
