"""endpoint-rendezvous: discoverable, collision-free local-endpoint files.

See :mod:`endpoint_rendezvous.rendezvous` for the full docstring. This package
gives a service-bearing plugin a shared, dependency-free way to *advertise* the
endpoint it bound and to *resolve* a service's endpoint on the client side with a
backwards-compatible cutover fallback ladder.
"""

from __future__ import annotations

from .rendezvous import (
    SCHEMA,
    Endpoint,
    EndpointUnavailable,
    clear_endpoint,
    connect_probe,
    default_runtime_dir,
    endpoint_file,
    is_stale,
    pid_alive,
    read_endpoint,
    resolve,
    utc_now_iso,
    write_endpoint,
)

__all__ = [
    "SCHEMA",
    "Endpoint",
    "EndpointUnavailable",
    "clear_endpoint",
    "connect_probe",
    "default_runtime_dir",
    "endpoint_file",
    "is_stale",
    "pid_alive",
    "read_endpoint",
    "resolve",
    "utc_now_iso",
    "write_endpoint",
]
