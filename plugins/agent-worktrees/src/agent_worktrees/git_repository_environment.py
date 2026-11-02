"""Existing Git identity isolation without registry or credential imports."""

from __future__ import annotations

import os

from . import env_scrub

_NO_HOOKS_PATH = "/dev/null"
_REPOSITORY_CONTEXT_ENV = frozenset({
    "GIT_ALTERNATE_OBJECT_DIRECTORIES",
    "GIT_CEILING_DIRECTORIES",
    "GIT_COMMON_DIR",
    "GIT_CONFIG",
    "GIT_CONFIG_COUNT",
    "GIT_CONFIG_PARAMETERS",
    "GIT_DIR",
    "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_GRAFT_FILE",
    "GIT_IMPLICIT_WORK_TREE",
    "GIT_INDEX_FILE",
    "GIT_INTERNAL_SUPER_PREFIX",
    "GIT_NAMESPACE",
    "GIT_NO_REPLACE_OBJECTS",
    "GIT_OBJECT_DIRECTORY",
    "GIT_PREFIX",
    "GIT_QUARANTINE_PATH",
    "GIT_REPLACE_REF_BASE",
    "GIT_SHALLOW_FILE",
    "GIT_WORK_TREE",
})


def repository_identity_env(environment: dict[str, str] | None = None) -> dict[str, str]:
    """Remove inherited Git context; preserve unrelated environment values."""
    env = env_scrub.scrub_python_runtime_env(dict(os.environ if environment is None else environment))
    for name in list(env):
        upper = name.upper()
        if (
            upper in _REPOSITORY_CONTEXT_ENV
            or upper.startswith("GIT_CONFIG_KEY_")
            or upper.startswith("GIT_CONFIG_VALUE_")
        ):
            env.pop(name, None)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env
