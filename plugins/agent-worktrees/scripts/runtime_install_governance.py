"""Installation authority and lock ownership for the Worktrees installer."""

from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
from contextlib import ExitStack, contextmanager
from pathlib import Path
import runtime_input_verifier


SCRIPTS = Path(__file__).resolve().parent


class InstallationRefused(RuntimeError):
    """Installation authority or a completed transition could not be proved."""


class Governance:
    def __init__(
        self, root: Path, payload: Path, *, source_descriptor: dict | None = None,
        runtime_profile_policy: dict | None = None,
        runtime_profile_mode: str = "source",
        runtime_input_verification: dict | None = None, verify_inputs=None,
        prepare_only: bool = False,
    ):
        self.root = root.resolve()
        self.payload = payload.resolve(strict=True)
        helper = SCRIPTS / "installation-context" / "installation_context.py"
        spec = importlib.util.spec_from_file_location("_worktrees_installation_context", helper)
        if spec is None or spec.loader is None:
            raise ImportError(f"Installation-context helper unavailable: {helper}")
        self.helper = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = self.helper
        spec.loader.exec_module(self.helper)
        if source_descriptor is not None:
            normalized = self.helper.normalize_source(source_descriptor)
            source_descriptor = {
                "kind": normalized.kind, "canonical": normalized.canonical, "ref": normalized.ref,
            }
        self.source_descriptor = source_descriptor
        self.runtime_profile_policy = (
            json.loads(json.dumps(runtime_profile_policy, allow_nan=False))
            if runtime_profile_policy is not None else None
        )
        if runtime_profile_mode not in ("source", "artifact"):
            raise InstallationRefused("Unsupported Worktrees runtime profile mode")
        self.runtime_profile_mode = runtime_profile_mode
        self.runtime_input_verification = (
            runtime_input_verifier._record(runtime_input_verification)
            if runtime_input_verification is not None else None
        )
        self.prepare_only = prepare_only
        self.verify_inputs = verify_inputs
        if self.runtime_input_verification is not None and verify_inputs is None and not prepare_only:
            self.verify_inputs = runtime_input_verifier.current_client(
                self.runtime_input_verification,
            ).check
        self.binding_options = (
            {"mode_arguments": {"source_descriptor": source_descriptor}}
            if source_descriptor is not None else {}
        )
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
            **({"source_descriptor": self.source_descriptor}
               if self.source_descriptor is not None else {}),
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
        if not self.prepare_only:
            self._verify_inputs()
        self._profile_policy(arguments)
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
        if (
            policy["state"] == "valid" and policy["managed"] is False
            and policy["reason"] == "runtime-profile-consumer-required"
            and self.runtime_profile_policy is not None
            and self.runtime_input_verification is not None
            and (self.prepare_only or callable(self.verify_inputs))
        ):
            return
        if not self.context and policy["state"] == "valid" and policy["managed"] is True:
            import command_binding as binding

            lease = binding._load_lifecycle().borrowed_lease(
                binding._rules(), self.helper, payload_root=self.payload,
                plugin_id="agent-worktrees", legacy_root=self.root,
                project_root=self.project_root,
                **self.binding_options,
            )
            if lease is not None and lease.purpose["origin"] == "management":
                return
        raise InstallationRefused(
            f"Runtime publication refused: {policy['reason']}. "
            f"{policy['diagnostic'] or ''} {policy['recovery'] or ''}".strip()
        )

    def _profile_policy(self, arguments: dict) -> None:
        expected = self.runtime_profile_policy
        if expected is None:
            return
        observed = self.helper.resolve_runtime_profile_policy(
            **arguments, backend=expected.get("selection", {}).get("backend"),
        )["runtimeProfiles"]
        if (
            expected.get("owner", {}).get("pluginId") != "agent-worktrees"
            or observed.get("status") != "ready" or observed != expected
            or Path(observed["owner"]["runtimeRoot"]).resolve() != self.root
            or (
                getattr(self, "runtime_profile_mode", "source") == "source"
                and observed["selection"]["allowSourceFallback"] is not True
            )
        ):
            raise InstallationRefused("Worktrees runtime profile policy changed or names another owner")

    def bind_runtime_profile(self, policy: dict) -> None:
        """Bind producer evidence only after checking this actual installation."""
        self.runtime_profile_policy = json.loads(json.dumps(policy, allow_nan=False))
        if self.runtime_profile_policy is not None:
            self.recheck()

    def recheck(self) -> None:
        if getattr(self, "prepare_only", False):
            raise InstallationRefused("Helper preparation cannot authorize runtime publication")
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
            **({"source_descriptor": self.source_descriptor}
               if self.source_descriptor is not None else {}),
            **({"runtime_profile_policy": self.runtime_profile_policy}
               if self.runtime_profile_policy is not None else {}),
            **({"runtime_profile_mode": self.runtime_profile_mode}
               if self.runtime_profile_policy is not None and self.runtime_profile_mode != "source" else {}),
            **({"runtime_input_verification": self.runtime_input_verification}
               if self.runtime_input_verification is not None else {}),
        }

    def _verify_inputs(self) -> None:
        if getattr(self, "runtime_input_verification", None) is not None:
            if not callable(self.verify_inputs):
                raise InstallationRefused("Runtime publication has no live input-verifier owner")
            self.verify_inputs()

    @contextmanager
    def _legacy_profile_authority(self):
        if self.context or self.runtime_profile_policy is None:
            yield
            return
        if self.runtime_input_verification is None or not callable(self.verify_inputs):
            raise InstallationRefused("Profile publication requires a live input-verifier owner")
        import command_binding as binding

        scope = getattr(binding._load_lifecycle(), "profile_publication_authority", None)
        if not callable(scope):
            raise InstallationRefused(
                "Selected lifecycle helper does not support profile publication authority"
            )
        arguments = {
            "legacy_root": self.root, "payload_root": self.payload,
            "plugin_id": "agent-worktrees", "project_root": self.project_root,
            "durable_home": self.durable_home,
            **({"source_descriptor": self.source_descriptor}
               if self.source_descriptor is not None else {}),
        }
        with scope(
            runtime_root=self.root, plugin_id="agent-worktrees",
            authorize=lambda: self._profile_policy(arguments),
        ):
            yield

    @contextmanager
    def manifest_publication(self, content: bytes):
        if getattr(self, "prepare_only", False):
            raise InstallationRefused("Helper preparation cannot publish a runtime manifest")
        self._verify_inputs()
        if self.context:
            yield content
            return
        import command_binding as binding

        with self._legacy_profile_authority():
            if self.retained_binding is not None:
                ownership = binding.retained_legacy_binding(
                    self.helper, self.command_lock, payload_root=self.payload,
                    plugin_id="agent-worktrees", legacy_root=self.root,
                    project_root=self.project_root,
                    **self.binding_options,
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
                if self.runtime_profile_policy is not None:
                    self.recheck()
                yield (json.dumps({**manifest, **ownership}, indent=2) + "\n").encode("utf-8")
                self.command_lock.assert_owned()
                return
            with binding.legacy_manifest_publication(
                self.helper, json.loads(content), payload_root=self.payload,
                plugin_id="agent-worktrees", legacy_root=self.root,
                project_root=self.project_root,
                **self.binding_options,
            ) as (manifest, result, _lock):
                if result["status"] == "unresolved":
                    print(
                        f"Command ownership was not published: {result['reason']}",
                        file=sys.stderr, flush=True,
                    )
                self.recheck()
                yield (json.dumps(manifest, indent=2) + "\n").encode("utf-8")

    def publication_environment(self) -> dict[str, str]:
        environment = dict(os.environ)
        if not self.context:
            import command_binding as binding

            lifecycle = binding._load_lifecycle()
            lease = lifecycle.borrowed_lease(
                binding._rules(), self.helper, payload_root=self.payload,
                plugin_id="agent-worktrees", legacy_root=self.root,
                project_root=self.project_root, **self.binding_options,
            )
            if lease is None:
                raise InstallationRefused("Helper publication requires its existing borrowed lifecycle")
            environment[lifecycle.TICKET_ENV] = json.dumps(lease.ticket)
        return environment

    @contextmanager
    def held(self, *, timeout: float = 180):
        if getattr(self, "prepare_only", False):
            raise InstallationRefused("Helper preparation cannot acquire publication custody")
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
            stack.enter_context(self._legacy_profile_authority())
            locks = []
            command_lock = None
            receipt = self.initial[1]
            retained = self._retained_bootstrap()
            if retained:
                import command_binding as binding

                arguments = {
                    "payload_root": self.payload, "plugin_id": "agent-worktrees",
                    "legacy_root": self.root, "project_root": self.project_root,
                    **self.binding_options,
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
                    **self.binding_options,
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
        environment = Path(sys.prefix).resolve()
        slot = environment.parent if environment.name == ".venv" else environment
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
