from ._engine_runtime import engine_module

_target = engine_module("pr_ops")


def __getattr__(name):
    return getattr(_target, name)
