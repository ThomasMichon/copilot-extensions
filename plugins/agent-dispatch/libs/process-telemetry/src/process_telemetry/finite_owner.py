"""One finite tool owner for a single command route admitted by local policy.

A short CLI invocation (create a PR, launch a remote worker) owns exactly one
`CommandRoutes` lifetime: admit the operator's explicitly configured root,
offer closed observations, then close within the contract's two-second
shutdown budget.  Absence of the policy file keeps the explicitly unbound
legacy behavior: nothing is offered and no receiver starts.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .command_config import CAPABILITY, read_object
from .emission import LEGACY, SinkSlot

POLICY_FILE = "consumer-telemetry.json"


def capability(source_schemas: list[str]) -> dict[str, Any]:
    """Describe the executing provider's own schemas, not every registered one."""
    return {
        **{key: list(value) if type(value) is list else value for key, value in CAPABILITY.items()},
        "source_schemas": list(source_schemas),
    }


def load_policy(directory: Path) -> Any:
    """Read only the exact owner-local admission file; absence is legacy."""
    path = Path(directory) / POLICY_FILE
    try:
        value = read_object(path, 16384)
        if value is not None:
            return value
        try:
            path.lstat()
        except FileNotFoundError:
            return LEGACY
        return {}
    except (OSError, ValueError, UnicodeError, TypeError, RecursionError):
        return {}


def validate_policy(value: Any) -> str:
    """Return the admitted absolute root; the policy names nothing else."""
    if type(value) is not dict or set(value) != {"schema_version", "root"}:
        raise ValueError("invalid admission policy")
    if type(value["schema_version"]) is not int or value["schema_version"] != 1:
        raise ValueError("invalid admission version")
    root = value["root"]
    if type(root) is not str or not root or len(root) > 4096 or not Path(root).is_absolute():
        raise ValueError("invalid admission root")
    return root


class FiniteOwner:
    """Owner-local admission, offer and bounded close for one finite run."""

    def __init__(self, policy: Any, *, source: str, source_schema: str) -> None:
        self.legacy = policy is LEGACY
        self.source = source
        self.source_schema = source_schema
        self.refusal: str | None = None
        self.owner: Any = None
        self.binding: Any = None
        self._root = ""
        if self.legacy:
            return
        try:
            self._root = validate_policy(policy)
        except ValueError:
            self.refusal = "invalid_config"

    @classmethod
    def from_directory(cls, directory: Path, *, source: str, source_schema: str) -> FiniteOwner:
        return cls(load_policy(directory), source=source, source_schema=source_schema)

    @property
    def active(self) -> bool:
        return self.binding is not None and self.binding.state == "active"

    def start(self) -> FiniteOwner:
        if self.legacy or self.refusal is not None or self.owner is not None:
            return self
        try:
            self.owner = SinkSlot().configure_commands(
                source=self.source, source_schema=self.source_schema,
            )
            self.binding = self.owner.admit(self._root)
        except Exception:
            self.refusal = "unsupported_contract"
            self.close()
        return self

    def emit(self, event: dict[str, Any]) -> None:
        """Nonblocking offer; product behavior never depends on its result."""
        if self.owner is not None and self.binding is not None:
            self.owner.slot.emit(event, binding=self.binding)

    def close(self) -> None:
        if self.owner is not None:
            try:
                self.owner.close()
            except Exception:
                self.refusal = "cleanup_unconfirmed"

    def status(self) -> dict[str, Any]:
        """Content-free local status; never argv, paths or record contents."""
        return {
            "legacy": self.legacy, "refusal": self.refusal,
            "state": self.binding.state if self.binding is not None else None,
            "counters": self.owner.stats() if self.owner is not None else {},
        }

    def __enter__(self) -> FiniteOwner:
        return self.start()

    def __exit__(self, *_exc: object) -> None:
        self.close()
