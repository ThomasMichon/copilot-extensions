# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

def _lock_openprocess_failure_means_live(last_error: int) -> bool:
    return last_error != 87  # ERROR_INVALID_PARAMETER is the absent-PID result.


def _pid_is_live(pid: int) -> bool:
    if pid < 1:
        return False
    if os.name == "nt":
        if pid > 0xFFFFFFFF:
            return True
        import ctypes
        from ctypes import wintypes

        synchronize = 0x00100000
        process_query_limited_information = 0x1000
        wait_object_0 = 0
        wait_timeout = 258
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.OpenProcess.argtypes = (
            wintypes.DWORD,
            wintypes.BOOL,
            wintypes.DWORD,
        )
        kernel32.OpenProcess.restype = wintypes.HANDLE
        kernel32.WaitForSingleObject.argtypes = (wintypes.HANDLE, wintypes.DWORD)
        kernel32.WaitForSingleObject.restype = wintypes.DWORD
        kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
        kernel32.CloseHandle.restype = wintypes.BOOL
        handle = kernel32.OpenProcess(
            synchronize | process_query_limited_information,
            False,
            pid,
        )
        if not handle:
            return _lock_openprocess_failure_means_live(ctypes.get_last_error())
        try:
            wait_result = kernel32.WaitForSingleObject(handle, 0)
            if wait_result == wait_object_0:
                return False
            if wait_result == wait_timeout:
                return True
            return True
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return True
    return True


class _DirectoryLock(AbstractContextManager["_DirectoryLock"]):
    def __init__(
        self,
        path: Path,
        *,
        kind: str,
        marketplace_id: str,
        plugin_id: str | None = None,
        timeout_seconds: float = LOCK_INITIALIZATION_GRACE_SECONDS,
    ) -> None:
        self.path = path
        self.kind = kind
        self.marketplace_id = marketplace_id
        self.plugin_id = plugin_id
        self.timeout_seconds = timeout_seconds
        self.token = secrets.token_hex(16)
        self.owner_path = path / "owner.json"
        self.host = socket.gethostname().split(".", 1)[0].casefold()
        self.acquired = False

    def _owner(self) -> Mapping[str, Any]:
        owner = read_json(self.owner_path)
        if not isinstance(owner, Mapping):
            _fail(f"Installation lock owner receipt '{self.owner_path}' must be an object.")
        if (
            _string_property(owner, "schema") != LOCK_SCHEMA
            or _property(owner, "version") != LOCK_VERSION
            or _string_property(owner, "kind") != self.kind
            or _string_property(owner, "marketplaceId") != self.marketplace_id
            or _string_property(owner, "pluginId") != (self.plugin_id or "")
        ):
            _fail(f"Installation lock owner receipt '{self.owner_path}' is invalid.")
        token = _string_property(owner, "token")
        host = _string_property(owner, "host")
        pid = _property(owner, "pid")
        if (
            not token
            or not host
            or isinstance(pid, bool)
            or not isinstance(pid, int)
            or pid < 1
        ):
            _fail(f"Installation lock owner receipt '{self.owner_path}' is incomplete.")
        return owner

    def _create(self, deadline: float) -> None:
        with _lock_mutation_guard(self.path, deadline=deadline):
            self.path.mkdir()
            owner = {
                "schema": LOCK_SCHEMA,
                "version": LOCK_VERSION,
                "kind": self.kind,
                "marketplaceId": self.marketplace_id,
                "pluginId": self.plugin_id or "",
                "token": self.token,
                "host": self.host,
                "pid": os.getpid(),
                "acquiredAt": _utc_now(),
            }
            birth = _runtime_publication_process_identity(os.getpid())
            if birth is not None:
                owner["processIdentity"] = birth
            try:
                _atomic_write_json(self.owner_path, owner)
            except BaseException:
                if self.owner_path.exists():
                    self.owner_path.unlink()
                self.path.rmdir()
                raise
            self.acquired = True

    def acquire(self) -> None:
        self.path = _lock_path(self.path)
        self.owner_path = self.path / "owner.json"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            if time.monotonic() >= deadline:
                if self.path.exists() and not self.owner_path.exists():
                    try:
                        age = time.time() - self.path.stat().st_mtime
                    except FileNotFoundError:
                        continue
                    if age >= LOCK_INITIALIZATION_GRACE_SECONDS:
                        _fail(
                            f"Installation lock '{self.path}' has no owner receipt; "
                            "explicit repair is required."
                        )
                _fail(f"Installation lock '{self.path}' remained busy.")
            try:
                self._create(deadline)
            except FileExistsError:
                if not self.owner_path.exists():
                    try:
                        age = time.time() - self.path.stat().st_mtime
                    except FileNotFoundError:
                        continue
                    if age >= LOCK_INITIALIZATION_GRACE_SECONDS:
                        _fail(
                            f"Installation lock '{self.path}' has no owner receipt; "
                            "explicit repair is required."
                        )
                    if time.monotonic() >= deadline:
                        _fail(f"Installation lock '{self.path}' remained busy.")
                    time.sleep(LOCK_POLL_SECONDS)
                    continue
                try:
                    owner = self._owner()
                except _FileIdentityChanged:
                    time.sleep(LOCK_POLL_SECONDS)
                    continue
                except InstallationContextError:
                    if not self.path.exists() or not self.owner_path.exists():
                        time.sleep(LOCK_POLL_SECONDS)
                        continue
                    raise
                owner_host = _string_property(owner, "host")
                owner_pid = _property(owner, "pid")
                if owner_host == self.host:
                    if _lock_owner_state(owner) == "stale":
                        with _lock_mutation_guard(self.path, deadline=deadline):
                            try:
                                current_owner = self._owner()
                            except _FileIdentityChanged:
                                continue
                            except InstallationContextError:
                                if not self.path.exists() or not self.owner_path.exists():
                                    continue
                                raise
                            if current_owner != owner or _lock_owner_state(current_owner) != "stale":
                                continue
                            _fail(
                                f"Installation lock '{self.path}' has a stale owner; "
                                "the owner's descendants must be independently confirmed inactive. "
                                "Use installation_context.py lock-inspect, then lock-repair with "
                                "--expected-evidence and --inactive-owner-evidence; "
                                "see recovering-operational-locks."
                            )
                    if time.monotonic() >= deadline:
                        _fail(f"Installation lock '{self.path}' remained busy.")
                    time.sleep(LOCK_POLL_SECONDS)
                    continue
                _fail(
                    f"Installation lock '{self.path}' is busy "
                    f"(host={owner_host}, pid={owner_pid})."
                )
            else:
                return

    def assert_owned(self) -> None:
        if not self.acquired:
            _fail(f"Installation lock '{self.path}' is not held.")
        owner = self._owner()
        if _string_property(owner, "token") != self.token:
            _fail(f"Installation lock '{self.path}' ownership changed during mutation.")

    def release(self) -> None:
        if not self.acquired:
            return
        with _lock_mutation_guard(self.path, deadline=time.monotonic() + 1):
            self._release_directory()

    def _release_directory(self) -> None:
        self.assert_owned()
        deadline = time.monotonic() + 1.0
        while True:
            try:
                self.owner_path.unlink()
                break
            except PermissionError as error:
                if time.monotonic() >= deadline:
                    _fail(f"Cannot release installation lock '{self.path}': {error}")
                time.sleep(LOCK_POLL_SECONDS)
        deadline = time.monotonic() + 1.0
        while True:
            try:
                self.path.rmdir()
                break
            except PermissionError as error:
                if time.monotonic() >= deadline:
                    _fail(f"Cannot release installation lock '{self.path}': {error}")
                time.sleep(LOCK_POLL_SECONDS)
            except OSError as error:
                _fail(f"Cannot release installation lock '{self.path}': {error}")
        self.acquired = False

    def __enter__(self) -> "_DirectoryLock":
        self.acquire()
        return self

    def __exit__(self, exc_type: object, exc_value: object, traceback: object) -> None:
        if exc_type is None:
            self.release()
            return
        try:
            self.release()
        except InstallationContextError as release_error:
            warnings.warn(
                f"{release_error} while preserving the original mutation failure.",
                RuntimeWarning,
                stacklevel=2,
            )


def _reject_duplicate_keys(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    casefolded: dict[str, str] = {}
    for key, value in pairs:
        if key in result:
            _fail(f"Duplicate JSON property '{key}'.")
        folded = key.casefold()
        if folded in casefolded:
            _fail(
                f"JSON properties '{casefolded[folded]}' and '{key}' differ only by case."
            )
        result[key] = value
        casefolded[folded] = key
    return result


def _normalize_git_url(value: str) -> str:
    if any(ord(character) < 32 or ord(character) == 127 for character in value):
        _fail("A git source URL may not contain control characters.")
    if not value.strip():
        _fail("A git source requires url.")
    candidate = value.strip()
    if re.search(r"%(?![0-9A-Fa-f]{2})", candidate):
        _fail("Git URL has a malformed percent-escape.")
    scp_match = re.fullmatch(r"[^/@:]+@([^/:]+):(.+)", candidate)
    if scp_match:
        candidate = f"ssh://{scp_match.group(1)}/{scp_match.group(2)}"
    parsed = urlsplit(candidate)
    if not parsed.scheme or not parsed.hostname:
        _fail(f"Git URL must be absolute and include a host: {value}")
    host = parsed.hostname.lower()
    if ":" in host:
        try:
            ipaddress.IPv6Address(host)
        except ValueError:
            _fail(f"Git URL has an invalid host: {value}")
    elif not re.fullmatch(r"[a-z0-9._-]+", host):
        _fail(f"Git URL has an invalid host: {value}")
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = ""
    try:
        if parsed.port is not None:
            defaults = {"http": 80, "https": 443}
            if defaults.get(parsed.scheme.lower()) != parsed.port:
                port = f":{parsed.port}"
    except ValueError as error:
        _fail(f"Invalid git URL '{value}': {error}")
    path = quote(
        (parsed.path or "/").replace("\\", "/"),
        safe="/-._~!$&'()*+,;=:@%[]",
    )
    path = re.sub(
        r"%([0-9a-fA-F]{2})",
        lambda match: (
            chr(int(match.group(1), 16))
            if 48 <= int(match.group(1), 16) <= 57
            or 65 <= int(match.group(1), 16) <= 90
            or 97 <= int(match.group(1), 16) <= 122
            or chr(int(match.group(1), 16)) in "-._~"
            else f"%{match.group(1).upper()}"
        ),
        path,
    )
    parts = path.split("/")
    normalized_parts: list[str] = []
    for part in parts:
        if not part and not normalized_parts:
            continue
        if part == ".":
            continue
        if part == "..":
            if normalized_parts:
                normalized_parts.pop()
            continue
        normalized_parts.append(part)
    while normalized_parts and not normalized_parts[-1]:
        normalized_parts.pop()
    path = f"/{'/'.join(normalized_parts)}"
    if path.lower().endswith(".git"):
        path = path[:-4]
    if not path.startswith("/"):
        path = f"/{path}"
    return urlunsplit((parsed.scheme.lower(), f"{host}{port}", path, "", ""))


def normalize_source(
    descriptor: Mapping[str, Any],
    base_directory: str | os.PathLike[str] | None = None,
    *,
    from_receipt: bool = False,
) -> NormalizedSource:
    """Normalize a source descriptor into the portable identity record."""

    raw_kind = _string_property(descriptor, "kind") or _string_property(
        descriptor,
        "source",
    )
    kind = raw_kind.strip().lower()
    aliases = {"local": "directory", "url": "git"}
    kind = aliases.get(kind, kind)
    ref = _string_property(descriptor, "ref")
    canonical_input = _string_property(descriptor, "canonical")
    if from_receipt and not canonical_input:
        _fail("A receipt source requires canonical identity.")
    canonical = ""

    if kind == "github":
        if canonical_input:
            if not canonical_input.startswith("github:"):
                _fail(f"Invalid canonical GitHub source '{canonical_input}'.")
            repository = canonical_input[7:]
        else:
            repository = (
                _string_property(descriptor, "repo")
                or _string_property(descriptor, "url")
            )
        repository = repository.strip()
        repository = re.sub(r"(?i)^https?://github\.com/", "", repository)
        repository = re.sub(r"(?i)^ssh://git@github\.com/", "", repository)
        repository = re.sub(r"(?i)^git@github\.com:", "", repository)
        repository = repository.strip("/")
        if repository.lower().endswith(".git"):
            repository = repository[:-4]
        if not re.fullmatch(r"[^/]+/[^/]+", repository):
            _fail(f"GitHub source requires owner/repository, got '{repository}'.")
        canonical = f"github:{repository.lower()}"
    elif kind == "git":
        if canonical_input:
            if not canonical_input.startswith("git:"):
                _fail(f"Invalid canonical git source '{canonical_input}'.")
            git_url = canonical_input[4:]
        else:
            git_url = _string_property(descriptor, "url")
        canonical = f"git:{_normalize_git_url(git_url)}"
    elif kind == "opaque":
        if canonical_input:
            canonical = canonical_input
        else:
            opaque_id = (
                _string_property(descriptor, "id")
                or _string_property(descriptor, "value")
            )
            if not opaque_id.strip():
                _fail("An opaque source requires a non-empty id.")
            canonical = f"opaque:{opaque_id}"
        if not canonical.startswith("opaque:"):
            _fail(f"Invalid canonical opaque source '{canonical}'.")
    elif kind == "directory":
        stable_id = _string_property(descriptor, "stableId").strip()
        if canonical_input:
            if canonical_input.startswith("directory-id:"):
                receipt_id = canonical_input[13:].strip()
                if not receipt_id:
                    _fail("A canonical directory-id source requires a non-empty id.")
                canonical = f"directory-id:{receipt_id}"
            elif canonical_input.startswith("directory:"):
                directory = canonical_input[10:]
                canonical = f"directory:{canonical_path(directory, must_exist=not from_receipt)}"
            else:
                _fail(f"Invalid canonical directory source '{canonical_input}'.")
        elif stable_id:
            canonical = f"directory-id:{stable_id}"
        else:
            directory_text = _string_property(descriptor, "path")
            if not directory_text.strip():
                _fail("A directory source requires a non-empty path or stableId.")
            directory = Path(directory_text)
            if not directory.is_absolute():
                if base_directory is None:
                    _fail("A relative directory source requires a declaration base directory.")
                directory = Path(base_directory) / directory
            canonical = f"directory:{canonical_path(directory, must_exist=True)}"
        if not (
            canonical.startswith("directory:") or canonical.startswith("directory-id:")
        ):
            _fail(f"Invalid canonical directory source '{canonical}'.")
    else:
        _fail(f"Unsupported source kind '{kind}'.")

    return NormalizedSource(kind=kind, canonical=canonical, ref=ref)


def source_record(source: NormalizedSource) -> str:
    fields = (
        ("version", "1"),
        ("kind", source.kind),
        ("source", source.canonical),
        ("ref", source.ref),
    )
    return "".join(
        f"{name}:{len(value.encode('utf-8'))}:{value}\n" for name, value in fields
    )


def _slug(value: str) -> str:
    result: list[str] = []
    previous_dash = False
    for character in value:
        if "A" <= character <= "Z":
            character = chr(ord(character) + 32)
        if "a" <= character <= "z" or "0" <= character <= "9":
            result.append(character)
            previous_dash = False
        elif result and not previous_dash:
            result.append("-")
            previous_dash = True
    return "".join(result).strip("-") or "marketplace"


def source_identity(source: NormalizedSource, readable_name: str) -> dict[str, Any]:
    record = source_record(source)
    digest = hashlib.sha256(record.encode("utf-8")).hexdigest()
    return {
        "kind": source.kind,
        "canonical": source.canonical,
        "ref": source.ref,
        "record": record,
        "sha256": digest,
        "fingerprint": f"sha256:{digest}",
        "marketplaceId": f"{_slug(readable_name)}--{digest[:16]}",
    }


def _get_declarations(
    key: str,
    copilot_home: Path,
    project_root: Path | None,
) -> list[dict[str, Any]]:
    settings_paths: list[tuple[Path, str, Path]] = [
        (copilot_home / "settings.json", "user", copilot_home),
        (copilot_home / "settings.local.json", "user-local", copilot_home),
    ]
    if project_root is not None:
        for relative in (
            ".claude/settings.json",
            ".claude/settings.local.json",
            ".github/copilot/settings.json",
            ".github/copilot/settings.local.json",
        ):
            settings_paths.append(
                (project_root / relative, f"project:{project_root}", project_root)
            )
    declarations: list[dict[str, Any]] = []
    for path, label, base in settings_paths:
        if not path.is_file():
            continue
        settings = read_json(path)
        marketplaces = _property(settings, "extraKnownMarketplaces")
        if not isinstance(marketplaces, Mapping):
            continue
        for candidate in marketplaces:
            if candidate != key and candidate.casefold() == key.casefold():
                _fail(f"JSON property '{candidate}' conflicts with exact case '{key}'.")
        if key not in marketplaces:
            continue
        declaration = marketplaces[key]
        descriptor = _property(declaration, "source")
        if not isinstance(descriptor, Mapping):
            _fail(f"Marketplace '{key}' has no source in '{path}'.")
        declarations.append(
            {
                "source": normalize_source(descriptor, base),
                "declaredIn": label,
                "settingsPath": canonical_path(path),
            }
        )
    if not declarations:
        _fail(
            "No user or explicit project extraKnownMarketplaces declaration found "
            f"for installed key '{key}'."
        )
    identities = {
        (
            item["source"].kind,
            item["source"].canonical,
            item["source"].ref,
        )
        for item in declarations
    }
    if len(identities) != 1:
        locations = ", ".join(str(item["settingsPath"]) for item in declarations)
        _fail(
            f"Conflicting declarations for marketplace key '{key}' in: {locations}. "
            "Supply explicit management provenance."
        )
    return declarations


def _resolve_installed_evidence(
    payload: Path,
    copilot_home: Path,
    project_root: Path | None,
) -> dict[str, Any] | None:
    installed = canonical_path(copilot_home / "installed-plugins")
    try:
        relative = payload.relative_to(installed)
    except ValueError:
        return None
    if len(relative.parts) != 2 or not all(relative.parts):
        _fail(
            "Installed payload must be exactly "
            f"<copilot-home>/installed-plugins/<key>/<plugin>: {payload}"
        )
    key, plugin_id = relative.parts
    declarations = _get_declarations(key, copilot_home, project_root)
    return {
        "source": declarations[0]["source"],
        "pluginId": plugin_id,
        "readableName": key,
        "locator": {
            "kind": "installed",
            "copilotHome": str(copilot_home),
            "marketplaceKey": key,
            "declaredIn": [item["declaredIn"] for item in declarations],
        },
    }


def _resolve_directory_evidence(
    payload: Path,
    requested_plugin_id: str | None,
) -> dict[str, Any] | None:
    cursor = payload
    manifest_paths = (
        ".github/plugin/marketplace.json",
        "marketplace.json",
        ".plugin/marketplace.json",
        ".claude-plugin/marketplace.json",
    )
    while True:
        for relative_manifest in manifest_paths:
            manifest_path = cursor / relative_manifest
            if not manifest_path.is_file():
                continue
            manifest = read_json(manifest_path)
            metadata = _property(manifest, "metadata", {})
            if not isinstance(metadata, Mapping):
                _fail(f"Marketplace metadata must be an object in '{manifest_path}'.")
            plugin_root_text = _string_property(metadata, "pluginRoot")
            source_base = cursor
            if plugin_root_text:
                plugin_root = Path(plugin_root_text)
                if plugin_root.is_absolute():
                    _fail(
                        f"Marketplace metadata.pluginRoot must be relative in '{manifest_path}'."
                    )
                if ".." in plugin_root.parts:
                    _fail(f"Marketplace metadata.pluginRoot may not escape '{cursor}'.")
                source_base = canonical_path(cursor / plugin_root)
                if not path_is_within(source_base, cursor):
                    _fail(f"Marketplace metadata.pluginRoot escapes '{cursor}'.")
            matches: list[Mapping[str, Any]] = []
            plugins = _property(manifest, "plugins", [])
            if not isinstance(plugins, Sequence) or isinstance(plugins, (str, bytes)):
                plugins = []
            for plugin in plugins:
                if not isinstance(plugin, Mapping):
                    continue
                name = _string_property(plugin, "name")
                if requested_plugin_id and name != requested_plugin_id:
                    continue
                source_path_text = _string_property(plugin, "source")
                if not source_path_text:
                    continue
                source_path = Path(source_path_text)
                if source_path.is_absolute() or ".." in source_path.parts:
                    _fail(
                        "Marketplace plugin source must be relative and remain "
                        f"beneath '{cursor}'."
                    )
                candidate = canonical_path(source_base / source_path)
                if not path_is_within(candidate, cursor):
                    _fail(f"Marketplace plugin source escapes '{cursor}'.")
                if candidate.exists() and paths_equal(candidate, payload):
                    matches.append(plugin)
            if len(matches) != 1:
                _fail(
                    f"Marketplace manifest '{manifest_path}' does not contain exactly "
                    f"one plugin entry resolving to '{payload}'."
                )
            plugin_id = _string_property(matches[0], "name")
            return {
                "source": normalize_source(
                    {"source": "directory", "path": str(cursor)},
                    cursor,
                ),
                "pluginId": plugin_id,
                "readableName": _string_property(manifest, "name", "marketplace"),
                "locator": {
                    "kind": "directory",
                    "marketplaceRoot": str(canonical_path(cursor, must_exist=True)),
                },
            }
        if cursor.parent == cursor:
            break
        cursor = canonical_path(cursor.parent)
    return None


def _resolve_legacy_snapshot_evidence(
    payload: Path, requested_plugin_id: str | None,
) -> dict[str, Any] | None:
    receipt_path = payload / SNAPSHOT_PROVENANCE_FILE
    if not os.path.lexists(receipt_path):
        return None
    receipt = read_json(receipt_path)
    if not isinstance(receipt, Mapping) or _property(receipt, "schema") != (
        "copilot-extensions.legacy-snapshot-source"
    ):
        return None
    version = _property(receipt, "version")
    if type(version) is not int or version not in (1, 2):
        _fail("Legacy snapshot source has an unsupported version.")
    snapshots = payload.parent
    root = snapshots.parent
    if snapshots.name != "snapshots" or _is_link_or_junction(receipt_path):
        _fail("Legacy snapshot source must be an ordinary snapshot-local file.")
    selected = _read_regular_file_bytes(
        root / "payload-dir", label="Legacy snapshot redirect", require_stable_identity=True,
    ).decode("utf-8").strip()
    stamped = _read_runtime_marker(root / "stamped-version", "Legacy snapshot version")
    snapshot_id = payload.name
    payload_version = payload.name
    if version == 2:
        snapshot_id = _string_property(receipt, "snapshotId")
        payload_version = _string_property(receipt, "payloadVersion")
        if not snapshot_id or not payload_version:
            _fail("Legacy snapshot source requires snapshot identity and payload version.")
    if (
        not _path_is_fully_qualified(selected)
        or not paths_equal(selected, payload)
        or snapshot_id != payload.name
        or stamped != payload_version
    ):
        _fail("Legacy snapshot source does not describe the selected snapshot.")
    resolution = _property(receipt, "resolution")
    if (
        not isinstance(resolution, Mapping)
        or _property(resolution, "action") != "resolve"
        or _property(resolution, "operative") is not False
        or _property(resolution, "rebindRequired") is not False
    ):
        _fail("Legacy snapshot source requires a canonical read-only resolution.")
    plugin = _string_property(resolution, "pluginId")
    _assert_plugin_id(plugin)
    if requested_plugin_id and plugin != requested_plugin_id:
        _fail("Legacy snapshot source identifies another plugin.")
    manifest = read_json(payload / "plugin.json")
    if not isinstance(manifest, Mapping) or _property(manifest, "name") != plugin:
        _fail("Legacy snapshot source does not match its payload plugin.")
    captured_source = _property(resolution, "source")
    if not isinstance(captured_source, Mapping):
        _fail("Legacy snapshot source identity is missing.")
    normalized = normalize_source(captured_source, from_receipt=True)
    marketplace = _string_property(resolution, "marketplaceId")
    _validate_marketplace_id(marketplace)
    readable = marketplace.rsplit("--", 1)[0]
    identity = source_identity(normalized, readable)
    if (
        identity["marketplaceId"] != marketplace
        or identity["fingerprint"] != _string_property(resolution, "sourceFingerprint")
        or identity["record"] != _string_property(captured_source, "record")
    ):
        _fail("Legacy snapshot source identity does not match its canonical fingerprint.")
    return {
        "source": normalized, "pluginId": plugin,
        "readableName": readable, "locator": None,
    }


def resolve_payload_source(
    *,
    payload_root: str | os.PathLike[str] | None = None,
    plugin_id: str | None = None,
    copilot_home: str | os.PathLike[str] | None = None,
    project_root: str | os.PathLike[str] | None = None,
    source_descriptor: Mapping[str, Any] | None = None,
    marketplace_key: str | None = None,
    environment: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    """Resolve payload provenance without reading or creating installation cells."""

    environment = environment if environment is not None else os.environ
    home = Path(environment.get("HOME") or Path.home())
    copilot_value = Path(copilot_home or home / ".copilot")
    if not _path_is_fully_qualified(copilot_value):
        _fail("--copilot-home must be absolute.")
    copilot = canonical_path(copilot_value)
    project = (
        canonical_path(project_root, must_exist=True)
        if project_root is not None else None
    )
    payload_value = payload_root or environment.get("COPILOT_PLUGIN_ROOT")
    if payload_value is None:
        _fail("resolve requires --payload-root or COPILOT_PLUGIN_ROOT.")
    payload_input = Path(payload_value)
    if not _path_is_fully_qualified(payload_input):
        _fail("The payload root must be absolute.")
    payload = canonical_path(payload_input, must_exist=True)
    if not payload.is_dir():
        _fail(f"The payload root must be an existing directory: {payload}")
    inherited_payload = environment.get("COPILOT_PLUGIN_ROOT")
    if inherited_payload:
        if not _path_is_fully_qualified(inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT must be absolute.")
        if not paths_equal(payload, inherited_payload):
            _fail("COPILOT_PLUGIN_ROOT conflicts with --payload-root.")

    if source_descriptor is not None:
        if not plugin_id:
            _fail("Explicit source resolution requires --plugin-id.")
        evidence = {
            "source": normalize_source(source_descriptor),
            "pluginId": plugin_id,
            "readableName": marketplace_key or "marketplace",
            "locator": None,
        }
    else:
        evidence = _resolve_installed_evidence(payload, copilot, project)
        if evidence is None:
            evidence = _resolve_legacy_snapshot_evidence(payload, plugin_id)
        if evidence is None:
            evidence = _resolve_directory_evidence(payload, plugin_id)
        if evidence is None:
            _fail(
                f"Cannot establish marketplace provenance for payload '{payload}'. "
                "Supply an explicit source descriptor for management/development mode."
            )
        if plugin_id and plugin_id != evidence["pluginId"]:
            _fail(
                f"Expected plugin '{plugin_id}', payload evidence identifies "
                f"'{evidence['pluginId']}'."
            )
    identity = source_identity(evidence["source"], evidence["readableName"])
    resolved_plugin_id = str(evidence["pluginId"])
    _assert_plugin_id(resolved_plugin_id)
    return {
        "source": {
            "kind": identity["kind"],
            "canonical": identity["canonical"],
            "ref": identity["ref"],
            "record": identity["record"],
        },
        "sourceFingerprint": identity["fingerprint"],
        "marketplaceId": identity["marketplaceId"],
        "pluginId": resolved_plugin_id,
        "payloadRoot": str(payload),
        "locator": evidence["locator"],
    }


def _locator_matches(locator: Mapping[str, Any], receipt: Mapping[str, Any]) -> bool:
    if _property(locator, "kind", "") != _property(receipt, "kind", ""):
        return False
    if locator["kind"] == "installed":
        return (
            _string_property(receipt, "marketplaceKey") == locator["marketplaceKey"]
            and paths_equal(
                _string_property(receipt, "copilotHome"),
                str(locator["copilotHome"]),
            )
        )
    if locator["kind"] == "directory":
        return paths_equal(
            _string_property(receipt, "marketplaceRoot"),
            str(locator["marketplaceRoot"]),
        )
    return False
