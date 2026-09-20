"""Lazy compatibility boundary to the active agent-worktrees runtime.

Unlike this package's other ``_target = engine_module(...)`` compatibility
shims, ``update_stage`` backs a non-critical, cosmetic feature (the picker's
version-update indicator glyph) that every picker session imports at module
load (``engine.py`` reads ``indicator_state`` at import time) but only ever
*calls* once first paint has already happened. Resolving ``engine_module``
eagerly here would pay its import cost (the engine's ``config``/
``project_state`` modules, pulled in transitively) on every picker start
regardless of whether the indicator is ever displayed before the operator
moves on. Deferred to the first actual attribute access instead
(picker-startup-latency follow-up) -- ``engine.py`` in turn imports this
module itself (not the ``indicator_state`` name directly) so binding the
import doesn't force the resolution either; only calling
``update_stage.indicator_state()`` does.
"""
_target = None


def _resolved():
    global _target
    if _target is None:
        from ._engine_runtime import engine_module

        _target = engine_module("update_stage")
    return _target


def __getattr__(name):
    return getattr(_resolved(), name)
