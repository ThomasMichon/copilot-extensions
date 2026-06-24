"""Best-effort post-push HTTP notify.

A small, dependency-free helper used by the sync engine (target-independent
``sync.notify``) and the ``ingest`` target. After a successful push it POSTs a
JSON body ``{"machine": <machine>}`` to a configured URL so a downstream
consumer can crunch immediately. ``{machine}`` in the URL is also substituted
(back-compat with the ingest target's original notify shape).

Facility-neutral by design: the URL is whatever the operator configures -- a
processing service, an rsync-daemon sidecar, or a public webhook callback (e.g.
a Home Assistant webhook that relays to a private service). All errors are
swallowed; a notify failure never fails a sync.
"""

from __future__ import annotations

import json
import urllib.error
import urllib.request
from pathlib import Path


def post_notify(
    url: str,
    machine: str,
    *,
    bearer_token_file: str = "",
    timeout: int = 5,
) -> bool:
    """POST a best-effort notify. Returns True on a 2xx-ish send, else False.

    Never raises -- a notify is a courtesy ping, not a critical path. The body
    is ``{"machine": <machine>}``; ``{machine}`` in ``url`` is substituted too.
    """
    if not url:
        return False
    target = url.replace("{machine}", machine)
    body = json.dumps({"machine": machine}).encode("utf-8")
    try:
        req = urllib.request.Request(
            target,
            data=body,
            method="POST",
            headers={"Content-Type": "application/json"},
        )
        if bearer_token_file and Path(bearer_token_file).is_file():
            token = Path(bearer_token_file).read_text(encoding="utf-8").strip()
            if token:
                req.add_header("Authorization", f"Bearer {token}")
        urllib.request.urlopen(req, timeout=timeout)  # noqa: S310 (configured URL)
        return True
    except (urllib.error.URLError, OSError, ValueError):
        return False
