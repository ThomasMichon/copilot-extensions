"""Build-time native support locator; no interpreter is needed by its consumers."""
from __future__ import annotations

from importlib.resources import files


def native_support_files() -> dict[str, bytes]:
    """Return a relocatable closure from the installed, caller-selected provider.

    Generators materialize these exact named files together in their owned
    versioned assets directory and embed absolute helper paths in their stubs.
    This lookup never searches plugin installations or provisions a runtime.
    """
    native = files("process_telemetry").joinpath("native")
    procutil = files("agent_procutil")
    return {
        **{name: native.joinpath(name).read_bytes() for name in (
            "command-errors.sh", "command-errors.ps1", "command-causes.tsv",
        )},
        **{name.replace("_", "-"): procutil.joinpath(name).read_bytes() for name in (
            "private_paths.sh", "private_paths.ps1",
        )},
    }
