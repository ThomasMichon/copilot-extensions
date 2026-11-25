"""Ordinary application front door for the independent Driver core."""

import sys

from agent_remote_driver.session_host.launcher import main as host_main


def main(argv: list[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments[:1] == ["service"]:
        from .service_commands import main as service_main

        return service_main(arguments[1:])
    if arguments[:1] == ["__source-start"]:
        if len(arguments) != 1:
            raise ValueError("Driver service startup accepts no application arguments")
        import time
        from .connector import install, publish_startup
        from .service_lifecycle import selected_source, start

        deadline = time.monotonic() + 60
        binding = selected_source(deadline)
        install(binding, deadline=deadline)
        publish_startup(binding, binding.root / "connector-marketplace")
        start(binding, deadline=deadline)
        return 0
    from .service_lifecycle import retain_host_runtime

    with retain_host_runtime():
        return host_main(arguments)

__all__ = ["main"]
