"""CLI entry point for the agent-index service and query surface."""

from __future__ import annotations

import argparse
import contextlib
import io
import json
import os
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from agent_procutil import detached_kwargs, windowless_python
import httpx

from . import __version__
from .client import AgentIndexClient
from .config import (
    Config,
    client_url,
    discovered_endpoint,
    install_dir,
    load_config,
    routing_dir,
    run_dir,
)
from .query_surface import format_error, hit_to_dict
from .rendezvous import clear_endpoint, pid_alive as _pid_exists
from .runtime_version import current_runtime_version
from .server import serve


def _emit(value: Any) -> int:
    json.dump(value, sys.stdout, indent=2, sort_keys=True)
    sys.stdout.write("\n")
    return 0


def _emit_error(exc: BaseException) -> int:
    _emit({"error": format_error(exc), "hits": []})
    return 1


class ServiceOwnershipError(RuntimeError):
    """A reachable endpoint is not owned by this installation."""


class CutoverGovernanceBlocked(ServiceOwnershipError):
    """Installation governance changed after passive service preparation."""

    def __init__(self, governance: dict[str, Any]) -> None:
        super().__init__("installation governance blocked cutover before commit")
        self.governance = governance


CELL_TRANSACTION_PATH_ENV = "AGENT_INDEX_CELL_TRANSACTION"
CELL_TRANSACTION_TOKEN_ENV = "AGENT_INDEX_CELL_TRANSACTION_TOKEN"
CELL_LOCK_TOKEN_ENV = "AGENT_INDEX_CELL_LOCK_TOKEN"
CELL_LOCK_ROOT_ENV = "AGENT_INDEX_CELL_LOCK_ROOT"
CELL_START_TOKEN_ENV = "AGENT_INDEX_CELL_START_TOKEN"
CELL_TRANSACTION_SCHEMA = "copilot-extensions.agent-index.selection-transaction"
CUTOVER_CRASH_EVIDENCE_FILE = "cutover-crash-evidence.json"
CUTOVER_CRASH_EXIT_CODES = {
    "passive": 86,
    "flipped": 87,
    "draining": 88,
    "committed": 89,
}


def _expected_installation_id() -> str:
    return os.environ.get("AGENT_INDEX_INSTALLATION_ID", "")


def _selected_interpreter_matches(environment_name: str) -> bool:
    selected = os.environ.get(environment_name, "")
    if not selected:
        return False
    try:
        return os.path.samefile(selected, sys.executable)
    except OSError:
        return False


def _strict_json_object(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    value: dict[str, Any] = {}
    for key, item in pairs:
        if key in value:
            raise ServiceOwnershipError(f"duplicate transaction field: {key}")
        value[key] = item
    return value


def _validated_cell_transaction() -> dict[str, Any] | None:
    installation_id = _expected_installation_id()
    if not installation_id:
        return None
    root_value = os.environ.get("AGENT_INDEX_HOME", "")
    path_value = os.environ.get(CELL_TRANSACTION_PATH_ENV, "")
    supplied_token = os.environ.get(CELL_TRANSACTION_TOKEN_ENV, "")
    supplied_id = os.environ.get("AGENT_INDEX_CELL_TRANSACTION_ID", "")
    if not root_value or not path_value or not supplied_token or not supplied_id:
        raise ServiceOwnershipError(
            "namespaced deploy/recovery requires an installation transaction receipt"
        )
    root = Path(root_value).resolve()
    expected = root / "selection-transaction.json"
    path = Path(path_value)
    try:
        if path.is_symlink() or path.resolve(strict=True) != expected.resolve(strict=True):
            raise ServiceOwnershipError(
                "installation transaction receipt is outside the selected cell"
            )
        raw = path.read_text(encoding="utf-8")
        value = json.loads(raw, object_pairs_hook=_strict_json_object)
    except ServiceOwnershipError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise ServiceOwnershipError(
            "installation transaction receipt is unavailable or malformed"
        ) from exc
    target = value.get("target") if isinstance(value, dict) else None
    marketplace_id, separator, plugin_id = installation_id.partition("/")
    expected_context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    management = value.get("management") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema") != CELL_TRANSACTION_SCHEMA
        or value.get("version") != 1
        or value.get("id") != supplied_id
        or value.get("marketplaceId") != marketplace_id
        or not separator
        or value.get("pluginId") != plugin_id
        or value.get("installationId") != installation_id
        or value.get("token") != supplied_token
        or len(supplied_token) < 32
        or not isinstance(value.get("context"), str)
        or not expected_context
        or os.path.normcase(os.path.abspath(value["context"]))
        != os.path.normcase(os.path.abspath(expected_context))
        or value.get("state")
        not in {"prepared", "marker-published", "manifest-published", "reconciling"}
        or not isinstance(management, dict)
        or not isinstance(management.get("path"), str)
        or not isinstance(management.get("version"), str)
        or not isinstance(target, dict)
        or not isinstance(target.get("payloadRoot"), str)
        or not isinstance(target.get("payloadVersion"), str)
        or not isinstance(target.get("snapshotId"), str)
        or target.get("runtimeVersion") != current_runtime_version()
    ):
        raise ServiceOwnershipError(
            "installation transaction receipt does not authorize this runtime"
        )
    return value


def _validate_cutover_governance(transaction: dict[str, Any] | None) -> None:
    if transaction is None:
        return
    management = transaction.get("management")
    governance: dict[str, Any] = {
        "status": "invalid",
        "reason": "governance-check-failed",
    }
    try:
        if not isinstance(management, dict):
            raise OSError("management payload identity is absent")
        management_root = Path(str(management["path"]))
        if _path_is_link_or_reparse(management_root):
            raise OSError("management payload root is linked")
        management_root = management_root.resolve(strict=True)
        script = management_root / "scripts" / "cell-runtime.py"
        if (
            _path_is_link_or_reparse(script)
            or not script.is_file()
            or _path_is_link_or_reparse(script.parent)
        ):
            raise OSError("management governance checker is unavailable")
        context = Path(str(transaction["context"])).resolve(strict=True)
        durable_home = context.parents[4]
        result = subprocess.run(
            [
                sys.executable,
                "-I",
                "-X",
                "utf8",
                str(script),
                "governance-check",
                "--context",
                str(context),
                "--expected-marketplace-id",
                str(transaction["marketplaceId"]),
                "--durable-home",
                str(durable_home),
            ],
            cwd=management_root,
            env={
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONPATH", "PYTHONHOME"}
            },
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            check=False,
            timeout=30,
        )
        if result.returncode != 0:
            raise OSError(
                (result.stderr or result.stdout).strip()
                or "governance checker exited unsuccessfully"
            )
        payload = json.loads(
            result.stdout,
            object_pairs_hook=_strict_json_object,
        )
        if not isinstance(payload, dict):
            raise ValueError("governance result is not an object")
        observed = payload.get("governance")
        if isinstance(observed, dict):
            governance = observed
        if payload.get("active") is not True:
            raise CutoverGovernanceBlocked(governance)
    except CutoverGovernanceBlocked:
        raise
    except (
        KeyError,
        IndexError,
        OSError,
        ServiceOwnershipError,
        subprocess.SubprocessError,
        UnicodeError,
        ValueError,
    ):
        raise CutoverGovernanceBlocked(governance) from None


def _path_is_link_or_reparse(path: Path) -> bool:
    if path.is_symlink():
        return True
    try:
        return bool(path.lstat().st_file_attributes & 0x400)
    except (AttributeError, OSError):
        return False


def _configured_service_role() -> str | None:
    role = os.environ.get("AGENT_INDEX_ROLE", "").strip().lower()
    if not role:
        from .config import _read_config_role, config_path

        role = (_read_config_role(config_path()) or "").strip().lower()
    if role in {"host", "engine", "server", "indexer"}:
        return "host"
    if role in {"client", "none", "consumer"}:
        return "client"
    return None


def _validate_cell_start_authority(*, passive: bool) -> None:
    installation_id = _expected_installation_id()
    if not installation_id:
        raise ServiceOwnershipError(
            "the private cell startup path requires a namespaced installation"
        )
    if _configured_service_role() != "host":
        raise ServiceOwnershipError(
            "the private cell startup path requires the configured host role"
        )
    root_value = os.environ.get("AGENT_INDEX_HOME", "")
    lock_root_value = os.environ.get(CELL_LOCK_ROOT_ENV, "")
    lock_token = os.environ.get(CELL_LOCK_TOKEN_ENV, "")
    start_token = os.environ.get(CELL_START_TOKEN_ENV, "")
    if (
        not root_value
        or not lock_root_value
        or not lock_token
        or len(lock_token) < 32
        or start_token != lock_token
    ):
        raise ServiceOwnershipError(
            "the private cell startup path requires the owning lifecycle lock"
        )
    root = Path(root_value).resolve()
    lock_root = Path(lock_root_value).resolve()
    if os.path.normcase(str(root)) != os.path.normcase(str(lock_root)):
        raise ServiceOwnershipError(
            "the private cell startup lock belongs to another installation"
        )
    owner = root / ".payload-provision.lock.d" / "owner.json"
    try:
        if _path_is_link_or_reparse(owner) or _path_is_link_or_reparse(owner.parent):
            raise ServiceOwnershipError(
                "the private cell startup lock is not an ordinary receipt"
            )
        value = json.loads(
            owner.read_text(encoding="utf-8"),
            object_pairs_hook=_strict_json_object,
        )
    except ServiceOwnershipError:
        raise
    except (OSError, UnicodeError, ValueError) as exc:
        raise ServiceOwnershipError(
            "the private cell startup lock receipt is unavailable or malformed"
        ) from exc
    pid = value.get("pid") if isinstance(value, dict) else None
    if (
        not isinstance(value, dict)
        or value.get("schema") != "copilot-extensions.agent-index.cell-lock"
        or value.get("version") != 1
        or value.get("token") != lock_token
        or type(pid) is not int
        or not _pid_exists(pid)
    ):
        raise ServiceOwnershipError(
            "the private cell startup lifecycle lock is not live"
        )
    transaction_values = tuple(
        os.environ.get(name, "")
        for name in (
            CELL_TRANSACTION_PATH_ENV,
            CELL_TRANSACTION_TOKEN_ENV,
            "AGENT_INDEX_CELL_TRANSACTION_ID",
        )
    )
    if any(transaction_values):
        transaction = (
            _validated_cell_transaction() if all(transaction_values) else None
        )
        if transaction is None or transaction.get("state") != "reconciling":
            raise ServiceOwnershipError(
                "the private cell startup transaction is not in its "
                "service-reconciliation phase"
            )
    elif passive:
        raise ServiceOwnershipError(
            "a passive cell service requires the owning selection transaction"
        )


def _owned_service_status(
    url: str,
    *,
    expected_version: str | None = None,
    expected_pid: int | None = None,
    expected_instance_token: str | None = None,
    timeout: float = 5.0,
) -> dict[str, Any]:
    with httpx.Client(timeout=timeout) as client:
        response = client.get(f"{url.rstrip('/')}/health")
        response.raise_for_status()
        payload = response.json()
    if not isinstance(payload, dict):
        raise ServiceOwnershipError("service status is not an object")
    expected_installation = _expected_installation_id()
    if (payload.get("installationId") or "") != expected_installation:
        raise ServiceOwnershipError(
            "service installation identity does not match this invocation"
        )
    if payload.get("plugin") != "agent-index":
        raise ServiceOwnershipError("endpoint is not an Agent Index service")
    if expected_version is not None and payload.get("version") != expected_version:
        raise ServiceOwnershipError(
            f"service runtime {payload.get('version')} does not match "
            f"{expected_version}"
        )
    if expected_pid is not None and int(payload.get("pid", 0)) != expected_pid:
        raise ServiceOwnershipError("service pid does not match routing ownership")
    token = payload.get("instanceToken")
    if expected_installation and (not isinstance(token, str) or not token):
        raise ServiceOwnershipError("service did not attest an instance token")
    if expected_instance_token is not None and token != expected_instance_token:
        raise ServiceOwnershipError("service instance token changed during ownership check")
    return payload


def _routing_endpoint_for_url(url: str):
    try:
        from zdd.routing import Endpoint, read_table

        table = read_table(routing_dir()) or {}
        normalized = url.rstrip("/")
        for key in ("active", "previous"):
            raw = table.get(key)
            endpoint = Endpoint.from_dict(raw) if isinstance(raw, dict) else None
            if endpoint is not None and endpoint.base_url.rstrip("/") == normalized:
                return endpoint
    except Exception:
        pass
    return None


def _owned_service_client(
    url: str,
    *,
    timeout: float,
    transaction_token: str | None = None,
) -> tuple[dict[str, Any], AgentIndexClient]:
    expected_installation = _expected_installation_id()
    route = _routing_endpoint_for_url(url)
    if expected_installation and route is None:
        raise ServiceOwnershipError(
            "service endpoint is not present in this installation's routing record"
        )
    status = _owned_service_status(
        url,
        expected_version=getattr(route, "version", None),
        expected_pid=getattr(route, "pid", None),
        timeout=min(timeout, 5.0),
    )
    instance_token = (
        str(status["instanceToken"]) if status.get("instanceToken") else None
    )
    if expected_installation:
        if not instance_token:
            raise ServiceOwnershipError(
                "namespaced control requires an exact service instance token"
            )
        confirmed_route = _routing_endpoint_for_url(url)
        if (
            confirmed_route is None
            or confirmed_route.base_url.rstrip("/") != route.base_url.rstrip("/")
            or confirmed_route.pid != route.pid
            or confirmed_route.version != route.version
        ):
            raise ServiceOwnershipError(
                "service routing ownership changed during control validation"
            )
        status = _owned_service_status(
            url,
            expected_version=confirmed_route.version,
            expected_pid=confirmed_route.pid,
            expected_instance_token=instance_token,
            timeout=min(timeout, 5.0),
        )
    return status, AgentIndexClient(
        url,
        timeout=timeout,
        installation_id=expected_installation,
        instance_token=instance_token,
        transaction_token=transaction_token,
    )


def _setup_required_payload(*, runtime_state: str = "ready") -> dict[str, Any]:
    return {
        "schema": "agent-index.lifecycle",
        "schema_version": 1,
        "version": __version__,
        "plugin": "agent-index",
        "state": "setup_required",
        "setup_required": True,
        "configured": False,
        "role": None,
        "running": False,
        "runtime": {"state": runtime_state},
        "setup": {
            "interactive": "agent-index setup",
            "noninteractive": [
                "agent-index setup --single --yes",
                "agent-index setup --indexer <machine> --ssh <alias> --yes",
            ],
        },
    }


def _status_payload() -> dict[str, Any]:
    from . import transport

    role, _indexer = transport.plan_route()
    if role == "unconfigured":
        return _setup_required_payload()
    url = client_url()
    if not url:
        return {
            "schema": "agent-index.lifecycle",
            "schema_version": 1,
            "state": "not_running",
            "setup_required": False,
            "configured": True,
            "role": role,
            "running": False,
            "plugin": "agent-index",
            "version": __version__,
            "runtime": {"state": "ready"},
            "index": {"chunks": None, "available": None, "unreachable": True},
        }
    try:
        expected_installation = _expected_installation_id()
        route = _routing_endpoint_for_url(url)
        if expected_installation and route is None:
            raise ServiceOwnershipError(
                "service endpoint is not present in this installation's routing record"
            )
        health = _owned_service_status(
            url,
            expected_version=getattr(route, "version", None),
            expected_pid=getattr(route, "pid", None),
            timeout=10.0,
        )
        with httpx.Client(timeout=10.0) as client:
            payload = client.get(f"{url}/status").json()
        if not isinstance(payload, dict):
            raise ValueError("service status is not an object")
        if (payload.get("installationId") or "") != expected_installation:
            raise ServiceOwnershipError(
                "service installation identity does not match this invocation"
            )
        if (
            payload.get("pid") != health.get("pid")
            or payload.get("version") != health.get("version")
            or payload.get("instanceToken") != health.get("instanceToken")
        ):
            raise ServiceOwnershipError(
                "service status changed during ownership validation"
            )
        if expected_installation and payload.get("promoted") is not True:
            raise ServiceOwnershipError(
                "service endpoint is passive and has not completed promotion"
            )
        if payload.get("draining") is True or health.get("status") == "draining":
            payload["schema"] = "agent-index.lifecycle"
            payload["schema_version"] = 1
            payload["state"] = "draining"
            payload["setup_required"] = False
            payload["configured"] = True
            payload["role"] = role
            payload["runtime"] = {"state": "ready"}
            payload["running"] = False
            payload["endpoint"] = url
            return payload
        if health.get("status") != "ok":
            raise ServiceOwnershipError(
                f"service health state is not ready: {health.get('status')}"
            )
        payload["schema"] = "agent-index.lifecycle"
        payload["schema_version"] = 1
        payload["state"] = "ready"
        payload["setup_required"] = False
        payload["configured"] = True
        payload["role"] = role
        payload["runtime"] = {"state": "ready"}
        payload["running"] = True
        payload["endpoint"] = url
        return payload
    except (httpx.HTTPError, ServiceOwnershipError, ValueError) as exc:
        # Failing to reach the service is "unknown," not an empty index:
        # never fabricate chunks:0 here (dotfiles issue #1531).
        return {
            "schema": "agent-index.lifecycle",
            "schema_version": 1,
            "state": "unreachable",
            "setup_required": False,
            "configured": True,
            "role": role,
            "running": False,
            "plugin": "agent-index",
            "version": __version__,
            "runtime": {"state": "ready"},
            "error": str(exc),
            "endpoint": url,
            "index": {"chunks": None, "available": None, "unreachable": True},
        }


def _config_from_args(args: argparse.Namespace) -> Config:
    cfg = load_config()
    host = getattr(args, "host", None) or cfg.host
    port = getattr(args, "port", None)
    return Config(host=host, port=cfg.port if port is None else int(port))


def cmd_start(args: argparse.Namespace) -> int:
    """Public commands never launch or provision the optional host service."""
    print(
        "agent-index: the host service is managed by agent-dispatch. "
        "An already-running dispatch supervisor must provision and start it; "
        "this command cannot start, restart, deploy, or install the host runtime.",
        file=sys.stderr,
    )
    return 2


def cmd_managed_start(args: argparse.Namespace) -> int:
    """Run an already-selected interpreter without any provisioning fallback."""
    from . import transport

    role, indexer = transport.plan_route()
    if (
        not _selected_interpreter_matches("AGENT_INDEX_MANAGED_PYTHON")
        or _expected_installation_id()
        or os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
        or role != "host"
        or not indexer
    ):
        print(
            "agent-index: managed host launch requires the dispatch-selected "
            "interpreter and an effective host configuration in a supported "
            "installation context.",
            file=sys.stderr,
        )
        return 2
    serve(_config_from_args(args), passive=False)
    return 0


def cmd_managed_engine_start(args: argparse.Namespace) -> int:
    """Run the durable engine from the dispatch-selected interpreter only."""
    from . import transport
    from .engine.app import run_engine
    from .engine.daemon import engine_endpoint

    role, indexer = transport.plan_route()
    if (
        not _selected_interpreter_matches("AGENT_INDEX_ENGINE_MANAGED_PYTHON")
        or _expected_installation_id()
        or os.environ.get("COPILOT_EXTENSIONS_CONTEXT")
        or role != "host"
        or not indexer
    ):
        print(
            "agent-index: managed engine launch requires the dispatch-selected "
            "interpreter and an effective host configuration in a supported "
            "installation context.",
            file=sys.stderr,
        )
        return 2
    host, port = engine_endpoint()
    if getattr(args, "host", None):
        host = str(args.host)
    if getattr(args, "port", None) is not None:
        port = int(args.port)
    run_engine(host=host, port=port)
    return 0


def cmd_managed_engine_health(_args: argparse.Namespace) -> int:
    """Probe engine readiness against generation, interpreter, and deps."""
    from . import transport
    from .engine.generation import current_engine_generation
    from .index_config import IndexConfig
    import httpx

    role, indexer = transport.plan_route()
    if role != "host" or not indexer:
        return _emit(
            {
                "schema_version": 1,
                "healthy": False,
                "detail": "agent-index engine host configuration is inactive",
            }
        )
    if _expected_installation_id() or os.environ.get("COPILOT_EXTENSIONS_CONTEXT"):
        return _emit(
            {
                "schema_version": 1,
                "healthy": False,
                "detail": "agent-index engine health requires the host companion context",
            }
        )
    if not _selected_interpreter_matches("AGENT_INDEX_ENGINE_MANAGED_PYTHON"):
        return _emit(
            {
                "schema_version": 1,
                "healthy": False,
                "detail": "agent-index engine is not using the dispatch-selected interpreter",
            }
        )
    config = IndexConfig()
    profile = next(iter(config.model_profiles.values()))
    try:
        response = httpx.get(
            f"{profile.engine_url.rstrip('/')}/health",
            timeout=5.0,
        )
        response.raise_for_status()
        payload = response.json()
    except (httpx.HTTPError, ValueError):
        payload = {
            "status": "unreachable",
            "detail": f"agent-index engine is unreachable at {profile.engine_url}",
        }
    observed_generation = payload.get("generation")
    if payload.get("status") == "unreachable":
        detail = payload.get("detail") or "agent-index engine is unreachable"
        return _emit({"schema_version": 1, "healthy": False, "detail": detail})
    if observed_generation != current_engine_generation():
        return _emit(
            {
                "schema_version": 1,
                "healthy": False,
                "detail": (
                    "agent-index engine generation mismatch: "
                    f"expected {current_engine_generation()}, got {observed_generation}"
                ),
            }
        )
    python_executable = payload.get("python_executable")
    try:
        interpreter_matches = isinstance(python_executable, str) and os.path.samefile(
            python_executable, sys.executable
        )
    except OSError:
        interpreter_matches = False
    if not interpreter_matches:
        return _emit(
            {
                "schema_version": 1,
                "healthy": False,
                "detail": "agent-index engine is running under a different interpreter",
            }
        )
    if payload.get("gpu_deps_installed") is not True:
        return _emit(
            {
                "schema_version": 1,
                "healthy": False,
                "detail": payload.get("detail")
                or "agent-index engine dependencies are not installed",
            }
        )
    device = str(config.device).strip().lower()
    if device.startswith("cuda") and payload.get("cuda_available") is not True:
        return _emit(
            {
                "schema_version": 1,
                "healthy": False,
                "detail": payload.get("detail")
                or "agent-index engine CUDA is unavailable for the configured device",
            }
        )
    return _emit(
        {
            "schema_version": 1,
            "healthy": True,
            "detail": (
                f"agent-index engine generation {current_engine_generation()} "
                "is reachable with healthy dependencies"
            ),
        }
    )


def cmd_cell_start(args: argparse.Namespace) -> int:
    try:
        _validate_cell_start_authority(
            passive=bool(getattr(args, "passive", False))
        )
    except ServiceOwnershipError as exc:
        print(f"agent-index: {exc}", file=sys.stderr)
        return 2
    serve(_config_from_args(args), passive=bool(getattr(args, "passive", False)))
    return 0


def cmd_status(_args: argparse.Namespace) -> int:
    return _emit(_status_payload())


def cmd_installer_readiness(_args: argparse.Namespace) -> int:
    from . import transport
    from .installer_readiness import (
        client_configured,
        client_transport_missing,
        emit,
        evaluate,
        inspect_configuration,
        unconfigured,
    )

    role, _indexer = transport.plan_route()
    if role == "unconfigured":
        return emit(unconfigured())
    if role == "client":
        result = (
            client_configured()
            if transport.has_usable_client_transport()
            else client_transport_missing()
        )
        return emit(result)
    return emit(evaluate(_status_payload(), inspect_configuration()))


def cmd_version(_args: argparse.Namespace) -> int:
    payload = _status_payload()
    print(payload.get("version") or __version__)
    return 0


def cmd_mcp(_args: argparse.Namespace) -> int:
    import importlib.util

    if importlib.util.find_spec("mcp") is None:
        print(
            "agent-index: the optional stdio MCP dependencies are unavailable "
            "in the lightweight client runtime. Use the read CLI or the hosted "
            "HTTP service; this command will not install host dependencies.",
            file=sys.stderr,
        )
        return 2
    from agent_index.mcp_app import serve_stdio

    serve_stdio()
    return 0


def cmd_role(args: argparse.Namespace) -> int:
    """Print the current repository's resolved agent-index activation role."""
    from agent_index.transport import plan_route

    role, _indexer = plan_route()
    if getattr(args, "json", False):
        return _emit(
            {
                "role": None if role == "unconfigured" else role,
                "state": "setup_required" if role == "unconfigured" else "ready",
                "setup_required": role == "unconfigured",
            }
        )
    print(role)
    return 0


def _setup_multi(cfg, args, this: str, root, indexers: list[dict]) -> int:
    """Adopt this machine against an authored **ordered** ``indexers:`` list.

    This box is a ``host`` if it is any listed indexer, else a ``client`` that routes
    to the ordered endpoints with failover. Only THIS machine's machine-local role /
    routing is written; the operator-authored repo list is left as the source of
    truth (vision §adoption-designates-ordered-indexers)."""
    machines = [str(i["machine"]).strip() for i in indexers if i.get("machine")]
    is_host = any(m.lower() == this.lower() for m in machines)
    role = "host" if is_host else "client"

    device = None
    if role == "host":
        from agent_index import capability

        decision = capability.decide_device()
        device = decision["device"]
        if not decision["ok"] and not getattr(args, "force", False):
            if getattr(args, "json", False):
                _emit({"machine": this, "role": role, "blocked": True, **decision})
            else:
                print(
                    f"[FAIL] '{this}' is an underpowered indexer: {decision['reason']}.\n"
                    f"       Designate a stronger machine, or re-run with --force to override.",
                    file=sys.stderr,
                )
            return 1

    machine_updates: dict = {"role": role}
    if device:
        machine_updates["device"] = device
    endpoints: list[str] = []
    ssh_targets: list[str] = []
    if role == "client":
        endpoints = [str(i["endpoint"]).strip() for i in indexers if i.get("endpoint")]
        ssh_targets = [str(i["ssh"]).strip() for i in indexers if i.get("ssh")]
        if endpoints:
            # Ordered failover list (primary first) + singular back-compat mirror.
            machine_updates["endpoints"] = endpoints
            machine_updates["endpoint"] = endpoints[0]
        role_path = cfg.set_machine_config(machine_updates)
    else:
        # This box is a host: clear any stale client routing a prior client
        # adoption may have left, so it never shadows the live local service.
        role_path = cfg.set_machine_config(machine_updates, remove=["endpoint", "endpoints"])

    result = {
        "machine": this,
        "role": role,
        "device": device,
        "indexers": machines,
        "endpoints": endpoints,
        "ssh_targets": ssh_targets,
        "repo": str(root) if root else None,
        "written": {"machine_config": str(role_path)},
        "service": {
            "manager": "agent-dispatch" if role == "host" else None,
            "state": "dispatch-managed" if role == "host" else "not-required",
            "started_by_setup": False,
            "provisioned_by_setup": False,
        },
    }
    if getattr(args, "json", False):
        return _emit(result)

    print(f"agent-index adoption: this machine '{this}' -> role: {role}")
    print(f"  designated indexers (primary first): {', '.join(machines)}")
    if device:
        print(f"  engine device: {device}")
    if role == "client":
        if endpoints:
            print(f"  routing endpoints (failover order): {', '.join(endpoints)}")
            if ssh_targets:
                print(f"  ssh targets: {', '.join(ssh_targets)} -- establish an SSH "
                      "port-forward per target so each endpoint reaches its indexer")
        else:
            print("  routing endpoints: (unset) -- add endpoints to the repo's indexers list")
    else:
        print("  host service: managed by an already-running agent-dispatch "
              "supervisor; setup does not provision or start it. "
              "The independent embedding engine is unchanged.")
    print(f"  machine config: {role_path}")
    return 0


def cmd_setup(args: argparse.Namespace) -> int:
    """Adopt agent-index: designate one indexer, then write role + designation config.

    Records the shared indexer designation into ``<repo>/.agent-index/config.yaml``
    and this machine's concrete ``role:`` into the machine-local config (which the
    installer reads). Running setup on the designated machine makes it the ``host``;
    everywhere else it is a ``client`` (effort agent-index-engine-daemon, Phase 6;
    vision §adoption-designates-one-indexer).
    """
    from agent_index import config as cfg

    this = cfg.machine_id()
    root = cfg.repo_root(getattr(args, "repo", None))
    interactive = sys.stdin.isatty() and not getattr(args, "yes", False)

    single = bool(getattr(args, "single", False))
    indexer = getattr(args, "indexer", None)
    ssh = getattr(args, "ssh", None)
    endpoint = getattr(args, "endpoint", None)

    # Multi-indexer adoption: when neither --single nor an explicit --indexer is
    # given, honor an **authored** plural ``indexers:`` list already in the repo
    # config (primary first). This box is a host if it is ANY listed indexer;
    # otherwise a client that routes to the ordered list with failover. The list is
    # operator-authored, so setup only resolves THIS machine's role/routing from it
    # and never rewrites it (vision §adoption-designates-ordered-indexers).
    if not single and not indexer and root is not None:
        raw = cfg._load_yaml(cfg.repo_config_path(root))
        if isinstance(raw.get("indexers"), list) and cfg.read_indexers(root):
            return _setup_multi(cfg, args, this, root, cfg.read_indexers(root))

    if not single and not indexer:
        if interactive:
            ans = input(
                f"Single-machine setup (this box '{this}' hosts everything)? [Y/n] "
            ).strip().lower()
            single = ans in ("", "y", "yes")
            if not single:
                indexer = input(
                    f"Which machine is the indexer? [default: {this}] "
                ).strip() or this
                if indexer.lower() != this:
                    ssh = ssh or (input(
                        f"SSH alias clients use to reach '{indexer}' (blank to skip): "
                    ).strip() or None)
        else:
            payload = _setup_required_payload()
            payload["error"] = (
                "Non-interactive setup requires an explicit role choice: pass "
                "`--single` or `--indexer <machine>`."
            )
            _emit(payload)
            return 2

    if single:
        indexer = this
    designated = (indexer or this).strip()
    role = "host" if designated.lower() == this.lower() else "client"

    # Capability match for a host designation: hard-block an underpowered CPU-only
    # indexer candidate; record the chosen device (Phase 7; §capability-matched).
    device = None
    if role == "host":
        from agent_index import capability

        decision = capability.decide_device()
        device = decision["device"]
        if not decision["ok"] and not getattr(args, "force", False):
            msg = (
                f"[FAIL] '{this}' is an underpowered indexer: {decision['reason']}.\n"
                f"       Designate a stronger machine, or re-run with --force to override."
            )
            if getattr(args, "json", False):
                _emit({"machine": this, "role": role, "blocked": True, **decision})
            else:
                print(msg, file=sys.stderr)
            return 1

    # Client routing: resolve the endpoint this client uses to reach the designated
    # indexer -- explicit --endpoint, else the repo's recorded indexer.endpoint;
    # ssh alias likewise falls back to the repo (Phase 8; §local-first-standalone).
    if role == "client":
        rec = cfg.read_indexer(root) or {}
        endpoint = endpoint or rec.get("endpoint")
        ssh = ssh or rec.get("ssh")

    written: dict[str, str] = {}
    if root is not None:
        p = cfg.write_indexer_designation(root, designated, ssh=ssh, endpoint=endpoint)
        written["repo_config"] = str(p)
    machine_updates: dict = {"role": role}
    if device:
        machine_updates["device"] = device
    if role == "client" and endpoint:
        machine_updates["endpoint"] = endpoint
    role_path = cfg.set_machine_config(machine_updates)
    written["machine_config"] = str(role_path)

    result = {
        "machine": this,
        "indexer": designated,
        "role": role,
        "device": device,
        "single_machine": single,
        "ssh": ssh,
        "endpoint": endpoint,
        "repo": str(root) if root else None,
        "written": written,
        "service": {
            "manager": "agent-dispatch" if role == "host" else None,
            "state": "dispatch-managed" if role == "host" else "not-required",
            "started_by_setup": False,
            "provisioned_by_setup": False,
        },
    }
    if getattr(args, "json", False):
        return _emit(result)

    print(f"agent-index adoption: this machine '{this}' -> role: {role}")
    print(f"  designated indexer: {designated}" + (f" (ssh: {ssh})" if ssh else ""))
    if device:
        print(f"  engine device: {device}")
    if role == "client":
        if endpoint:
            print(f"  routing endpoint: {endpoint}")
        else:
            print("  routing endpoint: (unset) -- pass --endpoint or record indexer.endpoint "
                  "in the repo config so this client can reach the service")
    if root is None:
        print("  note: no repo detected (--repo / AGENT_INDEX_REPO / git cwd) -- "
              "wrote machine-local role only; the shared designation was not recorded")
    else:
        print(f"  repo config:    {written.get('repo_config')}")
    print(f"  machine config: {written['machine_config']}")
    if role == "host":
        print("  host service: managed by an already-running agent-dispatch "
              "supervisor; setup does not provision or start it. "
              "The independent embedding engine is unchanged.")
    else:
        print("  next: use the lightweight client CLI; no local host service is installed.")
        if ssh and endpoint:
            print(f"        establish the trusted transport, e.g. an SSH port-forward via '{ssh}' "
                  f"so {endpoint} reaches the indexer '{designated}'.")
        elif ssh:
            print(f"        establish an SSH port-forward via '{ssh}' to the indexer's service, "
                  "then set the local endpoint (--endpoint / AGENT_INDEX_ENDPOINT).")
    return 0


def cmd_capability(args: argparse.Namespace) -> int:
    """Detect this host's capabilities and the engine device it would use."""
    from agent_index import capability

    decision = capability.decide_device()
    if getattr(args, "json", False):
        return _emit(decision)
    verdict = "OK" if decision["ok"] else "UNDERPOWERED (CPU-only indexer would be blocked)"
    print(f"agent-index capability: {verdict}")
    print(f"  cores: {decision['cores']}  ram_gb: {decision['ram_gb']}  cuda: {decision['cuda']}")
    print(f"  device: {decision['device']}  ({decision['reason']})")
    return 0 if decision["ok"] else 1


def cmd_engine(args: argparse.Namespace) -> int:
    """Manage the durable, persistent embedding-engine daemon."""
    from agent_index.engine import daemon

    action = args.engine_action
    if action == "status":
        return _emit(daemon.status())
    if action == "start":
        try:
            print(daemon.start())
        except (FileNotFoundError, RuntimeError, TimeoutError) as exc:
            print(f"[FAIL] {exc}", file=sys.stderr)
            return 1
        return 0
    if action == "stop":
        print(daemon.stop())
        return 0
    if action == "run":
        return daemon.run_foreground()
    print(f"[FAIL] unknown engine action: {action}", file=sys.stderr)
    return 2


def _routing_endpoint():
    try:
        from zdd.routing import read_active_endpoint

        return read_active_endpoint(routing_dir(), verify_listener=False)
    except Exception:
        return None


def cmd_stop(_args: argparse.Namespace) -> int:
    routed = _routing_endpoint()
    url = client_url()
    if url:
        try:
            status, owned_client = _owned_service_client(url, timeout=5.0)
            pid = int(status.get("pid", 0)) or None
            if (
                pid is None
                and routed is not None
                and routed.base_url.rstrip("/") == url.rstrip("/")
            ):
                pid = getattr(routed, "pid", None)
            owned_client.shutdown()
            if pid and pid != os.getpid():
                deadline = time.monotonic() + 5.0
                while time.monotonic() < deadline:
                    if not _pid_exists(pid):
                        return _emit({"stopped": True, "pid": pid})
                    time.sleep(0.2)
                return _emit({"stopped": False, "reason": "still-running", "pid": pid})
            return _emit({"stopped": True})
        except ServiceOwnershipError as exc:
            return _emit(
                {
                    "stopped": False,
                    "reason": "ownership-mismatch",
                    "error": str(exc),
                    "endpoint": url,
                }
            )
        except Exception as exc:
            return _emit(
                {
                    "stopped": False,
                    "reason": "ownership-unverified",
                    "error": str(exc),
                    "endpoint": url,
                }
            )

    ep = discovered_endpoint()
    if ep is None or not ep.pid:
        return _emit({"stopped": False, "reason": "not-running"})
    if ep.pid == os.getpid():
        return _emit({"stopped": False, "reason": "refusing-to-stop-self"})
    try:
        status, owned_client = _owned_service_client(
            f"http://{ep.address}",
            timeout=5.0,
        )
    except ServiceOwnershipError as exc:
        return _emit(
            {
                "stopped": False,
                "reason": "ownership-mismatch",
                "pid": ep.pid,
                "error": str(exc),
            }
        )
    except Exception as exc:
        if not _pid_exists(ep.pid):
            clear_endpoint(run_dir())
            return _emit({"stopped": False, "reason": "not-running", "pid": ep.pid})
        return _emit(
            {
                "stopped": False,
                "reason": "ownership-unverified",
                "pid": ep.pid,
                "error": str(exc),
            }
        )
    pid = int(status.get("pid", 0)) or ep.pid
    try:
        owned_client.shutdown()
    except Exception as exc:
        return _emit(
            {
                "stopped": False,
                "reason": "shutdown-refused",
                "pid": pid,
                "error": str(exc),
            }
        )

    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if not _pid_exists(pid):
            clear_endpoint(run_dir())
            return _emit({"stopped": True, "pid": pid})
        time.sleep(0.2)
    return _emit({"stopped": False, "reason": "still-running", "pid": pid})


def cmd_index(args: argparse.Namespace) -> int:
    try:
        from agent_index.indexing import engine as indexing_engine

        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            result = indexing_engine.run_reindex(full=args.full, source=args.source)
        _emit(result)
        # A per-source failure is swallowed by the run loop (so other sources
        # still index); surface it here as a non-zero exit rather than letting a
        # wholly-failed reindex look like a clean run (#1350).
        return 1 if result.get("sources_failed") else 0
    except Exception as exc:
        return _emit_error(exc)


def cmd_index_worker(args: argparse.Namespace) -> int:
    """Run one queued indexing task in this (versioned) worker process.

    Internal entry point spawned by the service's TaskRunner (model A): the
    worker runs from the active versioned slot's python, so it survives a
    service cutover and makes the job resumable via the durable queue.
    """
    from agent_index.indexing.worker import run_worker

    return run_worker(args.task)


def cmd_search(args: argparse.Namespace) -> int:
    try:
        from agent_index import transport

        role, _indexer = transport.plan_route()
        if role == "client":
            url = client_url()
            if not url:
                raise RuntimeError("configured client endpoint is unavailable")
            payload = AgentIndexClient(url).search(
                args.query,
                limit=args.limit,
                source=args.source,
                language=args.language,
                repo=args.repo,
            )
            if not payload.get("available", False):
                raise RuntimeError(payload.get("error") or "remote search unavailable")
            return _emit(payload.get("hits", []))

        from agent_index.search import engine as search_engine

        if not args.json and sys.stdout.isatty():
            search_engine.run_search(
                query=args.query,
                limit=args.limit,
                source=args.source,
                language=args.language,
                repo=args.repo,
            )
            return 0

        engine = search_engine.create_search_engine()
        hits = engine.search(
            args.query,
            limit=args.limit,
            source=args.source,
            language=args.language,
            repo=args.repo,
        )
        return _emit([hit_to_dict(hit) for hit in hits])
    except Exception as exc:
        return _emit_error(exc)


def cmd_similar(args: argparse.Namespace) -> int:
    try:
        from agent_index import transport

        role, _indexer = transport.plan_route()
        if role == "client":
            url = client_url()
            if not url:
                raise RuntimeError("configured client endpoint is unavailable")
            payload = AgentIndexClient(url).similar(
                args.chunk_id,
                limit=args.limit,
                source=args.source,
            )
            if not payload.get("available", False):
                raise RuntimeError(payload.get("error") or "remote similarity unavailable")
            return _emit(payload.get("hits", []))

        from agent_index.search import engine as search_engine

        engine = search_engine.create_search_engine()
        hits = engine.find_similar(args.chunk_id, limit=args.limit, source=args.source)
        return _emit([hit_to_dict(hit) for hit in hits])
    except Exception as exc:
        return _emit_error(exc)


def cmd_clusters(args: argparse.Namespace) -> int:
    try:
        from agent_index import transport

        role, _indexer = transport.plan_route()
        if role == "client":
            url = client_url()
            if not url:
                raise RuntimeError("configured client endpoint is unavailable")
            payload = AgentIndexClient(url).clusters(
                source=args.source,
                bucket=args.bucket,
                model=args.model,
                exact_dupes_only=args.exact_dupes_only,
                limit=args.limit,
            )
            if not payload.get("available", False):
                raise RuntimeError(payload.get("error") or "remote clusters unavailable")
            return _emit(payload.get("clusters", []))

        from agent_index.index_config import IndexConfig
        from agent_index.store.cluster_store import ClusterStore
        from agent_index.store.clustering import source_bucket

        from .query_surface import stored_cluster_to_dict

        bucket = args.bucket
        if args.source and not bucket:
            bucket = source_bucket(args.source)
        config = IndexConfig()
        store = ClusterStore(config.clusters_db)
        stored = store.list_clusters(
            bucket=bucket,
            model_id=args.model,
            has_exact_dupes=True if args.exact_dupes_only else None,
            limit=args.limit,
            offset=0,
        )
        return _emit([stored_cluster_to_dict(c) for c in stored])
    except Exception as exc:
        return _emit_error(exc)


def cmd_deploy(args: argparse.Namespace) -> int:
    from zdd import breadcrumb
    from zdd import routing as zdd_routing
    from zdd.cutover import CutoverOrchestrator

    cfg = load_config()
    expected_installation = _expected_installation_id()
    runtime_version = current_runtime_version()
    try:
        transaction = _validated_cell_transaction()
    except ServiceOwnershipError as exc:
        if args.json:
            _emit(
                {
                    "ok": False,
                    "reason": "transaction-unauthorized",
                    "error": str(exc),
                }
            )
        else:
            print(f"[FAIL] {exc}", file=sys.stderr)
        return 1
    transaction_token = (
        str(transaction["token"]) if transaction is not None else None
    )
    injected_phase = (
        os.environ.get("AGENT_INDEX_TEST_CUTOVER_CRASH_PHASE", "").strip().lower()
        if expected_installation
        else ""
    )
    passive_instance: dict[str, Any] = {}
    governance_block: dict[str, Any] = {}
    drain_started: set[str] = set()

    def crash_at(phase: str) -> None:
        if injected_phase == phase:
            exit_code = CUTOVER_CRASH_EXIT_CODES[phase]
            route = _routing_endpoint()
            evidence = {
                "schema": "copilot-extensions.agent-index.cutover-crash-evidence",
                "version": 1,
                "phase": phase,
                "exitCode": exit_code,
                "pid": os.getpid(),
                "installationId": expected_installation,
                "runtimeVersion": runtime_version,
                "transactionId": (
                    transaction.get("id") if isinstance(transaction, dict) else None
                ),
                "passive": dict(passive_instance),
                "route": (
                    {
                        "bind": route.bind,
                        "port": route.port,
                        "pid": route.pid,
                        "version": route.version,
                    }
                    if route is not None
                    else None
                ),
            }
            path = run_dir() / CUTOVER_CRASH_EVIDENCE_FILE
            path.parent.mkdir(parents=True, exist_ok=True)
            temporary = path.with_name(
                f".{path.name}.{os.getpid()}.{phase}.tmp"
            )
            temporary.write_text(
                json.dumps(evidence, separators=(",", ":"), sort_keys=True) + "\n",
                encoding="utf-8",
                newline="\n",
            )
            os.replace(temporary, path)
            os._exit(exit_code)

    wildcard_v4 = ".".join(("0", "0", "0", "0"))
    host = cfg.host if cfg.host not in (wildcard_v4, "", "::") else "127.0.0.1"
    if cfg.host == "::":
        host = "::1"
    cutover_started = False

    def pick_free_port() -> int:
        family = socket.AF_INET6 if ":" in host else socket.AF_INET
        with socket.socket(family, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])

    def spawn_passive(port: int):
        start_command = "__cell-start" if expected_installation else "start"
        cmd = [
            windowless_python(sys.executable),
            "-I",
            "-X",
            "utf8",
            "-m",
            "agent_index",
            start_command,
            "--host",
            cfg.host,
            "--port",
            str(port),
            "--passive",
        ]
        kwargs: dict[str, Any] = {
            "cwd": str(install_dir().resolve()),
            "env": {
                key: value
                for key, value in os.environ.items()
                if key not in {"PYTHONPATH", "PYTHONHOME"}
            },
            "stdin": subprocess.DEVNULL,
            "stdout": subprocess.DEVNULL,
            "stderr": subprocess.DEVNULL,
        }
        kwargs.update(detached_kwargs())
        handle = subprocess.Popen(cmd, **kwargs)  # noqa: S603
        passive_instance.update({"port": port, "pid": handle.pid})
        return handle

    def health_check(check_host: str, port: int) -> bool:
        try:
            route = _routing_endpoint_for_url(f"http://{check_host}:{port}")
            expected_pid = (
                int(passive_instance["pid"])
                if passive_instance.get("port") == port
                else getattr(route, "pid", None)
            )
            expected_version = (
                runtime_version
                if passive_instance.get("port") == port
                else getattr(route, "version", None)
            )
            payload = _owned_service_status(
                f"http://{check_host}:{port}",
                expected_version=expected_version,
                expected_pid=expected_pid,
                timeout=2.0,
            )
            if (
                passive_instance.get("port") == port
                and payload.get("promoted") is not True
            ):
                crash_at("passive")
            return payload.get("status") != "draining"
        except Exception:
            return False

    def make_recovery_client(base_url: str) -> AgentIndexClient:
        _status, client = _owned_service_client(
            base_url,
            timeout=float(args.drain_timeout) + 60.0,
            transaction_token=transaction_token,
        )
        return client

    def make_client(base_url: str) -> AgentIndexClient:
        normalized_url = base_url.rstrip("/")
        if (
            cutover_started
            and injected_phase == "flipped"
            and passive_instance.get("port") is not None
            and base_url.rstrip("/")
            != f"http://{host}:{passive_instance['port']}".rstrip("/")
        ):
            crash_at("flipped")
        client = make_recovery_client(base_url)

        class PhaseClient:
            def health(self) -> dict[str, Any]:
                return client.health()

            def drain(
                self,
                *,
                timeout: float,
                poll: float,
                force: bool,
            ) -> dict[str, Any]:
                drain_started.add(normalized_url)
                result = client.drain(timeout=timeout, poll=poll, force=force)
                crash_at("draining")
                return result

            def undrain(self) -> dict[str, Any]:
                if normalized_url not in drain_started:
                    return {"draining": False}
                return client.undrain()

            def shutdown(self) -> dict[str, Any]:
                crash_at("committed")
                return client.shutdown()

            def adopt_relay(self) -> dict[str, Any]:
                return client.adopt_relay()

        return PhaseClient()  # type: ignore[return-value]

    def liveness_check(check_host: str, port: int) -> bool:
        # Recovery MUST use a plain liveness probe, NOT the draining-aware
        # health_check above: an aborted cutover strands the old service DRAINING,
        # and the point of recovery is to undrain it. A draining-aware probe
        # reports a drained survivor as "unreachable", so recovery would retire the
        # breadcrumb without undraining -- leaving the service permanently closed to
        # new work. A drained daemon still answers /health 200, so any 200 is alive.
        try:
            route = _routing_endpoint_for_url(f"http://{check_host}:{port}")
            if expected_installation and route is None:
                return False
            payload = _owned_service_status(
                f"http://{check_host}:{port}",
                expected_version=getattr(route, "version", None),
                expected_pid=getattr(route, "pid", None),
                timeout=2.0,
            )
            return payload.get("plugin") == "agent-index"
        except Exception:
            return False

    class PromotingRouting:
        def __getattr__(self, name: str) -> Any:
            return getattr(zdd_routing, name)

        def publish_active(self, config_dir: Any, **kwargs: Any):
            target_publish = (
                passive_instance.get("port") == kwargs.get("port")
                and passive_instance.get("pid") == kwargs.get("pid")
            )
            if not target_publish:
                current = zdd_routing.read_active_endpoint(
                    config_dir,
                    verify_listener=False,
                )
                if (
                    current is not None
                    and current.bind == kwargs.get("bind")
                    and current.port == kwargs.get("port")
                    and current.pid == kwargs.get("pid")
                    and current.version == kwargs.get("version")
                ):
                    return current
            if target_publish:
                try:
                    _validate_cutover_governance(transaction)
                except CutoverGovernanceBlocked as exc:
                    governance_block.update(exc.governance)
                    raise
                base_url = f"http://{host}:{kwargs['port']}"
                status = _owned_service_status(
                    base_url,
                    expected_version=kwargs.get("version"),
                    expected_pid=kwargs.get("pid"),
                    timeout=5.0,
                )
                instance_token = str(status.get("instanceToken") or "")
                if expected_installation and not instance_token:
                    raise ServiceOwnershipError(
                        "passive service did not attest an instance token"
                    )
                promoted = AgentIndexClient(
                    base_url,
                    timeout=30.0,
                    installation_id=expected_installation,
                    instance_token=instance_token or None,
                    transaction_token=transaction_token,
                ).promote()
                if promoted.get("promoted") is not True:
                    raise ServiceOwnershipError(
                        "passive service did not acknowledge promotion"
                    )
                ready = _owned_service_status(
                    base_url,
                    expected_version=kwargs.get("version"),
                    expected_pid=kwargs.get("pid"),
                    expected_instance_token=instance_token or None,
                    timeout=5.0,
                )
                if (
                    ready.get("promoted") is not True
                    or ready.get("status") != "ok"
                ):
                    raise ServiceOwnershipError(
                        "promoted service did not become read-ready"
                    )
            return zdd_routing.publish_active(config_dir, **kwargs)

    routed = _routing_endpoint()
    if routed is not None:
        try:
            _owned_service_client(routed.base_url, timeout=2.0)
        except ServiceOwnershipError as exc:
            if args.json:
                _emit(
                    {
                        "ok": False,
                        "error": str(exc),
                        "reason": "ownership-mismatch",
                    }
                )
            else:
                print(f"[FAIL] {exc}", file=sys.stderr)
            return 1
        except Exception:
            pass

    recovery = breadcrumb.recover_stale_cutover(
        routing_dir(), make_recovery_client, health_check=liveness_check
    )
    if getattr(args, "recover", False):
        if args.json:
            _emit(recovery)
        elif recovery.get("recovered"):
            print(f"[OK] {recovery.get('reason')}")
        else:
            print(f"[>] {recovery.get('reason')}")
        return 0
    if recovery.get("recovered") and not args.json:
        print(f"[>] Recovered a prior aborted cutover: {recovery.get('reason')}")

    cutover_started = True
    orch = CutoverOrchestrator(
        routing_dir(),
        bind=cfg.host,
        version=runtime_version,
        spawn_passive=spawn_passive,
        health_check=health_check,
        make_client=make_client,
        pick_free_port=pick_free_port,
        routing_mod=PromotingRouting(),
    )
    result = orch.run(
        health_timeout=args.health_timeout,
        drain_timeout=args.drain_timeout,
        force=args.force,
    )

    if result.ok:
        active = _routing_endpoint()
        if active is None:
            result.ok = False
            result.error = "cutover did not publish an active endpoint"
        else:
            try:
                _owned_service_client(
                    active.base_url,
                    timeout=2.0,
                )
                _owned_service_status(
                    active.base_url,
                    expected_version=runtime_version,
                    expected_pid=getattr(active, "pid", None),
                    timeout=2.0,
                )
            except Exception as exc:
                result.ok = False
                result.error = f"cutover endpoint failed ownership validation: {exc}"

    if args.json:
        payload = result.to_dict()
        if governance_block:
            payload["reason"] = "governance-blocked-before-commit"
            payload["governance"] = governance_block
        _emit(payload)
    else:
        for step in result.steps:
            print(f"  - {step}")
        if result.ok:
            print(f"Cutover complete: active daemon now on port {result.new_port}.")
        elif result.rolled_back:
            print(f"[WARN] Cutover rolled back: {result.error}", file=sys.stderr)
        else:
            print(f"[FAIL] Cutover failed: {result.error}", file=sys.stderr)
    return 0 if result.ok else 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="agent-index")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command")

    def add_start_args(p: argparse.ArgumentParser) -> None:
        p.add_argument("--host", help="bind host (defaults to AGENT_INDEX_HOST or 127.0.0.1)")
        p.add_argument("--port", type=int, help="bind port (defaults to AGENT_INDEX_PORT or 0)")
        p.add_argument(
            "--passive",
            action="store_true",
            help="start as a passive cutover instance",
        )

    p_start = sub.add_parser("start", help="run the local service shell")
    add_start_args(p_start)
    p_start.set_defaults(func=cmd_start)
    p_restart = sub.add_parser("restart", help="report dispatch-owned host lifecycle")
    p_restart.set_defaults(func=cmd_start)
    p_serve = sub.add_parser("serve", help="alias for start")
    add_start_args(p_serve)
    p_serve.set_defaults(func=cmd_start)
    p_managed = sub.add_parser("__managed-start", help=argparse.SUPPRESS)
    add_start_args(p_managed)
    p_managed.set_defaults(func=cmd_managed_start)
    p_managed_engine = sub.add_parser(
        "__managed-engine-start", help=argparse.SUPPRESS
    )
    add_start_args(p_managed_engine)
    p_managed_engine.set_defaults(func=cmd_managed_engine_start)
    p_managed_engine_health = sub.add_parser(
        "__managed-engine-health", help=argparse.SUPPRESS
    )
    p_managed_engine_health.set_defaults(func=cmd_managed_engine_health)
    p_cell_start = sub.add_parser("__cell-start", help=argparse.SUPPRESS)
    add_start_args(p_cell_start)
    p_cell_start.set_defaults(func=cmd_start)
    p_status = sub.add_parser("status", help="print service status as JSON")
    p_status.set_defaults(func=cmd_status)
    p_readiness = sub.add_parser(
        "installer-readiness",
        help="emit the plugin-owned installer/readiness contract state as JSON",
    )
    p_readiness.set_defaults(func=cmd_installer_readiness)
    p_version = sub.add_parser("version", help="print the running or local version")
    p_version.set_defaults(func=cmd_version)
    p_mcp = sub.add_parser("mcp", help="run the discoverable MCP toolset over stdio")
    p_mcp.set_defaults(func=cmd_mcp)
    p_stop = sub.add_parser("stop", help="stop the active service process")
    p_stop.set_defaults(func=cmd_stop)

    p_deploy = sub.add_parser("deploy", help="zero-downtime active/passive cutover")
    p_deploy.add_argument("--health-timeout", type=float, default=60.0)
    p_deploy.add_argument("--drain-timeout", type=float, default=300.0)
    p_deploy.add_argument("--force", action="store_true")
    p_deploy.add_argument("--recover", action="store_true")
    p_deploy.add_argument("--json", action="store_true")
    p_deploy.set_defaults(func=cmd_start)

    p_index = sub.add_parser("index", help="populate or refresh the durable index")
    p_index.add_argument("--source", help="source name to index instead of configured defaults")
    p_index.add_argument("--full", action="store_true", help="run a full reindex")
    p_index.set_defaults(func=cmd_index)

    # Internal: run a single queued task in a detached versioned worker process
    # (spawned by the service TaskRunner; not part of the public surface).
    p_worker = sub.add_parser("index-worker", help=argparse.SUPPRESS)
    p_worker.add_argument("--task", required=True, help="task id to run")
    p_worker.set_defaults(func=cmd_index_worker)

    p_search = sub.add_parser("search", help="search the durable index")
    p_search.add_argument("query")
    p_search.add_argument("--source", help="filter by source")
    p_search.add_argument("--language", help="filter by language")
    p_search.add_argument("--repo", help="filter by repository metadata")
    p_search.add_argument("--limit", type=int, default=10, help="maximum hits to return")
    p_search.add_argument(
        "--json",
        action="store_true",
        help="emit JSON even when stdout is a TTY",
    )
    p_search.set_defaults(func=cmd_search)

    p_similar = sub.add_parser("similar", help="find chunks similar to an indexed chunk")
    p_similar.add_argument("chunk_id")
    p_similar.add_argument("--limit", type=int, default=10, help="maximum hits to return")
    p_similar.add_argument("--source", help="filter by source")
    p_similar.set_defaults(func=cmd_similar)

    p_clusters = sub.add_parser(
        "clusters", help="list similarity clusters of near-duplicate items"
    )
    p_clusters.add_argument("--source", help="scope to a source (collapsed to its bucket)")
    p_clusters.add_argument("--bucket", help="explicit bucket (e.g. git, gitea:issues)")
    p_clusters.add_argument("--model", help="embedding space (code or prose)")
    p_clusters.add_argument(
        "--exact-dupes-only",
        dest="exact_dupes_only",
        action="store_true",
        help="only clusters that contain a byte-identical pair",
    )
    p_clusters.add_argument("--limit", type=int, default=50, help="maximum clusters to return")
    p_clusters.set_defaults(func=cmd_clusters)

    p_engine = sub.add_parser(
        "engine", help="manage the durable, persistent embedding-engine daemon"
    )
    p_engine.add_argument(
        "engine_action",
        choices=["start", "stop", "status", "run"],
        help="start/stop/status the daemon, or run it in the foreground (task entry)",
    )
    p_engine.set_defaults(func=cmd_engine)

    p_role = sub.add_parser(
        "role",
        help="print the resolved agent-index role (host/client/unconfigured)",
    )
    p_role.add_argument("--json", action="store_true", help="emit the role as JSON")
    p_role.set_defaults(func=cmd_role)

    p_setup = sub.add_parser(
        "setup", help="adopt agent-index: designate the indexer + write role config"
    )
    p_setup.add_argument("--indexer", help="machine designated as the indexer (host)")
    p_setup.add_argument(
        "--single", action="store_true", help="single-machine: this box hosts everything"
    )
    p_setup.add_argument("--ssh", help="SSH alias clients use to reach the indexer")
    p_setup.add_argument("--endpoint", help="explicit service endpoint URL for clients")
    p_setup.add_argument("--repo", help="harness repo root (default: AGENT_INDEX_REPO or git cwd)")
    p_setup.add_argument("--force", action="store_true", help="override the underpowered-indexer hard block")
    p_setup.add_argument("--yes", action="store_true", help="non-interactive (no prompts)")
    p_setup.add_argument("--json", action="store_true", help="emit the outcome as JSON")
    p_setup.set_defaults(func=cmd_setup)

    p_cap = sub.add_parser(
        "capability", help="report this host's capabilities + the engine device it would use"
    )
    p_cap.add_argument("--json", action="store_true", help="emit the decision as JSON")
    p_cap.set_defaults(func=cmd_capability)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not hasattr(args, "func"):
        args = parser.parse_args(["status"])

    # Project-aware, role-routed transport: a client runs the read subcommands on
    # the designated indexer host over SSH (see transport.py). The host (or a
    # no-project fallback whose machine-global role is host) runs locally.
    from . import transport

    sub = getattr(args, "command", None)
    raw = list(sys.argv[1:] if argv is None else argv)
    if sub is None:
        sub, raw = "status", ["status"]
    if sub == "status":
        role, _indexer = transport.plan_route()
        if role == "unconfigured":
            return int(args.func(args))
    if sub in transport.DELEGABLE:
        rc = transport.maybe_delegate(sub, raw)
        if rc is not None:
            return rc

    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
