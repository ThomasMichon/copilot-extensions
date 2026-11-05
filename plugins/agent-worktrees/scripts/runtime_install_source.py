"""Connect the retained source authority to the native Worktrees lifecycle."""
from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path


def install_source_runtime(plan, *, frontend=None, associated=None, **options):
    """Keep the authenticated source and feed owner alive through cutover."""
    import runtime_install as native

    if Path(native.__file__).resolve() != Path(__file__).with_name("runtime_install.py").resolve():
        raise ValueError("Worktrees source mode needs its own native lifecycle")
    from runtime_source_build import retain_source_runtime_inputs

    policy = plan.policy()
    owner = policy.get("owner") or {}
    if (
        policy.get("status") != "ready" or owner.get("pluginId") != "agent-worktrees"
        or plan.release_selection is not None
        or plan.source_trigger not in (
            "release-index-unavailable", "artifact-unavailable", "approved-profile-unavailable",
        )
        or policy.get("selection", {}).get("allowSourceFallback") is not True
        or options.get("component") != "agent-worktrees" or options.get("extras", ())
    ):
        raise ValueError("Worktrees source mode needs its own selected installation authority")
    root = Path(owner["runtimeRoot"])
    if not root.is_absolute():
        raise ValueError("Worktrees source mode needs its installation-owned runtime root")
    python = Path(options["python"]).resolve(strict=True)
    if python != Path(sys.executable).resolve():
        raise ValueError("The source authority must run under the selected native interpreter")
    uv = Path(shutil.which(str(options["uv"])) or options["uv"]).resolve(strict=True)
    installation = options["installation"]
    payload = Path(installation["payload_root"]).resolve(strict=True)
    descriptor = installation.get("source_descriptor")
    deadline = options["deadline"]
    governance = native.Governance(
        root, payload, **({"source_descriptor": descriptor} if descriptor is not None else {}),
    )
    governance.require_owned_lifecycle()
    selected_options = {**options, "uv": str(uv), "python": python}
    if associated is None:
        retained = retain_source_runtime_inputs(plan, **selected_options)
    else:
        from runtime_source_build import retain_associated_source_runtime_inputs

        retained = retain_associated_source_runtime_inputs(
            plan, associated=associated, **selected_options,
        )
    with retained as source:
        def verify():
            governance.require_owned_lifecycle()
            source.verify()

        verify()
        receipt = source.wheels.receipt()
        binding = receipt["binding"]
        locked = binding.get("source_lock")
        profile = source.profile.record()
        if (
            source.identity.plugin != "agent-worktrees" or not isinstance(locked, dict)
            or binding.get("profile") != profile or locked.get("policy") != policy
            or locked.get("source") != source.identity.document()
            or locked.get("trigger") != plan.source_trigger
            or locked.get("component") != "agent-worktrees" or locked.get("extras") != []
            or locked.get("target") != profile.get("target")
        ):
            raise ValueError("Retained Worktrees source differs from its installation owner")
        packages = native.engine._package_roots(source.payload)
        local = tuple(sorted(
            native.engine._package_name(package) for package in packages
            if native.engine._package_name(package) in receipt["distributions"]
        ))
        if (
            local != source.local_packages
            or native.engine._package_name(source.payload) not in local
        ):
            raise ValueError("Retained Worktrees wheels differ from their local source projects")
        identity = native.runtime_source.manifest_fields(source.identity)
        selection = {
            "identity": identity, "source_descriptor": descriptor,
            "runtime_version": source.runtime_version,
            "payload_hash": native.engine._wheel_construction_hash(
                native.engine._construction_hash(native.engine._source_files(packages), (), local),
                source.wheels,
            ),
            "extras": [], "local_packages": list(local),
        }
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise ValueError("Worktrees source authority expired before construction")
        result = native.install(
            root, source.payload, payload=payload, uv=uv, base_python=python,
            wait_timeout=remaining, child_timeout=remaining,
            expected_source_identity=identity, frontend=frontend, wheel_inputs=source.wheels,
            source_descriptor=descriptor, expected_wheel_selection=selection,
            _before_publish=verify,
        )
        governance.require_owned_lifecycle(require_pending=False)
        source.verify()
        return result
