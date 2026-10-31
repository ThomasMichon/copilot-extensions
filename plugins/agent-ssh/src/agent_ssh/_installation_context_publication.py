# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from collections.abc import Iterator

RUNTIME_PUBLICATION_ENV = "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION"


def _runtime_publication_process_identity(pid: int) -> str | None:
    """Prove a live native birth, not conservative PID existence."""
    if type(pid) is not int or pid < 1:
        return None
    if sys.platform == "linux":
        try:
            record = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
            boot = Path("/proc/sys/kernel/random/boot_id").read_text(encoding="ascii").strip()
        except (OSError, UnicodeError):
            return None
        closing = record.rfind(")")
        fields = record[closing + 1:].split()
        if (
            closing < 0 or len(fields) < 20
            or fields[0] not in {"R", "S", "D", "T", "t", "W", "K", "P", "I"}
            or not fields[19].isdigit() or int(fields[19]) < 1
            or re.fullmatch(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", boot) is None
        ):
            return None
        return f"linux:{boot}:{fields[19]}"
    if os.name != "nt" or pid > 0xFFFFFFFF:
        return None
    import ctypes
    from ctypes import wintypes

    try:
        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel.WaitForSingleObject.restype = wintypes.DWORD
        kernel.GetProcessTimes.argtypes = (wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)] * 4))
        kernel.GetProcessTimes.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel.CloseHandle.restype = wintypes.BOOL
        handle = kernel.OpenProcess(0x00100000 | 0x1000, False, pid)
        if not handle:
            return None
        try:
            if kernel.WaitForSingleObject(handle, 0) != 258:
                return None
            creation, exit_time, kernel_time, user_time = (wintypes.FILETIME() for _ in range(4))
            if not kernel.GetProcessTimes(
                handle, ctypes.byref(creation), ctypes.byref(exit_time),
                ctypes.byref(kernel_time), ctypes.byref(user_time),
            ):
                return None
            birth = (creation.dwHighDateTime << 32) | creation.dwLowDateTime
            return f"windows:{birth}" if birth else None
        finally:
            kernel.CloseHandle(handle)
    except (OSError, ctypes.ArgumentError):
        return None


def _runtime_publication_identity(
    arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not arguments.get("context") or arguments.get("policy_path") is not None:
        _fail("Runtime publication requires an explicit context and canonical installation policy.")
    decision = resolve_runtime_deployment(**arguments)
    policy = decision["deployment"]
    if "runtimeProfiles" in policy:
        _fail(RUNTIME_PROFILE_RECOVERY)
    if (
        decision["status"] not in {"ready", "deactivation-required"}
        or decision["actualMode"] != "namespaced"
        or policy["state"] not in {"missing", "valid"}
    ):
        _fail(
            f"Runtime publication refused: {policy['reason']}; "
            f"{policy['diagnostic'] or decision['reason']}"
        )
    receipt = validate_context_receipt(
        arguments["context"],
        arguments.get("durable_home") or Path(arguments["context"]).parents[4],
        expected_plugin_id=decision["pluginId"],
        expected_payload_root=arguments.get("payload_root"),
        environment=arguments.get("environment"),
    )
    if (
        canonical_path(decision["runtimeRoot"]) != canonical_path(receipt["pluginRoot"])
        or decision["installGeneration"] != receipt["generation"]
    ):
        _fail("Runtime publication context differs from installation governance.")
    identity = {
        "owner": _deployment_owner(decision, arguments),
        "context": str(canonical_path(arguments["context"])),
        "namespaceGeneration": receipt["namespaceGeneration"],
        "installGeneration": receipt["generation"],
        "activationGeneration": decision["activationGeneration"],
    }
    return identity, receipt


def _runtime_publication_locks(
    receipt: Mapping[str, Any], durable_home: str | os.PathLike[str] | None,
    *, timeout_seconds: float = LOCK_INITIALIZATION_GRACE_SECONDS,
) -> tuple[_DirectoryLock, _DirectoryLock]:
    cell = Path(receipt["cellRoot"])
    durable = canonical_path(durable_home or cell.parents[1])
    marketplace, plugin = receipt["marketplaceId"], receipt["pluginId"]
    return (
        _DirectoryLock(
            durable / "marketplaces" / ".locks" / f"{marketplace}.genesis",
            kind="genesis", marketplace_id=marketplace, timeout_seconds=timeout_seconds,
        ),
        _DirectoryLock(
            cell / ".locks" / f"{plugin}.install.lock", kind="install",
            marketplace_id=marketplace, plugin_id=plugin, timeout_seconds=timeout_seconds,
        ),
    )


@_validation_scope
def borrow_runtime_publication(**arguments: Any) -> tuple[_DirectoryLock, _DirectoryLock]:
    """Validate live management custody; returned locks must never be released by a borrower."""
    identity, receipt = _runtime_publication_identity(arguments)
    environment = arguments.get("environment")
    environment = os.environ if environment is None else environment
    try:
        ticket = json.loads(
            environment.get(RUNTIME_PUBLICATION_ENV, ""), object_pairs_hook=_reject_duplicate_keys,
        )
    except (ValueError, TypeError):
        _fail("Runtime publication requires a valid management custody ticket.")
    if (
        not isinstance(ticket, dict) or set(ticket) != {"version", "tokens"}
        or type(ticket["version"]) is not int or ticket["version"] != 1
        or not isinstance(ticket["tokens"], list) or len(ticket["tokens"]) != 2
        or any(not isinstance(token, str) or re.fullmatch(r"[0-9a-f]{32}", token) is None
               for token in ticket["tokens"])
    ):
        _fail("Runtime publication management custody ticket is invalid.")
    locks = _runtime_publication_locks(receipt, arguments.get("durable_home"))
    owners = []
    purpose = {"version": 1, "origin": "management", "operation": "publish", **identity}
    for lock, token in zip(locks, ticket["tokens"]):
        lock.token, lock.acquired = token, True
        lock.assert_owned()
        owner = lock._owner()
        process_identity = _runtime_publication_process_identity(owner["pid"])
        if (
            process_identity is None
            or json.dumps(owner.get("runtimePublication"), sort_keys=True)
            != json.dumps({**purpose, "processIdentity": process_identity}, sort_keys=True)
            or owner["host"] != lock.host
        ):
            _fail("Runtime publication management owner is stale, foreign or has another purpose.")
        owners.append(owner)
    if owners[0]["pid"] != owners[1]["pid"]:
        _fail("Runtime publication management locks have different owners.")
    if _runtime_publication_identity(arguments)[0] != identity:
        _fail("Runtime publication installation changed while borrowing custody.")
    for lock, owner in zip(locks, owners):
        if (
            dict(lock._owner()) != dict(owner)
            or _runtime_publication_process_identity(owner["pid"])
            != owner["runtimePublication"]["processIdentity"]
        ):
            _fail("Runtime publication management owner changed while borrowing custody.")
    return locks


@contextmanager
def runtime_publication_lease(
    *, timeout_seconds: float = LOCK_INITIALIZATION_GRACE_SECONDS, **arguments: Any,
) -> Iterator[dict[str, str]]:
    """Hold existing namespace locks for explicit, caller-owned runtime installation."""
    identity, receipt = _runtime_publication_identity(arguments)
    process_identity = _runtime_publication_process_identity(os.getpid())
    if process_identity is None:
        _fail("Runtime publication management process identity is unavailable.")
    deadline = time.monotonic() + timeout_seconds
    with ExitStack() as stack:
        locks = _runtime_publication_locks(receipt, arguments.get("durable_home"))
        for lock in locks:
            lock.timeout_seconds = max(0, deadline - time.monotonic())
            stack.enter_context(lock)
        if _runtime_publication_identity(arguments)[0] != identity:
            _fail("Runtime publication installation changed before management admission.")
        purpose = {
            "version": 1, "origin": "management", "operation": "publish",
            **identity, "processIdentity": process_identity,
        }
        for lock in locks:
            lock.assert_owned()
            _atomic_write_json(
                lock.owner_path, {**lock._owner(), "runtimePublication": purpose},
            )
        patch = {
            "COPILOT_EXTENSIONS_CONTEXT": identity["context"],
            RUNTIME_PUBLICATION_ENV: json.dumps({
                "version": 1, "tokens": [lock.token for lock in locks],
            }, separators=(",", ":")),
        }
        environment = arguments.get("environment")
        environment = os.environ if environment is None else environment
        borrowed_arguments = {
            **arguments, "environment": {**environment, **patch},
        }
        borrow_runtime_publication(**borrowed_arguments)
        yield patch
        borrow_runtime_publication(**borrowed_arguments)
