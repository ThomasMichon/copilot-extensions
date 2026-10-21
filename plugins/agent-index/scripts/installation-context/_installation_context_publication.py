# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

from contextlib import ExitStack, contextmanager
from collections.abc import Iterator

RUNTIME_PUBLICATION_ENV = "COPILOT_EXTENSIONS_RUNTIME_PUBLICATION"


def _runtime_publication_identity(
    arguments: Mapping[str, Any],
) -> tuple[dict[str, Any], dict[str, Any]]:
    if not arguments.get("context") or arguments.get("policy_path") is not None:
        _fail("Runtime publication requires an explicit context and canonical installation policy.")
    decision = resolve_runtime_deployment(**arguments)
    policy = decision["deployment"]
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
        if (
            json.dumps(owner.get("runtimePublication"), sort_keys=True)
            != json.dumps(purpose, sort_keys=True) or owner["host"] != lock.host
            or not _pid_is_live(owner["pid"])
        ):
            _fail("Runtime publication management owner is stale, foreign or has another purpose.")
        owners.append(owner)
    if owners[0]["pid"] != owners[1]["pid"]:
        _fail("Runtime publication management locks have different owners.")
    if _runtime_publication_identity(arguments)[0] != identity:
        _fail("Runtime publication installation changed while borrowing custody.")
    for lock, owner in zip(locks, owners):
        if dict(lock._owner()) != dict(owner):
            _fail("Runtime publication management owner changed while borrowing custody.")
    return locks


@contextmanager
def runtime_publication_lease(
    *, timeout_seconds: float = LOCK_INITIALIZATION_GRACE_SECONDS, **arguments: Any,
) -> Iterator[dict[str, str]]:
    """Hold existing namespace locks for explicit, caller-owned runtime installation."""
    identity, receipt = _runtime_publication_identity(arguments)
    deadline = time.monotonic() + timeout_seconds
    with ExitStack() as stack:
        locks = _runtime_publication_locks(receipt, arguments.get("durable_home"))
        for lock in locks:
            lock.timeout_seconds = max(0, deadline - time.monotonic())
            stack.enter_context(lock)
        if _runtime_publication_identity(arguments)[0] != identity:
            _fail("Runtime publication installation changed before management admission.")
        purpose = {"version": 1, "origin": "management", "operation": "publish", **identity}
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
