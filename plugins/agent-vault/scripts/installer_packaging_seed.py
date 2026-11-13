"""Restricted stdlib bootstrap for the admission toolkit's Packaging dependency.

The invoked installation owner supplies independently authenticated source and
current policy verification. A source manifest, wheel hash or this helper alone
does not establish that authority. This is not an ordinary installer entrypoint.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass, field
import hashlib
import json
from pathlib import Path
import re
import subprocess
import time
from types import ModuleType
from urllib.parse import unquote, urlsplit

import installer_dependency_policy as dependencies
import installer_engine as engine
from installer_engine_process import ChildLaunchError, run_owned
from installer_native_feed import capture_native_feed_inputs, recheck_native_feed_inputs
from installer_wheel_acquisition import AcquisitionLimits, acquire_wheel_inputs
from installer_wheel_inputs import WheelError, WheelLimits, _check_deadline, inspect_archive
from installer_wheel_snapshot import WheelInstallInputs, _digest, _open, _ordinary, _unchanged


REQUIREMENT = "packaging>=23"
_TARGET_PROBE_TIMEOUT_SECONDS = 30
_PROJECT = (
    '[project]\nname="installer-packaging-seed"\nversion="0"\n'
    'dependencies=[' + json.dumps(REQUIREMENT) + "]\n"
)
_TARGET = (
    "import json,platform,sys,sysconfig;"
    "print(json.dumps(dict(version=list(sys.version_info),"
    "implementation=sys.implementation.name,cache_tag=sys.implementation.cache_tag,"
    "platform=sysconfig.get_platform(),machine=platform.machine(),"
    "base_prefix=sys.base_prefix),sort_keys=True))"
)


@dataclass(frozen=True)
class SeedLimits:
    max_lock_bytes: int
    max_interpreter_bytes: int
    max_tooling_bytes: int

    def __post_init__(self):
        if any(type(value) is not int or value <= 0 for value in vars(self).values()):
            raise WheelError("tooling seed requires explicit positive byte limits")


@dataclass(frozen=True, kw_only=True)
class ReadySeed(engine.ReadyCandidate):
    _verification: Callable[[], None] = field(repr=False, compare=False)

    def verify(self) -> None:
        self._verification()


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _file_identity(path, limit, deadline):
    before = _ordinary(path)
    with _open(path) as stream:
        opened = _unchanged(path, before, stream)
        digest, _ = _digest(stream, limit, deadline)
        _unchanged(path, before, stream, opened=opened)
    return digest


def _tooling_identity(limit, deadline):
    inputs, total = [], 0
    for namespace, root, pattern in (
        ("installer", Path(__file__).resolve().parent, "installer_*.py"),
        ("runtime", Path(engine.runtime.__file__).resolve().parent, "versioned_runtime*.py"),
    ):
        for path in root.glob(pattern):
            _check_deadline(deadline)
            if len(inputs) >= 256:
                raise WheelError("tooling seed source count limit exceeded")
            size = _ordinary(path).st_size
            if size > limit - total:
                raise WheelError("tooling seed source byte limit exceeded")
            inputs.append((namespace + "/" + path.name, path, size, limit - total))
            total += size
    def read(item):
        name, path, size, remaining = item
        if _ordinary(path).st_size != size:
            raise WheelError("tooling seed source changed during collection")
        return name, _file_identity(path, remaining, deadline)

    records = dict(engine._bounded_content_map(read, inputs))
    return "sha256:" + hashlib.sha256(_canonical(records).encode()).hexdigest()


def _target_facts(target):
    if (
        not isinstance(target, dict) or target.keys() != {
            "version", "implementation", "cache_tag", "platform", "machine", "base_prefix",
        }
        or not isinstance(target["version"], list) or len(target["version"]) != 5
        or any(type(value) is not int for value in target["version"][:3])
        or target["version"][:2] < [3, 11]
    ):
        raise ValueError
    # Windows venvs can spell the same base prefix differently. Bind its
    # physical directory, not case or extended-path syntax.
    base = Path(target["base_prefix"])
    if not base.is_absolute() or not base.is_dir():
        raise ValueError
    identity = base.stat()
    if not identity.st_ino:
        raise ValueError
    target["base_prefix"] = {"device": identity.st_dev, "inode": identity.st_ino}
    return target


def _target(python: Path, environment: dict, deadline: float, procutil: ModuleType) -> dict:
    _check_deadline(deadline)
    clean = {
        key: value for key, value in environment.items()
        if not key.upper().startswith(("PYTHON", "PIP_", "UV_"))
        and key.upper() not in ("VIRTUAL_ENV", "CONDA_PREFIX")
    }
    try:
        probe_deadline = min(deadline, time.monotonic() + _TARGET_PROBE_TIMEOUT_SECONDS)
        result = run_owned(
            [dependencies._native_retained_path(python), "-I", "-S", "-B", "-c", _TARGET],
            cwd=Path(dependencies._native_retained_path(Path.cwd())), env=clean,
            timeout=probe_deadline - time.monotonic(),
            deadline=probe_deadline, procutil=procutil, construction_fd=None,
        )
        _check_deadline(probe_deadline)
        if result.returncode or len(result.stdout) > 16_384:
            raise ValueError
        return _target_facts(json.loads(result.stdout))
    except (OSError, TypeError, ValueError, ChildLaunchError, subprocess.SubprocessError):
        raise WheelError("tooling seed target probe failed or returned unsupported facts") from None


def _targets(
    python: Path, environment: dict, deadline: float, procutil: ModuleType, *, installed: Path,
) -> tuple[dict, dict]:
    deadline = min(deadline, time.monotonic() + _TARGET_PROBE_TIMEOUT_SECONDS)
    selected, observed = engine._bounded_content_map(
        lambda target: _target(target, environment, deadline, procutil),
        (python, installed),
    )
    return selected, observed


def _selected_wheel(content: bytes, *, deadline: float) -> dict:
    """Read one native file witness; UV owns version and target selection."""
    _check_deadline(deadline)
    try:
        record = dependencies._toml_parser().loads(content.decode("utf-8"))
        if (
            record.keys() != {"lock-version", "created-by", "requires-python", "packages"}
            or record["lock-version"] != "1.0" or record["created-by"] != "uv"
            or not isinstance(record["requires-python"], str)
            or not 0 < len(record["requires-python"]) <= 4096
            or not isinstance(record["packages"], list) or len(record["packages"]) != 1
        ):
            raise ValueError
        package = record["packages"][0]
        if (
            not isinstance(package, dict) or package.keys() != {"name", "version", "wheels"}
            or package["name"] != "packaging" or not isinstance(package["version"], str)
            or re.fullmatch(r"[0-9]+(?:\.[0-9]+){0,3}", package["version"]) is None
            or not isinstance(package["wheels"], list) or len(package["wheels"]) != 1
        ):
            raise ValueError
        wheel = package["wheels"][0]
        if (
            not isinstance(wheel, dict) or set(wheel) - {"url", "hashes", "size", "upload-time"}
            or not isinstance(wheel.get("url"), str)
            or not isinstance(wheel.get("hashes"), dict)
            or set(wheel["hashes"]) - {"sha256", "md5"}
            or not isinstance(wheel["hashes"]["sha256"], str)
            or re.fullmatch(r"[0-9a-f]{64}", wheel["hashes"]["sha256"]) is None
            or "md5" in wheel["hashes"] and (
                not isinstance(wheel["hashes"]["md5"], str)
                or re.fullmatch(r"[0-9a-f]{32}", wheel["hashes"]["md5"]) is None
            )
        ):
            raise ValueError
        location = urlsplit(wheel["url"])
        filename = unquote(location.path.rsplit("/", 1)[-1])
        if (
            location.scheme != "https" or not location.hostname
            or location.username is not None or location.password is not None
            or filename != "packaging-" + package["version"] + "-py3-none-any.whl"
        ):
            raise ValueError
        return {
            "name": "packaging", "version": package["version"],
            "filename": filename, "sha256": "sha256:" + wheel["hashes"]["sha256"],
        }
    except (KeyError, TypeError, ValueError, UnicodeError):
        raise WheelError("tooling seed needs one exact native Packaging wheel witness") from None


def inspect_seed(archive, selected: dict, *, limits: WheelLimits, deadline: float):
    """Reject dependencies and executable installation hooks before construction."""
    facts = inspect_archive(archive, limits=limits, deadline=deadline)
    prefix = "packaging-" + selected["version"] + ".dist-info/"
    if (
        facts.name != "packaging" or facts.version != selected["version"]
        or facts.prefix != prefix or facts.requires_dist or facts.provides_extras
        or facts.tags != ("py3-none-any",)
        or facts.wheel_metadata["Root-Is-Purelib"] != "true"
        or "packaging/__init__.py" not in facts.member_hashes
        or any(name.casefold() == prefix + "entry_points.txt" for name in facts.member_hashes)
        or any(
            not name.startswith(prefix) and not (
                name.startswith("packaging/") and (
                    name.endswith(".py") or name == "packaging/py.typed"
                ) and "__pycache__" not in name.split("/")
            )
            for name in facts.member_hashes
        )
    ):
        raise WheelError("tooling seed wheel is not dependency-free supported Packaging")
    return facts


@contextmanager
def retain_packaging_seed(
    *, payload: Path, source, policy: dict, uv: Path, python: Path,
    verify_owner: Callable[[tuple[dict, ...]], dict], opaque_identity: Callable[[str], str],
    limits: SeedLimits, acquisition_limits: AcquisitionLimits, wheel_limits: WheelLimits,
    deadline: float, procutil, transaction: dependencies.RetainedUVInstall,
    projections: tuple[str, str],
    source_verifier: Callable[[Path, object], None] | None = None,
):
    """Admit a fixed dependency through native UV and the existing constructor.

    ``verify_owner`` must recheck the real authenticated source and selected
    installation policy, including supplied revocations, through the yielded
    lifetime. Its argument is empty before native selection and contains the
    exact wheel afterwards; it returns the actual current owner policy for
    comparison with the selected policy. A self-asserted manifest is not
    verification.
    The caller keeps its source compiler's native transaction alive. Its
    exclusive retained settings, not ambient configuration, govern the seed.
    An explicit ``source_verifier`` may own source authentication instead,
    including the same payload and SourceIdentity's full fresh validation.
    Omission retains the original owner callback and raw source-pin check.
    """
    _check_deadline(deadline)
    acquisition_limits.verify()
    if (
        not isinstance(source, engine.runtime_source.SourceIdentity)
        or not isinstance(limits, SeedLimits) or not callable(verify_owner)
        or not isinstance(wheel_limits, WheelLimits)
        or not callable(opaque_identity) or not python.is_absolute()
        or not python.is_file() or not uv.is_absolute()
        or not isinstance(transaction, dependencies.RetainedUVInstall)
        or not isinstance(projections, tuple) or len(projections) != 2
        or policy.get("status") != "ready"
        or (source_verifier is not None and not callable(source_verifier))
    ):
        raise WheelError("tooling seed lacks its actual selected installation owner")
    uv = Path(dependencies._select_uv_executable(str(uv)))
    if transaction.argv[:3] != (str(uv), "pip", "compile"):
        raise WheelError("tooling seed differs from its owner's native compiler")
    python_identity = python.resolve(strict=True)
    frozen_policy = _canonical(policy)
    root = Path(policy["owner"]["runtimeRoot"])
    if not root.is_absolute():
        raise WheelError("tooling seed needs its installation-owned runtime root")
    if _canonical(verify_owner(())) != frozen_policy:
        raise WheelError("tooling seed selected policy changed before admission")
    expected_source = engine.runtime_source.manifest_fields(source)

    def verify_source():
        if source_verifier is None:
            engine._assert_source_pin(payload, expected_source)
        else:
            source_verifier(payload, source)

    verify_source()
    root = root / ".source-build-tools"
    owner_transaction, owner_projections = transaction, projections
    environment = dependencies._captured_environment(transaction.environment)
    retained_environment = _canonical(environment)
    target = _target(python, environment, deadline, procutil)
    python_digest = _file_identity(python_identity, limits.max_interpreter_bytes, deadline)
    tooling_digest = _tooling_identity(limits.max_tooling_bytes, deadline)
    selected_inputs = ()
    owner_feed = capture_native_feed_inputs(
        owner_transaction, owner_projections[1], owner_projections[0], None, policy,
        str(uv), deadline, wheels=None, profile_policy="packaging-seed-resolution",
        opaque_identity=opaque_identity,
    )

    def verify_inputs():
        _check_deadline(deadline)
        current_policy = verify_owner(tuple(dict(item) for item in selected_inputs))
        verify_source()
        if (
            _canonical(policy) != frozen_policy
            or _canonical(current_policy) != frozen_policy
            or _canonical(owner_transaction.environment) != retained_environment
            or python.resolve(strict=True) != python_identity
            or _file_identity(python_identity, limits.max_interpreter_bytes, deadline) != python_digest
            or _tooling_identity(limits.max_tooling_bytes, deadline) != tooling_digest
        ):
            raise WheelError("tooling seed source, interpreter or selected policy changed")
        if recheck_native_feed_inputs(
            owner_transaction, owner_projections[1], owner_projections[0], None, policy,
            str(uv), deadline, wheels=None, profile_policy="packaging-seed-resolution",
            opaque_identity=opaque_identity,
        ).policy_id != owner_feed.policy_id:
            raise WheelError("tooling seed owner feed policy changed")
        _check_deadline(deadline)

    def verify():
        verify_inputs()
        if _target(python, environment, deadline, procutil) != target:
            raise WheelError("tooling seed source, interpreter or selected policy changed")

    verify()
    owner_arguments = [
        argument
        for option, value in dependencies._argument_pairs(owner_transaction.argv[3:])
        if option in (
            "--exclude-newer", "--exclude-newer-package", "--resolution",
            "--prerelease", "--fork-strategy", "--index-strategy",
            "--keyring-provider", "--cert", "--system-certs",
            "--index", "--default-index", "--index-url", "--extra-index-url", "-i",
            "--no-sources",
        )
        for argument in ((option, value) if value else (option,))
    ]
    with dependencies._temporary_inputs(None) as temporary:
        temporary = dependencies._private_retained_root(Path(temporary)).resolve()
        project = temporary / "pyproject.toml"
        project.write_text(_PROJECT, encoding="utf-8")
        output = temporary / "pylock.toml"
        seed_projections = []
        with dependencies.retain_uv_source_compile(
            str(uv), [
                str(project), "--python", str(python), "--python-version",
                ".".join(str(value) for value in target["version"][:3]),
                "--only-binary", ":all:", "--no-python-downloads",
                *owner_arguments,
                "--output-file", str(output), "--format", "pylock.toml",
            ], env=environment, cwd=temporary, trusted_source_roots=(temporary,),
            _projection=seed_projections, deadline=deadline, native_target=True,
        ) as transaction:
            if len(seed_projections) != 2:
                raise WheelError("tooling seed lacks paired retained native settings")
            def frozen_cutoffs(held):
                return sorted(
                    (option, value) for option, value in dependencies._argument_pairs(held.argv[3:])
                    if option in ("--exclude-newer", "--exclude-newer-package")
                )

            if frozen_cutoffs(transaction) != frozen_cutoffs(owner_transaction):
                raise WheelError("tooling seed cutoffs differ from the held installation owner")

            def capture(wheels, profile_policy):
                return recheck_native_feed_inputs(
                    transaction, seed_projections[1], owner_projections[0], None, policy,
                    transaction.argv[0], deadline, wheels=wheels,
                    profile_policy=profile_policy, opaque_identity=opaque_identity,
                )

            preflight = capture(None, "packaging-seed-resolution").policy_id
            if preflight != owner_feed.policy_id:
                raise WheelError("tooling seed settings differ from the held installation owner")
            verify()
            try:
                with _open(project) as custody:
                    run_owned(
                        list(transaction.argv), env=transaction.environment, cwd=transaction.cwd,
                        timeout=deadline - time.monotonic(), deadline=deadline,
                        procutil=procutil, construction_fd=custody.fileno(),
                    )
            except (OSError, RuntimeError, subprocess.SubprocessError):
                raise WheelError("tooling seed native resolution failed or timed out") from None
            _check_deadline(deadline)
            if capture(None, "packaging-seed-resolution").policy_id != preflight:
                raise WheelError("tooling seed native configuration changed during resolution")
            with _open(output) as lock:
                content = lock.read(limits.max_lock_bytes + 1)
            if len(content) > limits.max_lock_bytes:
                raise WheelError("tooling seed native lock exceeds its byte budget")
            selected = _selected_wheel(content, deadline=deadline)
            selected_inputs = (selected,)
            verify()
            native_digest = "sha256:" + hashlib.sha256(content).hexdigest()
            feed = capture([selected], "packaging-seed-lock:" + native_digest)
            with acquire_wheel_inputs(
                [selected], feeds=feed.feeds, download_origins=feed.download_origins,
                cutoffs=feed.cutoffs, policy_id=feed.policy_id, limits=acquisition_limits,
                deadline=deadline, ssl_context=feed.context, proxies=feed.proxies, procutil=procutil,
            ) as acquired:
                facts = inspect_seed(
                    acquired.snapshot.archives[selected["filename"]], selected,
                    limits=wheel_limits, deadline=deadline,
                )
                binding = {
                    "schema": "copilot-extensions.packaging-seed", "version": 1,
                    "source": source.document(), "policy": json.loads(frozen_policy),
                    "python": str(python), "python_sha256": python_digest, "target": target,
                    "python_identity": str(python_identity),
                    "tooling_sha256": tooling_digest, "requirement": REQUIREMENT,
                    "native_lock_sha256": native_digest, "configuration_policy": preflight,
                    "feed_policy": feed.policy_id, "wheel": selected,
                    "requires_python": facts.requires_python,
                }
                inputs = acquired.construction_inputs(WheelInstallInputs(
                    acquired.snapshot, _canonical(binding),
                    (("packaging", facts.version, selected["filename"], selected["sha256"]),),
                ))
                version = source.runtime_version + ".build." + inputs.receipt()["lock_id"][7:]

                def validate(slot):
                    verify_inputs()
                    inputs.verify()
                    if capture([selected], "packaging-seed-lock:" + native_digest).policy_id != feed.policy_id:
                        raise WheelError("tooling seed feed policy changed during construction")
                    installed = engine.runtime.slot_python(root, slot.name)
                    if installed is None:
                        raise WheelError("tooling seed installed interpreter is missing")
                    original, observed = _targets(
                        python, environment, deadline, procutil, installed=installed,
                    )
                    if original != target:
                        raise WheelError("tooling seed source, interpreter or selected policy changed")
                    changed = sorted(key for key in target if target[key] != observed[key])
                    if changed:
                        raise WheelError(
                            "tooling seed installed interpreter differs from selection: " + ", ".join(changed),
                        )

                verify()
                with engine.numbered_candidate(
                    root, version, plugin=payload, uv=uv, base_python=python,
                    health_module="packaging", local_packages=(), wheel_inputs=inputs,
                    expected_source_identity=expected_source, validate_candidate=validate,
                    wait_timeout=deadline - time.monotonic(), child_timeout=deadline - time.monotonic(),
                ) as ready:
                    active = True

                    def verify_ready():
                        if not active:
                            raise WheelError("tooling seed custody is closed")
                        validate(ready.slot)

                    try:
                        verify_ready()
                        yield ReadySeed(**vars(ready), _verification=verify_ready)
                        verify_ready()
                    finally:
                        active = False
