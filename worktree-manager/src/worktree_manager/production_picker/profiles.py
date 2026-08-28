from ._engine_runtime import engine_module

_target = engine_module("profiles")


def __getattr__(name):
    return getattr(_target, name)
