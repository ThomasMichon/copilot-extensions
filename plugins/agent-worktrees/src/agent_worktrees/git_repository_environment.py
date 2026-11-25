"""Existing Git identity isolation without registry or credential imports."""

from __future__ import annotations

import os
import shlex
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from io import StringIO

from . import env_scrub

_identity_scoped = ContextVar("git_repository_identity_scoped", default=False)
_credential_config_scoped = ContextVar("git_credential_config_scoped", default=False)


def identity_scoped() -> bool:
    return _identity_scoped.get()


def credential_config_scoped() -> bool:
    return _credential_config_scoped.get()


@contextmanager
def repository_identity_scope(*, preserve_credentials: bool = False) -> Iterator[None]:
    """Keep nested Git helpers on their supplied cwd without global env mutation."""
    token = _identity_scoped.set(True)
    credential_token = _credential_config_scoped.set(
        preserve_credentials or _credential_config_scoped.get(),
    )
    try:
        yield
    finally:
        _credential_config_scoped.reset(credential_token)
        _identity_scoped.reset(token)


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


def _credential_key(key: str) -> bool:
    lowered = key.lower()
    return lowered.startswith(("credential.", "http.")) or lowered in {
        "core.sshcommand", "core.gitproxy",
    }


def _credential_parameters(value: str) -> str:
    stream = StringIO(value)
    lexer = shlex.shlex(stream, posix=True)
    lexer.whitespace_split = True
    lexer.commenters = ""
    selected: list[str] = []
    while True:
        start = stream.tell()
        item = lexer.get_token()
        if item is None:
            break
        if _credential_key(item.partition("=")[0]):
            selected.append(value[start:stream.tell()].strip())
    return " ".join(selected)


def _credential_configuration(environment: dict[str, str]) -> dict[str, str]:
    original = (
        {key.upper(): value for key, value in environment.items()}
        if os.name == "nt" else environment
    )
    preserved: dict[str, str] = {}
    try:
        # Git treats an empty count as no entries; mirror that instead of failing.
        count = int(original.get("GIT_CONFIG_COUNT") or "0")
        if count < 0:
            raise ValueError
        selected: list[tuple[str, str]] = []
        for index in range(count):
            key = original[f"GIT_CONFIG_KEY_{index}"]
            value = original[f"GIT_CONFIG_VALUE_{index}"]
            if _credential_key(key):
                selected.append((key, value))
        if selected:
            preserved["GIT_CONFIG_COUNT"] = str(len(selected))
            for index, (key, value) in enumerate(selected):
                preserved[f"GIT_CONFIG_KEY_{index}"] = key
                preserved[f"GIT_CONFIG_VALUE_{index}"] = value
        parameters = _credential_parameters(original.get("GIT_CONFIG_PARAMETERS", ""))
        if parameters:
            preserved["GIT_CONFIG_PARAMETERS"] = parameters
    except (ValueError, KeyError):
        raise ValueError("invalid command-scoped Git credential configuration") from None
    return preserved


def repository_identity_env(
    environment: dict[str, str] | None = None, *, preserve_credentials: bool = False,
) -> dict[str, str]:
    """Remove inherited Git context; preserve unrelated environment values."""
    env = env_scrub.scrub_python_runtime_env(dict(os.environ if environment is None else environment))
    credentials = _credential_configuration(env) if preserve_credentials else {}
    for name in list(env):
        upper = name.upper()
        if (
            upper in _REPOSITORY_CONTEXT_ENV
            or upper.startswith("GIT_CONFIG_KEY_")
            or upper.startswith("GIT_CONFIG_VALUE_")
        ):
            env.pop(name, None)
    env.update(credentials)
    env["GIT_TERMINAL_PROMPT"] = "0"
    return env
