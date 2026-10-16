# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

GUEST_ENVIRONMENT_SCHEMA = "copilot-extensions.guest-environment"
GUEST_ENVIRONMENT_FILE = "guest-environment.json"


def _is_wsl_guest() -> bool:
    if not sys.platform.startswith("linux"):
        return False
    if any(Path(marker).is_file() for marker in ("/.dockerenv", "/run/.containerenv")):
        return False
    try:
        cgroup = Path("/proc/1/cgroup").read_text(encoding="utf-8")
    except OSError:
        cgroup = ""
    if any(marker in cgroup for marker in ("/docker", "/kubepods", "containerd", "/lxc", "libpod")):
        return False
    try:
        release = Path("/proc/sys/kernel/osrelease").read_text(encoding="utf-8")
    except OSError:
        return False
    return "microsoft" in release.casefold()


def _guest_machine_id() -> str:
    try:
        machine_id = Path("/etc/machine-id").read_text(encoding="ascii").strip()
    except (OSError, UnicodeError) as error:
        _fail(f"guest-identity-unavailable: cannot read the operating-system machine identity: {error}")
    if re.fullmatch(r"[0-9a-fA-F]{32}", machine_id) is None or int(machine_id, 16) == 0:
        _fail("guest-identity-unavailable: the operating-system machine identity is invalid.")
    return machine_id.casefold()


def _guest_distribution_name(value: Any) -> str:
    if (
        not isinstance(value, str) or not value
        or any(ord(character) < 32 or ord(character) == 127 for character in value)
    ):
        _fail("guest-identity-invalid: distribution must be a non-empty single-line string.")
    return value


def _guest_distribution_key(value: str) -> str:
    return value.translate(str.maketrans("ABCDEFGHIJKLMNOPQRSTUVWXYZ", "abcdefghijklmnopqrstuvwxyz"))


def _guest_environment_path(profile: Path) -> Path:
    return profile / ".copilot-extensions" / GUEST_ENVIRONMENT_FILE


def _read_guest_environment(profile: Path) -> Mapping[str, Any] | None:
    path = _guest_environment_path(profile)
    if not os.path.lexists(path):
        return None
    if _is_link_or_junction(path) or _is_link_or_junction(path.parent):
        _fail("guest-identity-invalid: guest registration may not be linked.")
    metadata = path.stat()
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > 65_536:
        _fail("guest-identity-invalid: guest registration must be a bounded regular file.")
    if os.name == "posix" and (
        metadata.st_uid != os.getuid() or metadata.st_mode & 0o022
    ):
        _fail("guest-identity-invalid: guest registration must be owned and writable only by this user.")
    registration = read_json(path)
    if (
        not isinstance(registration, Mapping)
        or set(registration) != {"schema", "version", "distribution", "machineId", "homeRealPath"}
        or registration["schema"] != GUEST_ENVIRONMENT_SCHEMA
        or type(registration["version"]) is not int or registration["version"] != 1
    ):
        _fail("guest-identity-invalid: unsupported or malformed guest registration.")
    _guest_distribution_name(registration["distribution"])
    if (
        registration["homeRealPath"] != str(profile)
        or registration["machineId"] != _guest_machine_id()
    ):
        _fail("guest-identity-foreign: registration does not identify this operating-system guest and profile.")
    return registration


def _resolve_wsl_distribution(
    profile: Path, *, environment: Mapping[str, str], explicit: str | None,
) -> str | None:
    if not _is_wsl_guest():
        if explicit is not None:
            _fail("guest-identity-conflict: a WSL distribution was supplied outside a WSL guest.")
        return None
    observed = environment.get("WSL_DISTRO_NAME") or None
    if observed is not None:
        observed = _guest_distribution_name(observed)
    if explicit is not None:
        explicit = _guest_distribution_name(explicit)
        if observed is not None and _guest_distribution_key(explicit) != _guest_distribution_key(observed):
            _fail("guest-identity-conflict: explicit and observed WSL distributions disagree.")
    requested = observed or explicit
    registration = _read_guest_environment(profile)
    if registration is not None:
        distribution = _guest_distribution_name(registration["distribution"])
        if requested is not None and _guest_distribution_key(requested) != _guest_distribution_key(distribution):
            _fail("guest-identity-conflict: observed distribution differs from the guest registration.")
        return distribution
    if requested is not None:
        return requested
    _fail(
        "guest-identity-unregistered: this WSL entry has no distribution identity. "
        "Run guest-register --distribution <name> through that named direct WSL entry; "
        "no runtime ownership metadata has been changed."
    )


def register_guest_environment(
    distribution: str, *, environment: Mapping[str, str] | None = None,
    os_profile: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    """Explicitly bind one named WSL entry to its OS guest and account profile."""
    environment = environment if environment is not None else os.environ
    distribution = _guest_distribution_name(distribution)
    if not _is_wsl_guest():
        _fail("guest-identity-conflict: guest-register requires a real WSL guest, not a host or container.")
    observed = environment.get("WSL_DISTRO_NAME")
    if not observed or _guest_distribution_key(_guest_distribution_name(observed)) != _guest_distribution_key(distribution):
        _fail("guest-identity-unverified: run guest-register through the matching named direct WSL entry.")
    current, profile = _current_environment(
        environment=environment, os_profile=os_profile, platform="posix", wsl_distro=observed,
    )
    path = _guest_environment_path(profile)
    if _is_link_or_junction(path.parent):
        _fail("guest-identity-invalid: guest registration directory may not be linked.")
    registration = {
        "schema": GUEST_ENVIRONMENT_SCHEMA, "version": 1,
        "distribution": current["wslDistro"], "machineId": _guest_machine_id(),
        "homeRealPath": str(profile),
    }
    with _DirectoryLock(
        path.parent / ".guest-environment.lock",
        kind="guest-environment", marketplace_id="os-profile",
    ) as lock:
        current["wslDistro"] = _resolve_wsl_distribution(
            profile, environment=environment, explicit=observed,
        )
        registration["distribution"] = current["wslDistro"]
        existing = _read_guest_environment(profile)
        if existing is not None and existing != registration:
            _fail("guest-identity-conflict: an existing guest registration cannot be replaced implicitly.")
        created = existing is None
        if created:
            if registration["machineId"] != _guest_machine_id():
                _fail("guest-identity-conflict: operating-system identity changed during registration.")
            _atomic_write_json(path, registration, lock=lock)
    return {
        "action": "guest-register", "status": "ready", "created": created,
        "registration": str(path), "environment": current,
    }


def _host_default_wsl_distribution() -> str | None:
    if os.name != "nt" or _is_wsl_guest():
        return None
    import winreg

    root = r"Software\Microsoft\Windows\CurrentVersion\Lxss"
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root) as key:
            identity, _kind = winreg.QueryValueEx(key, "DefaultDistribution")
    except FileNotFoundError:
        return None
    except OSError as error:
        _fail(f"guest-default-unavailable: cannot read the host WSL registry: {error}")
    if not isinstance(identity, str) or re.fullmatch(
        r"\{[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\}", identity,
    ) is None:
        _fail("guest-default-invalid: the host default WSL registration is malformed.")
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, root + "\\" + identity) as key:
            distribution, _kind = winreg.QueryValueEx(key, "DistributionName")
    except OSError as error:
        _fail(f"guest-default-invalid: cannot read the host default WSL distribution: {error}")
    return _guest_distribution_name(distribution)


def inspect_guest_environment(
    *, distribution: str | None = None,
    environment: Mapping[str, str] | None = None,
    os_profile: str | os.PathLike[str] | None = None,
) -> dict[str, Any]:
    expected = (
        _guest_distribution_name(distribution)
        if distribution is not None else None
    )
    current, profile = _current_environment(
        environment=environment if environment is not None else os.environ,
        os_profile=os_profile, platform=None,
        wsl_distro=None,
    )
    if (
        expected is not None and current["wslDistro"] is not None
        and _guest_distribution_key(expected) != _guest_distribution_key(current["wslDistro"])
    ):
        _fail("guest-identity-conflict: requested distribution differs from the current guest.")
    default = _host_default_wsl_distribution()
    matches = (
        _guest_distribution_key(default) == _guest_distribution_key(expected)
        if default is not None and expected is not None else None
    )
    status = "ready"
    if expected is not None and current["wslDistro"] is None:
        status = "unavailable" if default is None else ("ready" if matches else "mismatch")
    registration = _guest_environment_path(profile)
    return {
        "action": "guest-status", "status": status, "environment": current,
        "registrationState": (
            ("registered" if registration.exists() else "unregistered")
            if current["wslDistro"] is not None else "not-applicable"
        ),
        "registration": (
            str(registration) if current["wslDistro"] is not None and registration.exists()
            else None
        ),
        "hostDefault": {
            "status": "observed" if default is not None else "unavailable",
            "distribution": default, "expectedDistribution": expected, "matches": matches,
        },
    }
