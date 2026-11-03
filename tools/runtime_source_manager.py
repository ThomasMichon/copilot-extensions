"""Repository Manager adapter over the reusable authenticated source builder."""
from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import shutil

from build_toolchain_lock import _query_marker_environment
from runtime_profile import ProfileError
from runtime_source_build import engine, retain_source_runtime_inputs
from runtime_source_authentication import retain_authenticated_source
from runtime_source_lock import dependencies, retain_source_configuration


@contextmanager
def _manager_source_inputs(plan, *, revoked_inputs, **options):
    from runtime_source_reuse import retain_source_runtime_reuse, selected_generation

    root = Path(plan.policy()["owner"]["runtimeRoot"])
    current = selected_generation(root)
    if current and ".profile." in current:
        with retain_authenticated_source(
            plan, installation=options["installation"], deadline=options["deadline"],
        ) as authenticated, retain_source_configuration(
            plan, payload=authenticated.payload, expected_source=authenticated.identity,
            target=options["target"], component=options["component"], uv=options["uv"],
            python=options["python"], extras=options.get("extras", ()), env=options.get("env"),
            deadline=options["deadline"], wheel_witness=True, include_build_tools=True,
            installation=options["installation"],
        ) as configuration, retain_source_runtime_reuse(
            authenticated, configuration, max_receipt_bytes=options["max_snapshot_bytes"],
            max_tags=options["wheel_limits"].max_tags, deadline=options["deadline"],
            revoked_inputs=revoked_inputs,
        ) as existing:
            if existing is not None:
                yield existing
                return
    with retain_source_runtime_inputs(
        plan, **options, **({"revoked_inputs": revoked_inputs} if revoked_inputs is not None else {}),
    ) as source:
        yield source


def install_manager_source_runtime(plan, *, revoked_inputs=None, **options):
    """Keep authenticated inputs alive through the actual Manager lifecycle."""
    from worktree_manager import self_install

    owner = plan.policy()["owner"]
    if owner["pluginId"] != "worktree-manager" or options.get("extras", ()):
        raise ProfileError("Manager source installation requires its base installation owner")
    if options.get("uv") is not None:
        selected_uv = dependencies._select_uv_executable(options["uv"])
        options["uv"] = str(Path(shutil.which(selected_uv) or selected_uv).resolve(strict=True))
    root = Path(owner["runtimeRoot"]).resolve()
    with _manager_source_inputs(plan, revoked_inputs=revoked_inputs, **options) as source:
        try:
            return _install_manager_inputs(
                source, root, self_install, revoked_inputs, options.get("deadline"), options.get("python"),
                options.get("uv"),
            )
        except engine.HealthRepairRequired:
            if not isinstance(source.wheels, engine.WheelReuseInputs):
                raise
    # A stored receipt cannot build its own repair.
    with retain_source_runtime_inputs(
        plan, **options, **({"revoked_inputs": revoked_inputs} if revoked_inputs is not None else {}),
    ) as source:
        return _install_manager_inputs(
            source, root, self_install, revoked_inputs, options.get("deadline"), options.get("python"),
            options.get("uv"),
        )


def _install_manager_inputs(source, root, self_install, revoked_inputs, deadline, python, uv):
    if source.identity.plugin != "worktree-manager":
        raise ProfileError("Manager source installation has a foreign source identity")

    def verify():
        source.verify()
        if revoked_inputs is not None:
            from runtime_source_reuse import verify_not_revoked

            verify_not_revoked(source.wheels.receipt(), revoked_inputs)

    def validate(slot):
        verify()
        python = engine.runtime.slot_python(root, slot.name)
        if python is None or _query_marker_environment(python, deadline=deadline) != (
            source.profile.record()["target"]["environment"]
        ):
            raise ProfileError("installed Manager interpreter differs from the selected target")

    verify()
    result = self_install.self_install(
        source.payload, root=root, dry_run=False,
        _wheel_inputs=source.wheels, _local_packages=source.local_packages,
        _validate_candidate=validate, _before_publish=verify,
        _raise_candidate_errors=True,
        **({"_base_python": python} if python is not None else {}),
        **({"_uv": Path(uv)} if uv is not None else {}),
    )
    verify()
    return result
