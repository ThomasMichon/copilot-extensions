# ruff: noqa: F401,F821
# Executed into installation_context.py's shared module globals.
from __future__ import annotations

def _validate_legacy_attribution_items(
    items: Sequence[Mapping[str, Any]],
) -> list[dict[str, Any]]:
    validated: list[dict[str, Any]] = []
    seen_paths: set[str] = set()
    for index, item in enumerate(items):
        if not isinstance(item, Mapping):
            _fail(
                "Legacy attribution items must be JSON objects "
                f"(item {index + 1})."
            )
        kind = _required_string(item, "kind", "legacy attribution item")
        if kind != "path":
            _fail("Legacy attribution currently supports only path items.")
        identity = _required_string(item, "identity", "legacy attribution item")
        path_text = _required_string(item, "path", "legacy attribution item")
        candidate = Path(path_text)
        if not _path_is_fully_qualified(candidate):
            _fail("Legacy attribution item paths must be absolute.")
        raw_path = Path(os.path.abspath(os.fspath(candidate)))
        resolved = (
            raw_path
            if os.path.lexists(raw_path) and _is_link_or_junction(raw_path)
            else canonical_path(raw_path)
        )
        key = os.path.normcase(os.path.abspath(os.fspath(raw_path)))
        if key in seen_paths:
            _fail(f"Legacy attribution item path '{raw_path}' is duplicated.")
        seen_paths.add(key)
        validated.append(
            {
                "kind": kind,
                "identity": identity,
                "path": raw_path,
                "canonicalPath": resolved,
            }
        )
    return validated


def _validate_retirement_id(value: Any) -> str:
    if not isinstance(value, str) or RETIREMENT_ID.fullmatch(value) is None:
        _fail(
            "Legacy retirement id must match "
            "'[a-z0-9](?:[a-z0-9._-]{0,62}[a-z0-9])?'."
        )
    return value


def _read_regular_file(
    path: Path,
    *,
    label: str,
    require_stable_identity: bool = False,
    consume_chunk: Callable[[bytes], Any] | None = None,
) -> tuple[bytes, os.stat_result]:
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
    flags |= getattr(os, "O_CLOEXEC", 0)
    flags |= getattr(os, "O_NONBLOCK", 0)
    flags |= getattr(os, "O_NOFOLLOW", 0)
    descriptor = -1
    try:
        for _attempt in range(64):
            descriptor = os.open(path, flags)
            opened_stat = os.fstat(descriptor)
            if not stat.S_ISREG(opened_stat.st_mode):
                _fail(f"{label} must be an ordinary file.")
            try:
                named_stat = os.lstat(path)
            except OSError:
                os.close(descriptor)
                descriptor = -1
                continue
            if stat.S_ISLNK(named_stat.st_mode) or _is_link_or_junction(path):
                _fail(f"{label} may not be a symbolic link or reparse point.")
            if not stat.S_ISREG(named_stat.st_mode):
                _fail(f"{label} must be an ordinary file.")
            if _stat_identity(named_stat) == _stat_identity(opened_stat):
                break
            if not require_stable_identity and _attempt == 63:
                break
            os.close(descriptor)
            descriptor = -1
        else:
            _fail(f"{label} changed while it was being opened.")
        chunks: list[bytes] = []
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            if consume_chunk is None:
                chunks.append(chunk)
            else:
                consume_chunk(chunk)
        final_stat = os.fstat(descriptor)
        if require_stable_identity and (
            _stat_identity(opened_stat) != _stat_identity(final_stat)
            or _stat_metadata(opened_stat) != _stat_metadata(final_stat)
        ):
            _fail(f"{label} changed while it was being read.")
    except OSError as error:
        _fail(f"Cannot read {label.lower()} '{path}': {error}")
    finally:
        if descriptor >= 0:
            os.close(descriptor)
    try:
        current_stat = os.lstat(path)
    except OSError as error:
        _fail(f"Cannot inspect {label.lower()} '{path}': {error}")
    if _is_link_or_junction(path):
        _fail(f"{label} may not be a symbolic link or reparse point.")
    if not stat.S_ISREG(current_stat.st_mode):
        _fail(f"{label} must be an ordinary file.")
    if require_stable_identity and (
        _stat_identity(current_stat) != _stat_identity(final_stat)
        or stat.S_IFMT(current_stat.st_mode) != stat.S_IFMT(final_stat.st_mode)
        or (current_stat.st_size, current_stat.st_mtime_ns) != (final_stat.st_size, final_stat.st_mtime_ns)
        # Some Windows Python versions expose creation time via lstat but
        # change time via fstat; executable permission synthesis also differs.
        # Compare each API's complete metadata only with that same API.
        or _stat_metadata(current_stat) != _stat_metadata(named_stat)
    ):
        _fail(f"{label} changed while it was being read.")
    return b"".join(chunks), current_stat


def _read_regular_file_bytes(
    path: Path,
    *,
    label: str,
    require_stable_identity: bool = False,
) -> bytes:
    content, _validated_stat = _read_regular_file(
        path,
        label=label,
        require_stable_identity=require_stable_identity,
    )
    return content


def _sha256_file(path: Path) -> str:
    cache = _VALIDATED_FILE_SHA256.get()
    cached = cache.get(_file_cache_key(path)) if cache is not None else None
    if cached is not None:
        content, current_stat = _read_regular_file(
            path,
            label=f"File '{path}'",
            require_stable_identity=True,
        )
        current = _ValidatedFileDigest(
            digest=hashlib.sha256(content).hexdigest(),
            identity=_stat_identity(current_stat),
            metadata=_stat_metadata(current_stat),
        )
        if current != cached:
            _fail(f"File '{path}' changed after it was validated.")
        return current.digest
    content, validated_stat = _read_regular_file(
        path,
        label=f"File '{path}'",
        require_stable_identity=True,
    )
    return _cache_validated_file_digest(path, content, validated_stat)


def _snapshot_content_sha256(
    snapshot_root: Path,
    *,
    max_entries: int = MAX_SNAPSHOT_ENTRIES,
    max_path_bytes: int = MAX_SNAPSHOT_PATH_BYTES,
    max_content_bytes: int = MAX_SNAPSHOT_CONTENT_BYTES,
) -> str:
    records: list[tuple[bytes, str]] = []
    entry_count = 0
    total_content_bytes = 0

    if max_entries < 0 or max_path_bytes < 0 or max_content_bytes < 0:
        _fail("Snapshot content limits must be non-negative integers.")

    def relative_bytes(relative_text: str) -> bytes:
        try:
            encoded = relative_text.encode("utf-8")
        except UnicodeEncodeError as error:
            _fail(
                f"Snapshot content path is not valid UTF-8: '{relative_text}': "
                f"{error}"
            )
        if len(encoded) > max_path_bytes:
            _fail(
                "Snapshot content relative path exceeds the "
                f"{max_path_bytes}-byte UTF-8 limit: '{relative_text}'."
            )
        return encoded

    def account_entry(
        relative_text: str,
        encoded: bytes,
        entry_stat: os.stat_result,
    ) -> None:
        nonlocal entry_count, total_content_bytes
        entry_count += 1
        if entry_count > max_entries:
            _fail(
                f"Snapshot content exceeds the {max_entries}-entry limit."
            )
        if stat.S_ISREG(entry_stat.st_mode):
            total_content_bytes += entry_stat.st_size
            if total_content_bytes > max_content_bytes:
                _fail(
                    "Snapshot content exceeds the "
                    f"{max_content_bytes}-byte regular-file limit."
                )
        if len(encoded) > max_path_bytes:
            _fail(
                "Snapshot content relative path exceeds the "
                f"{max_path_bytes}-byte UTF-8 limit: '{relative_text}'."
            )

    def entry_kind(entry_stat: os.stat_result, relative_text: str) -> str:
        if stat.S_ISLNK(entry_stat.st_mode):
            _fail(
                "Snapshot content may not contain symbolic links or reparse "
                f"points: '{relative_text}'."
            )
        if stat.S_ISDIR(entry_stat.st_mode):
            return "D"
        if stat.S_ISREG(entry_stat.st_mode):
            return "F"
        _fail(
            "Snapshot content entries must be ordinary files or "
            f"directories: '{relative_text}'."
        )

    def hash_opened_file(
        descriptor: int,
        opened_stat: os.stat_result,
        relative_text: str,
    ) -> str:
        file_hash = hashlib.sha256()
        while True:
            chunk = os.read(descriptor, 1024 * 1024)
            if not chunk:
                break
            file_hash.update(chunk)
        final_stat = os.fstat(descriptor)
        if (
            _stat_identity(opened_stat) != _stat_identity(final_stat)
            or opened_stat.st_size != final_stat.st_size
            or opened_stat.st_mtime_ns != final_stat.st_mtime_ns
            or opened_stat.st_ctime_ns != final_stat.st_ctime_ns
        ):
            _fail(f"Snapshot content changed during hashing: '{relative_text}'.")
        return file_hash.hexdigest()

    if (
        os.name != "nt"
        and os.open in os.supports_dir_fd
        and os.scandir in os.supports_fd
    ):
        directory_flags = os.O_RDONLY | getattr(os, "O_CLOEXEC", 0)
        directory_flags |= getattr(os, "O_DIRECTORY", 0)
        directory_flags |= getattr(os, "O_NOFOLLOW", 0)

        def validate_directory(
            descriptor: int,
            relative_parts: tuple[str, ...],
            expected_stat: os.stat_result | None,
        ) -> os.stat_result:
            opened_stat = os.fstat(descriptor)
            if (
                not stat.S_ISDIR(opened_stat.st_mode)
                or (
                    expected_stat is not None
                    and (
                        _stat_identity(opened_stat)
                        != _stat_identity(expected_stat)
                        or _stat_metadata(opened_stat)
                        != _stat_metadata(expected_stat)
                    )
                )
            ):
                relative_text = "/".join(relative_parts)
                if relative_text:
                    _fail(
                        "Snapshot content changed during hashing: "
                        f"'{relative_text}'."
                    )
                _fail("Snapshot content changed during hashing.")
            return opened_stat

        def enter_directory(
            directory_descriptor: int,
            relative_parts: tuple[str, ...],
            expected_stat: os.stat_result | None,
        ) -> _SnapshotWalkFrame:
            initial_stat = validate_directory(
                directory_descriptor,
                relative_parts,
                expected_stat,
            )
            manifest: list[tuple[bytes, str]] = []
            inspected: list[tuple[str, str, bytes, os.stat_result, str]] = []
            try:
                with os.scandir(directory_descriptor) as iterator:
                    for entry in iterator:
                        relative_text = "/".join((*relative_parts, entry.name))
                        try:
                            entry_stat = os.stat(
                                entry.name,
                                dir_fd=directory_descriptor,
                                follow_symlinks=False,
                            )
                        except OSError as error:
                            _fail(
                                "Cannot inspect snapshot content "
                                f"'{relative_text}': {error}"
                            )
                        encoded = relative_bytes(relative_text)
                        kind = entry_kind(entry_stat, relative_text)
                        account_entry(relative_text, encoded, entry_stat)
                        manifest.append((encoded, kind))
                        inspected.append(
                            (
                                entry.name,
                                relative_text,
                                encoded,
                                entry_stat,
                                kind,
                            )
                        )
            except OSError as error:
                _fail(f"Cannot enumerate snapshot content: {error}")
            manifest.sort()
            return _SnapshotWalkFrame(
                relative_parts,
                initial_stat,
                manifest,
                inspected,
            )

        def hash_descriptor_file(
            directory_descriptor: int,
            frame: _SnapshotWalkFrame,
            entry: tuple[str, str, bytes, os.stat_result, str],
        ) -> None:
            name, relative_text, encoded, entry_stat, _kind = entry
            validate_directory(
                directory_descriptor,
                frame.relative_parts,
                frame.initial_stat,
            )
            descriptor = -1
            try:
                flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
                flags |= getattr(os, "O_CLOEXEC", 0)
                flags |= getattr(os, "O_NONBLOCK", 0)
                flags |= getattr(os, "O_NOFOLLOW", 0)
                descriptor = os.open(name, flags, dir_fd=directory_descriptor)
                opened_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or _stat_identity(opened_stat) != _stat_identity(entry_stat)
                ):
                    _fail(
                        "Snapshot content changed during hashing: "
                        f"'{relative_text}'."
                    )
                file_digest = hash_opened_file(
                    descriptor,
                    opened_stat,
                    relative_text,
                )
                current_stat = os.stat(
                    name,
                    dir_fd=directory_descriptor,
                    follow_symlinks=False,
                )
                if (
                    not stat.S_ISREG(current_stat.st_mode)
                    or _stat_identity(current_stat) != _stat_identity(entry_stat)
                    or _stat_metadata(current_stat) != _stat_metadata(entry_stat)
                ):
                    _fail(
                        f"Snapshot content changed during hashing: '{relative_text}'."
                    )
            except OSError as error:
                _fail(f"Cannot hash snapshot content '{relative_text}': {error}")
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            if frame.relative_parts or name != SNAPSHOT_PROVENANCE_FILE:
                records.append((encoded, file_digest))

        def leave_directory(
            directory_descriptor: int,
            frame: _SnapshotWalkFrame,
        ) -> None:
            final_directory_stat = validate_directory(
                directory_descriptor,
                frame.relative_parts,
                frame.initial_stat,
            )
            final_manifest: list[tuple[bytes, str]] = []
            try:
                with os.scandir(directory_descriptor) as iterator:
                    for entry in iterator:
                        if len(final_manifest) >= len(frame.manifest):
                            _fail("Snapshot content tree changed during hashing.")
                        relative_text = "/".join(
                            (*frame.relative_parts, entry.name)
                        )
                        entry_stat = os.stat(
                            entry.name,
                            dir_fd=directory_descriptor,
                            follow_symlinks=False,
                        )
                        final_manifest.append(
                            (
                                relative_bytes(relative_text),
                                entry_kind(entry_stat, relative_text),
                            )
                        )
                final_directory_stat = os.fstat(directory_descriptor)
            except OSError as error:
                _fail(f"Cannot enumerate snapshot content: {error}")
            final_manifest.sort()
            if (
                frame.manifest != final_manifest
                or _stat_identity(final_directory_stat)
                != _stat_identity(frame.initial_stat)
                or _stat_metadata(final_directory_stat)
                != _stat_metadata(frame.initial_stat)
            ):
                _fail("Snapshot content tree changed during hashing.")

        current_descriptor = -1
        try:
            current_descriptor = os.open(snapshot_root, directory_flags)
            try:
                opened_root_stat = os.fstat(current_descriptor)
                if not stat.S_ISDIR(opened_root_stat.st_mode):
                    _fail("Snapshot root must be an ordinary directory.")
                named_root_stat = os.lstat(snapshot_root)
                if (
                    not stat.S_ISDIR(named_root_stat.st_mode)
                    or _stat_identity(named_root_stat)
                    != _stat_identity(opened_root_stat)
                ):
                    _fail(
                        "Snapshot root may not traverse a symbolic link or "
                        "reparse point."
                    )
                stack = [
                    enter_directory(
                        current_descriptor,
                        (),
                        opened_root_stat,
                    )
                ]
                while stack:
                    frame = stack[-1]
                    if frame.next_entry < len(frame.entries):
                        entry = frame.entries[frame.next_entry]
                        frame.next_entry += 1
                        name, relative_text, _encoded, entry_stat, kind = entry
                        if kind == "D":
                            child_descriptor = -1
                            try:
                                child_descriptor = os.open(
                                    name,
                                    directory_flags,
                                    dir_fd=current_descriptor,
                                )
                                child_frame = enter_directory(
                                    child_descriptor,
                                    (*frame.relative_parts, name),
                                    entry_stat,
                                )
                                os.close(current_descriptor)
                                current_descriptor = child_descriptor
                                stack.append(
                                    child_frame
                                )
                            except OSError as error:
                                if child_descriptor >= 0:
                                    os.close(child_descriptor)
                                _fail(
                                    "Cannot enumerate snapshot content "
                                    f"'{relative_text}': {error}"
                                )
                            except BaseException:
                                if child_descriptor >= 0:
                                    os.close(child_descriptor)
                                raise
                        else:
                            hash_descriptor_file(
                                current_descriptor,
                                frame,
                                entry,
                            )
                        continue
                    leave_directory(current_descriptor, frame)
                    stack.pop()
                    if stack:
                        parent_descriptor = -1
                        try:
                            parent_descriptor = os.open(
                                "..",
                                directory_flags,
                                dir_fd=current_descriptor,
                            )
                            validate_directory(
                                parent_descriptor,
                                stack[-1].relative_parts,
                                stack[-1].initial_stat,
                            )
                        except BaseException:
                            if parent_descriptor >= 0:
                                os.close(parent_descriptor)
                            raise
                        os.close(current_descriptor)
                        current_descriptor = parent_descriptor
                current_root_stat = os.lstat(snapshot_root)
                if (
                    not stat.S_ISDIR(current_root_stat.st_mode)
                    or _stat_identity(current_root_stat)
                    != _stat_identity(opened_root_stat)
                ):
                    _fail("Snapshot root changed during hashing.")
            finally:
                os.close(current_descriptor)
        except OSError as error:
            _fail(f"Cannot enumerate snapshot content '{snapshot_root}': {error}")
    else:
        def directory_path(relative_parts: tuple[str, ...]) -> Path:
            return snapshot_root.joinpath(*relative_parts)

        def enter_directory(
            relative_parts: tuple[str, ...],
            expected_stat: os.stat_result | None,
        ) -> _SnapshotWalkFrame:
            directory = directory_path(relative_parts)
            try:
                initial_stat = os.lstat(directory)
            except OSError as error:
                _fail(f"Cannot inspect snapshot content '{directory}': {error}")
            if (
                not stat.S_ISDIR(initial_stat.st_mode)
                or _is_link_or_junction(directory)
            ):
                _fail(
                    "Snapshot content may not traverse symbolic links or reparse "
                    f"points: '{'/'.join(relative_parts)}'."
                )
            if expected_stat is not None and (
                _stat_identity(initial_stat) != _stat_identity(expected_stat)
                or _stat_metadata(initial_stat) != _stat_metadata(expected_stat)
            ):
                _fail("Snapshot content changed during hashing.")
            manifest: list[tuple[bytes, str]] = []
            inspected: list[tuple[str, str, bytes, os.stat_result, str]] = []
            try:
                with os.scandir(directory) as iterator:
                    for entry in iterator:
                        path = Path(entry.path)
                        relative_text = "/".join((*relative_parts, entry.name))
                        if _is_link_or_junction(path):
                            _fail(
                                "Snapshot content may not contain symbolic links "
                                f"or reparse points: '{relative_text}'."
                            )
                        try:
                            # Windows DirEntry.stat may report zero device/inode.
                            entry_stat = os.lstat(path)
                        except OSError as error:
                            _fail(
                                f"Cannot inspect snapshot content '{path}': {error}"
                            )
                        encoded = relative_bytes(relative_text)
                        kind = entry_kind(entry_stat, relative_text)
                        account_entry(relative_text, encoded, entry_stat)
                        manifest.append((encoded, kind))
                        inspected.append(
                            (
                                entry.name,
                                relative_text,
                                encoded,
                                entry_stat,
                                kind,
                            )
                        )
            except OSError as error:
                _fail(f"Cannot enumerate snapshot content '{directory}': {error}")
            manifest.sort()
            return _SnapshotWalkFrame(
                relative_parts,
                initial_stat,
                manifest,
                inspected,
            )

        def hash_path_file(
            frame: _SnapshotWalkFrame,
            entry: tuple[str, str, bytes, os.stat_result, str],
        ) -> None:
            name, relative_text, encoded, entry_stat, _kind = entry
            path = directory_path((*frame.relative_parts, name))
            flags = os.O_RDONLY | getattr(os, "O_BINARY", 0)
            flags |= getattr(os, "O_CLOEXEC", 0)
            flags |= getattr(os, "O_NONBLOCK", 0)
            flags |= getattr(os, "O_NOFOLLOW", 0)
            descriptor = -1
            try:
                descriptor = os.open(path, flags)
                opened_stat = os.fstat(descriptor)
                if (
                    not stat.S_ISREG(opened_stat.st_mode)
                    or _stat_identity(opened_stat) != _stat_identity(entry_stat)
                ):
                    _fail(
                        "Snapshot content changed during hashing: "
                        f"'{relative_text}'."
                    )
                file_digest = hash_opened_file(
                    descriptor,
                    opened_stat,
                    relative_text,
                )
            except OSError as error:
                _fail(f"Cannot hash snapshot content '{relative_text}': {error}")
            finally:
                if descriptor >= 0:
                    os.close(descriptor)
            try:
                current_stat = os.lstat(path)
            except OSError as error:
                _fail(f"Cannot inspect snapshot content '{relative_text}': {error}")
            if (
                _is_link_or_junction(path)
                or not stat.S_ISREG(current_stat.st_mode)
                or _stat_identity(current_stat) != _stat_identity(entry_stat)
                or _stat_metadata(current_stat) != _stat_metadata(entry_stat)
            ):
                _fail(f"Snapshot content changed during hashing: '{relative_text}'.")
            if frame.relative_parts or name != SNAPSHOT_PROVENANCE_FILE:
                records.append((encoded, file_digest))

        def leave_directory(frame: _SnapshotWalkFrame) -> None:
            directory = directory_path(frame.relative_parts)
            try:
                current_directory_stat = os.lstat(directory)
            except OSError as error:
                _fail(f"Cannot inspect snapshot content '{directory}': {error}")
            if (
                _is_link_or_junction(directory)
                or not stat.S_ISDIR(current_directory_stat.st_mode)
                or _stat_identity(current_directory_stat)
                != _stat_identity(frame.initial_stat)
                or _stat_metadata(current_directory_stat)
                != _stat_metadata(frame.initial_stat)
            ):
                _fail("Snapshot content tree changed during hashing.")
            final_manifest: list[tuple[bytes, str]] = []
            try:
                with os.scandir(directory) as iterator:
                    for entry in iterator:
                        if len(final_manifest) >= len(frame.manifest):
                            _fail("Snapshot content tree changed during hashing.")
                        path = Path(entry.path)
                        relative_text = "/".join(
                            (*frame.relative_parts, entry.name)
                        )
                        if _is_link_or_junction(path):
                            _fail(
                                "Snapshot content may not contain symbolic links "
                                f"or reparse points: '{relative_text}'."
                            )
                        entry_stat = os.lstat(entry.path)
                        final_manifest.append(
                            (
                                relative_bytes(relative_text),
                                entry_kind(entry_stat, relative_text),
                            )
                        )
                final_directory_stat = os.lstat(directory)
            except OSError as error:
                _fail(f"Cannot enumerate snapshot content '{directory}': {error}")
            final_manifest.sort()
            if (
                _is_link_or_junction(directory)
                or not stat.S_ISDIR(final_directory_stat.st_mode)
                or frame.manifest != final_manifest
                or _stat_identity(final_directory_stat)
                != _stat_identity(frame.initial_stat)
                or _stat_metadata(final_directory_stat)
                != _stat_metadata(frame.initial_stat)
            ):
                _fail("Snapshot content tree changed during hashing.")

        stack = [enter_directory((), None)]
        while stack:
            frame = stack[-1]
            if frame.next_entry < len(frame.entries):
                entry = frame.entries[frame.next_entry]
                frame.next_entry += 1
                name, _relative_text, _encoded, entry_stat, kind = entry
                if kind == "D":
                    stack.append(
                        enter_directory(
                            (*frame.relative_parts, name),
                            entry_stat,
                        )
                    )
                else:
                    hash_path_file(frame, entry)
                continue
            leave_directory(frame)
            stack.pop()
    digest = hashlib.sha256()
    for relative_bytes, file_sha256 in sorted(records, key=lambda item: item[0]):
        digest.update(b"F\0")
        digest.update(relative_bytes)
        digest.update(b"\0")
        digest.update(file_sha256.encode("ascii"))
        digest.update(b"\n")
    return digest.hexdigest()


def _atomic_write_json(
    path: Path,
    value: Mapping[str, Any],
    *,
    lock: _DirectoryLock | Sequence[_DirectoryLock] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(16)}")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        if lock is None:
            locks: Sequence[_DirectoryLock] = ()
        elif isinstance(lock, Sequence):
            locks = lock
        else:
            locks = (lock,)
        for held_lock in locks:
            held_lock.assert_owned()
        os.replace(temporary, path)
        _invalidate_validated_file_digest(path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _atomic_write_text(
    path: Path,
    value: str,
    *,
    lock: _DirectoryLock | Sequence[_DirectoryLock] | None = None,
) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(16)}")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write((value + "\n").encode("utf-8"))
            stream.flush()
            os.fsync(stream.fileno())
        if lock is None:
            locks: Sequence[_DirectoryLock] = ()
        elif isinstance(lock, Sequence):
            locks = lock
        else:
            locks = (lock,)
        for held_lock in locks:
            held_lock.assert_owned()
        os.replace(temporary, path)
        _invalidate_validated_file_digest(path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
    finally:
        if temporary.exists():
            temporary.unlink()


def _write_private_json(path: Path, value: Mapping[str, Any]) -> None:
    descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(_json_bytes(value))
        stream.flush()
        os.fsync(stream.fileno())
    if os.name != "nt":
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)


def _openprocess_denied_means_live(last_error: int) -> bool:
    return last_error == WINDOWS_ERROR_ACCESS_DENIED


def _publish_json_no_replace(
    path: Path,
    value: Mapping[str, Any],
    *,
    locks: Sequence[_DirectoryLock],
) -> bool:
    temporary = path.with_name(f".{path.name}.tmp-{secrets.token_hex(16)}")
    descriptor = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(_json_bytes(value))
            stream.flush()
            os.fsync(stream.fileno())
        for held_lock in locks:
            held_lock.assert_owned()
        try:
            os.link(temporary, path)
        except FileExistsError:
            return False
        except OSError as error:
            if os.path.lexists(path):
                return False
            _fail(f"Cannot publish runtime slot completion '{path}': {error}")
        _invalidate_validated_file_digest(path)
        if os.name != "nt":
            directory = os.open(path.parent, os.O_RDONLY)
            try:
                os.fsync(directory)
            finally:
                os.close(directory)
        return True
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
