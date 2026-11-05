"""zdd -- zero-downtime active/passive cutover primitives.

A service-neutral library for zero-downtime redeploys, extracted from
agent-bridge so any Copilot CLI plugin or multi-machine service can reuse it:

- ``routing`` -- a file-based client-read routing table (``active.json``):
  publish the live endpoint, atomically flip active/previous on cutover, and
  let short-lived clients resolve (and self-heal to ``previous`` or a config
  fallback) without a long-lived front proxy.
- ``cutover`` -- ``CutoverOrchestrator``: stand a new daemon up beside the old
  on a fresh port, health-gate it, flip the routing table, drain the old, then
  retire it -- with a reversible sequence, rollback, and commit-forward. All
  side-effecting collaborators are injected, so a consumer supplies its own
  spawn / health-probe / HTTP-client / free-port functions and drain semantics.

The library carries no service-specific logic; see ``cutover``'s
``CutoverOrchestrator`` for the consumer contract.
"""

from importlib import import_module

_MODULE_EXPORTS = {
    "breadcrumb": (
        "clear_breadcrumb", "read_breadcrumb", "recover_stale_cutover", "write_breadcrumb",
    ),
    "claims": ("ClaimConflict", "Claimable", "decide_acquire", "generation_id", "is_recoverable"),
    "cutover": ("CutoverError", "CutoverOrchestrator", "CutoverResult"),
    "cutover_lock": ("CutoverLock", "CutoverLockedError"),
    "diagnostics": (
        "DaemonCandidate", "DiagnosticContext", "apply_daemon_health", "audit_daemon_health",
        "lock_data_is_live", "process_start_time", "terminate_pid_if_identity",
    ),
    "routing": (
        "Endpoint", "clear_if_owner", "publish_active", "read_active_endpoint",
        "read_table", "reap_stale_active", "routing_table_path",
    ),
    "singleton_manager": ("ManagerResult", "SingletonManager", "UnmanagedDaemonError"),
    "singleton_state": ("ManagerAlreadyRunning",),
}
_EXPORT_MODULES = {
    name: module for module, names in _MODULE_EXPORTS.items() for name in names
}


def __getattr__(name: str):
    if name in _MODULE_EXPORTS:
        value = import_module(f".{name}", __name__)
    elif name in _EXPORT_MODULES:
        value = getattr(import_module(f".{_EXPORT_MODULES[name]}", __name__), name)
    else:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    globals()[name] = value
    return value


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(__all__) | set(_MODULE_EXPORTS))

__all__ = [
    "Claimable",
    "ClaimConflict",
    "CutoverError",
    "CutoverLock",
    "CutoverLockedError",
    "CutoverOrchestrator",
    "CutoverResult",
    "DaemonCandidate",
    "DiagnosticContext",
    "Endpoint",
    "ManagerAlreadyRunning",
    "ManagerResult",
    "SingletonManager",
    "UnmanagedDaemonError",
    "apply_daemon_health",
    "audit_daemon_health",
    "breadcrumb",
    "claims",
    "clear_breadcrumb",
    "clear_if_owner",
    "cutover",
    "cutover_lock",
    "decide_acquire",
    "diagnostics",
    "generation_id",
    "is_recoverable",
    "lock_data_is_live",
    "process_start_time",
    "publish_active",
    "read_active_endpoint",
    "read_breadcrumb",
    "read_table",
    "reap_stale_active",
    "recover_stale_cutover",
    "routing",
    "routing_table_path",
    "terminate_pid_if_identity",
    "write_breadcrumb",
]
