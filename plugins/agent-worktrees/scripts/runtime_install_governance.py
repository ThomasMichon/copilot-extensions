"""Installation authority and lock ownership for the Worktrees installer."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path


SCRIPTS = Path(__file__).resolve().parent


class InstallationRefused(RuntimeError):
    """Installation authority or a completed transition could not be proved."""


class Governance:
    def __init__(self, root: Path, payload: Path):
        self.root = root.resolve()
        self.payload = payload.resolve(strict=True)
        helper = SCRIPTS / "installation-context" / "installation_context.py"
        spec = importlib.util.spec_from_file_location("_worktrees_installation_context", helper)
        if spec is None or spec.loader is None:
            raise ImportError(f"Installation-context helper unavailable: {helper}")
        self.helper = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.helper
        spec.loader.exec_module(self.helper)
        self.context = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
        self.project_root = (
            Path(os.environ["COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT"]).resolve()
            if os.environ.get("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE")
            and os.environ.get("COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT")
            else None
        )
        self.durable_home = (
            Path(self.context).parents[4] if self.context else
            Path(os.environ.get("USERPROFILE") or Path.home())
            / ".copilot-extensions"
        )
        self.initial = self.read()
        self.publication_locks = ()
        self.command_lock = None
        self.retained_binding = None

    def read(self) -> tuple[dict, dict | None]:
        arguments = {
            "legacy_root": self.root if not self.context else Path.home() / ".agent-worktrees",
            "payload_root": self.payload,
            "plugin_id": "agent-worktrees",
            "durable_home": self.durable_home,
        }
        if not self.context:
            arguments["project_root"] = self.project_root
            state = self.helper.probe_legacy_entrypoint(**arguments)
            if state.get("allowMutation") is not True:
                raise InstallationRefused(
                    f"Legacy runtime mutation refused: {state.get('probeReason')}"
                )
            self._publication(arguments)
            return state, None
        receipt = self.helper.validate_context_receipt(
            self.context, self.durable_home,
            expected_plugin_id="agent-worktrees", expected_payload_root=self.payload,
        )
        state = self.helper.resolve_installation_mode(context=self.context, **arguments)
        if (
            Path(receipt["pluginRoot"]).resolve() != self.root
            or state.get("actualMode") != "namespaced"
            or state.get("status") not in ("ready", "deactivation-required")
            or Path(state["runtimeRoot"]).resolve() != self.root
            or Path(state["context"]).resolve() != Path(self.context).resolve()
            or state.get("installGeneration") != receipt["generation"]
        ):
            raise InstallationRefused("Installation context does not authorize this runtime root")
        self._publication({**arguments, "context": self.context})
        return state, receipt

    def _publication(self, arguments: dict) -> None:
        decision = self.helper.resolve_runtime_deployment(**arguments)
        actual_root = decision["runtimeRoot"] or decision["legacy"]["root"]
        if Path(actual_root).resolve() != self.root:
            raise InstallationRefused("Runtime deployment policy names another root")
        policy = decision["deployment"]
        if self.context and os.environ.get(self.helper.RUNTIME_PUBLICATION_ENV):
            self.helper.borrow_runtime_publication(**arguments)
            return
        if policy["allowPluginPublication"] is True:
            return
        if not self.context and policy["state"] == "valid" and policy["managed"] is True:
            import command_binding as binding

            lease = binding._load_lifecycle().borrowed_lease(
                binding._rules(), self.helper, payload_root=self.payload,
                plugin_id="agent-worktrees", legacy_root=self.root,
                project_root=self.project_root,
            )
            if lease is not None and lease.purpose["origin"] == "management":
                return
        raise InstallationRefused(
            f"Runtime publication refused: {policy['reason']}. "
            f"{policy['diagnostic'] or ''} {policy['recovery'] or ''}".strip()
        )

    def recheck(self) -> None:
        state, receipt = self.read()
        previous, previous_receipt = self.initial
        keys = (
            "actualMode", "desiredMode", "status", "reason", "runtimeRoot",
            "context", "activationGeneration", "installGeneration", "allowMutation",
        )
        if any(state.get(key) != previous.get(key) for key in keys):
            raise InstallationRefused("Installation governance changed during construction")
        if receipt is not None and any(
            receipt[key] != previous_receipt[key]
            for key in ("marketplaceId", "namespaceGeneration", "generation", "pluginRoot")
        ):
            raise InstallationRefused("Installation receipt changed during construction")

    def signature(self) -> dict:
        state, receipt = self.initial
        return {
            "state": {key: state.get(key) for key in (
                "actualMode", "desiredMode", "status", "reason", "runtimeRoot",
                "context", "activationGeneration", "installGeneration", "allowMutation",
            )},
            "receipt": None if receipt is None else {key: receipt[key] for key in (
                "marketplaceId", "namespaceGeneration", "generation", "pluginRoot",
            )},
        }

    @contextmanager
    def manifest_publication(self, content: bytes):
        if self.context:
            yield content
            return
        import command_binding as binding

        if self.retained_binding is not None:
            ownership = binding.retained_legacy_binding(
                self.helper, self.command_lock, payload_root=self.payload,
                plugin_id="agent-worktrees", legacy_root=self.root,
                project_root=self.project_root,
            )
            if ownership != self.retained_binding:
                raise InstallationRefused("Retained command ownership changed during restoration")
            manifest = json.loads(content)
            if (
                manifest.get("schema_version") != 3
                or manifest.get("service") != "agent-worktrees"
                or "commandBinding" in manifest
                or "commandLifecycle" in manifest
            ):
                raise InstallationRefused("Restoration requires an unbound Worktrees manifest")
            yield (json.dumps({**manifest, **ownership}, indent=2) + "\n").encode("utf-8")
            self.command_lock.assert_owned()
            return
        with binding.legacy_manifest_publication(
            self.helper, json.loads(content), payload_root=self.payload,
            plugin_id="agent-worktrees", legacy_root=self.root,
            project_root=self.project_root,
        ) as (manifest, result, _lock):
            if result["status"] == "unresolved":
                print(
                    f"Command ownership was not published: {result['reason']}",
                    file=sys.stderr, flush=True,
                )
            yield (json.dumps(manifest, indent=2) + "\n").encode("utf-8")

    @contextmanager
    def held(self, *, timeout: float = 180):
        if self.publication_locks or self.command_lock is not None:
            for lock in self.publication_locks:
                lock.assert_owned()
            if self.command_lock is not None:
                self.command_lock.assert_owned()
            self.recheck()
            yield
            return
        deadline = time.monotonic() + timeout
        with ExitStack() as stack:
            locks = []
            command_lock = None
            receipt = self.initial[1]
            retained = self._retained_bootstrap()
            if retained:
                import command_binding as binding

                arguments = {
                    "payload_root": self.payload, "plugin_id": "agent-worktrees",
                    "legacy_root": self.root, "project_root": self.project_root,
                }
                command_lock = stack.enter_context(binding._load_lifecycle()._lock(
                    self.helper, arguments,
                    timeout_seconds=max(0, deadline - time.monotonic()),
                ))
                ownership = binding.retained_legacy_binding(self.helper, command_lock, **arguments)
            elif receipt is None:
                import command_binding as binding

                lease = stack.enter_context(binding._load_lifecycle().legacy_lifecycle(
                    binding._rules(), self.helper, reserve=False,
                    payload_root=self.payload, plugin_id="agent-worktrees",
                    legacy_root=self.root, project_root=self.project_root,
                    lock_timeout=max(0, deadline - time.monotonic()),
                ))
                command_lock = lease.lock
            elif receipt is not None and os.environ.get(self.helper.RUNTIME_PUBLICATION_ENV):
                locks.extend(self.helper.borrow_runtime_publication(
                    legacy_root=Path.home() / ".agent-worktrees", payload_root=self.payload,
                    plugin_id="agent-worktrees", durable_home=self.durable_home,
                    context=self.context,
                ))
            elif receipt is not None:
                marketplace = receipt["marketplaceId"]
                locks.append(stack.enter_context(self.helper._DirectoryLock(
                    self.durable_home / "marketplaces" / ".locks" / f"{marketplace}.genesis",
                    kind="genesis", marketplace_id=marketplace,
                    timeout_seconds=max(0, deadline - time.monotonic()),
                )))
                locks.append(stack.enter_context(self.helper._DirectoryLock(
                    Path(receipt["cellRoot"]) / ".locks" / "agent-worktrees.install.lock",
                    kind="install", marketplace_id=marketplace, plugin_id="agent-worktrees",
                    timeout_seconds=max(0, deadline - time.monotonic()),
                )))
            self.recheck()
            previous = self.publication_locks
            self.publication_locks = tuple(locks)
            previous_command_lock = self.command_lock
            self.command_lock = command_lock
            previous_binding = self.retained_binding
            if retained:
                self.retained_binding = ownership
            try:
                yield
            finally:
                self.publication_locks = previous
                self.command_lock = previous_command_lock
                self.retained_binding = previous_binding

    def _retained_bootstrap(self) -> bool:
        if self.context:
            return False
        slot = Path(sys.prefix).resolve()
        if (
            slot.parent != self.root / "versions"
            or self.payload != slot / ".runtime-bootstrap"
            or SCRIPTS != self.payload
        ):
            return False
        import versioned_runtime

        if versioned_runtime.read_marker(self.root, slot.name) is None:
            raise InstallationRefused("Retained restoration requires a completed control slot")
        manifest = self.helper.read_deployment_manifest(self.root / "deploy-manifest.json")
        return "commandBinding" in manifest
