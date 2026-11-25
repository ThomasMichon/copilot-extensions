"""Select the configuration-owner read before loading lifecycle commands."""

from __future__ import annotations

from collections.abc import Callable


def select_configuration_read(argv: list[str]) -> Callable[[], int] | None:
    if (
        len(argv) == 3 and argv[0] == "state-root"
        and set(argv[1:]) == {"--configuration", "--json"}
    ):
        return _run
    return None


def _run() -> int:
    import json
    import os

    from .configuration_resident import try_read

    report = try_read(os.getcwd())
    if report is not None:
        from .output import ensure_utf8_stdio

        ensure_utf8_stdio()
        print(json.dumps(report, ensure_ascii=False))
        return 0

    from . import config as cfg, output
    from .config_sources import emit_configuration_report
    from .record_config import metadata_project

    output.ensure_utf8_stdio()
    project = metadata_project(None)
    if not project:
        from .front_door_cli import cmd_help_unrouted

        return cmd_help_unrouted(requested="state-root")
    cfg.set_active_project(project)
    config = cfg.load_configuration_identity()
    return emit_configuration_report(config, cwd=os.getcwd())
