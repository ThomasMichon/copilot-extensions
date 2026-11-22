"""Keep explicit Git checkout operations independent of the caller's repository."""
from collections.abc import Mapping


_SELECTORS = {
    "GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
    "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES", "GIT_PREFIX",
    "GIT_CEILING_DIRECTORIES", "GIT_DISCOVERY_ACROSS_FILESYSTEM",
    "GIT_SHALLOW_FILE", "GIT_GRAFT_FILE", "GIT_REPLACE_REF_BASE", "GIT_CONFIG",
    "GIT_NAMESPACE", "GIT_QUARANTINE_PATH",
}


def checkout_environment(environment: Mapping[str, str]) -> dict[str, str]:
    return {key: value for key, value in environment.items() if key.upper() not in _SELECTORS}
