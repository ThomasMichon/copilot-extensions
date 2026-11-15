"""Side-effect-free relay protocol probe construction."""

from __future__ import annotations

import shlex


def build_relay_ping_probe_command(relay_port: int) -> str:
    """Build a bounded POSIX command requiring the relay's exact pong reply.

    The ping action does not query credentials or authorize their delivery.
    A successful round trip proves protocol reachability, not credential scope.
    """
    script = (
        f"exec 3<>/dev/tcp/127.0.0.1/{relay_port} && "
        'printf "ping\\n\\n" >&3 && '
        "IFS= read -r -t 3 reply <&3 && "
        '[ "$reply" = "pong" ]'
    )
    return f"timeout 3 bash -c {shlex.quote(script)} && echo OK"
