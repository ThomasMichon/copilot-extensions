"""Compatibility exports for shared installer capability probes."""

from update_telemetry.capabilities import (
    HUMAN_UTF8_ENV,
    posix_zero_downtime_flag_supported,
    powershell_file_target,
    powershell_human_utf8_supported,
)

__all__ = [
    "HUMAN_UTF8_ENV",
    "posix_zero_downtime_flag_supported",
    "powershell_file_target",
    "powershell_human_utf8_supported",
]
