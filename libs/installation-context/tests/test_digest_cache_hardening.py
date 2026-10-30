"""Structural and behavioral checks for shell digest-cache hardening."""
from __future__ import annotations

import importlib.util
import os
import shlex
import shutil
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest

from _powershell_fixture import _run_owner_probe

LIB = Path(__file__).resolve().parents[1]
POSIX_SCRIPT = LIB / "installation-context.sh"
POWERSHELL_SCRIPT = LIB / "installation-context.ps1"
POWERSHELL = shutil.which("pwsh") or shutil.which("powershell")
EXEMPLAR_SCRIPTS = (
    LIB.parents[1] / "plugins" / "agent-machines" / "scripts" / "init.sh",
    LIB.parents[1] / "plugins" / "agent-machines" / "scripts" / "init.ps1",
    LIB.parents[1] / "plugins" / "agent-index" / "scripts" / "install.sh",
    LIB.parents[1] / "plugins" / "agent-index" / "scripts" / "install.ps1",
)


def _load_python_module():
    path = LIB / "installation_context.py"
    spec = importlib.util.spec_from_file_location("digest_cache_installation_context", path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module




def _assembled_python_source() -> str:
    module = _load_python_module()
    fragments = getattr(module, "_FRAGMENT_FILES", ())
    if not fragments:
        return (LIB / "installation_context.py").read_text(encoding="utf-8")
    return "\n".join(
        (LIB / fragment).read_text(encoding="utf-8") for fragment in fragments
    )


def _posix_source() -> str:
    source = POSIX_SCRIPT.read_text(encoding="utf-8")
    assignment = 'SCRIPT_DIR="$(cd -P -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"'
    assert source.count(assignment) == 1
    return source.replace(assignment, f"SCRIPT_DIR={shlex.quote(str(LIB))}", 1)


def _run_canonical_shell_digest(
    shell: str,
    root: Path,
    work_root: Path,
    *,
    max_entries: int,
    max_path_bytes: int,
    max_content_bytes: int,
) -> subprocess.CompletedProcess[str]:
    work_root.mkdir(parents=True)
    if shell == "posix":
        source = _posix_source()
        prefix, marker, _ = source.partition('\nACTION="${1:-}"')
        assert marker
        probe = work_root / "digest.sh"
        probe.write_text(
            prefix
            + "\n"
            + (
                f'digest_snapshot_contents "$1" {max_entries} '
                f"{max_path_bytes} {max_content_bytes}\n"
            ),
            encoding="utf-8",
        )
        probe.chmod(0o755)
        command = (str(probe), str(root))
    else:
        return _run_owner_probe(
            work_root,
            f"print(_snapshot_content_sha256(Path({str(root)!r}), "
            f"max_entries={max_entries}, max_path_bytes={max_path_bytes}, "
            f"max_content_bytes={max_content_bytes}))",
        )
    return subprocess.run(
        command,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def test_powershell_uses_python_owned_native_identity_and_publication() -> None:
    script = POWERSHELL_SCRIPT.read_text(encoding="utf-8")
    python = _assembled_python_source()
    worker = (LIB / "_installation_context_worker.py").read_text(encoding="utf-8")
    assert "'installation_context.py'" in script
    assert "'_installation_context_worker.py'" in script
    assert 'runpy.run_path(request["sdk"]' in worker
    assert 'sdk["_powershell_main"](' in worker
    for forbidden in ("Add-Type", "Reflection.Emit", "DllImport", "fstat$INODE64"):
        assert forbidden not in script
    reader = python.split("def _read_regular_file(", 1)[1].split(
        "def _read_regular_file_bytes(", 1,
    )[0]
    assert "os.fstat(descriptor)" in reader
    assert "os.lstat(path)" in reader
    assert "_stat_identity(named_stat) == _stat_identity(opened_stat)" in reader
    publication = python.split("def _publish_json_no_replace(", 1)[1]
    assert "os.link(temporary, path)" in publication
    assert "except FileExistsError:" in publication


@pytest.mark.parametrize("occupied", (False, True))
def test_python_completion_publication_never_replaces_existing_identity(
    occupied: bool, tmp_path: Path,
) -> None:
    module = _load_python_module()
    target = tmp_path / "completion.json"
    if occupied:
        target.write_bytes(b'{"existing":true}\n')
        before = target.stat(), target.read_bytes()
    assert module._publish_json_no_replace(target, {"new": True}, locks=()) is (not occupied)
    if occupied:
        assert target.read_bytes() == before[1]
        assert module._stat_identity(target.stat()) == module._stat_identity(before[0])
    else:
        assert module.read_json(target) == {"new": True}
    assert list(tmp_path.iterdir()) == [target]


def test_shell_metadata_tokens_retain_posix_subsecond_precision() -> None:
    module = _load_python_module()
    posix = POSIX_SCRIPT.read_text(encoding="utf-8")

    first = SimpleNamespace(
        st_mode=0o100600, st_size=8,
        st_mtime_ns=1_000_000_001, st_ctime_ns=2_000_000_001,
    )
    for field in ("st_mtime_ns", "st_ctime_ns"):
        changed = SimpleNamespace(**{**vars(first), field: getattr(first, field) + 1})
        assert module._stat_metadata(first) != module._stat_metadata(changed)
    assert "%F|%d|%i|%s|%y|%z" in posix
    assert "ensure_kernel_name" in posix
    assert 'if [[ "$KERNEL_NAME" == Darwin ]]; then' in posix
    assert "digest_safe_file_fd_into verification_digest" in posix


def test_posix_cache_hit_hashes_same_size_same_second_rewrite(
    tmp_path: Path,
) -> None:
    script = _posix_source()
    prefix, marker, _ = script.partition('\nACTION="${1:-}"')
    assert marker
    probe = tmp_path / "digest-cache-probe.sh"
    probe.write_text(
        prefix
        + r'''
path="$1"
replacement="$2"
digest_file "$path" >/dev/null
cat -- "$replacement" >"$path"
touch -r "$replacement" "$path"
current="$(stat_file_metadata "$path")"
VALIDATED_FILE_IDENTITY["$path"]="$(metadata_identity "$current")"
VALIDATED_FILE_METADATA["$path"]="$current"
digest_file "$path" >/dev/null
''',
        encoding="utf-8",
    )
    probe.chmod(0o755)
    target = tmp_path / "target.txt"
    replacement = tmp_path / "replacement.txt"
    target.write_bytes(b"original")
    original = target.stat()
    replacement.write_bytes(b"modified")
    assert replacement.stat().st_size == original.st_size
    os.utime(
        replacement,
        ns=(original.st_atime_ns, original.st_mtime_ns),
    )

    result = subprocess.run(
        (str(probe), str(target), str(replacement)),
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )

    assert result.returncode != 0
    assert "changed after it was validated" in result.stderr.lower()


def test_python_cache_hit_rehashes_same_metadata_rewrite(tmp_path: Path) -> None:
    module = _load_python_module()
    path = tmp_path / "receipt.json"
    path.write_bytes(b'{"value":"first"}\n')
    original = path.stat()

    @module._validation_scope
    def probe() -> None:
        module.read_json(path)
        path.write_bytes(b'{"value":"other"}\n')
        assert path.stat().st_size == original.st_size
        os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
        current = path.stat()
        cache = module._VALIDATED_FILE_SHA256.get()
        assert cache is not None
        key = module._file_cache_key(path)
        cached = cache[key]
        module._VALIDATED_FILE_SHA256.set(
            {
                **cache,
                key: module._ValidatedFileDigest(
                    digest=cached.digest,
                    identity=module._stat_identity(current),
                    metadata=module._stat_metadata(current),
                ),
            }
        )
        module._sha256_file(path)

    with pytest.raises(
        module.InstallationContextError,
        match="changed after it was validated",
    ):
        probe()
    assert module._VALIDATED_FILE_SHA256.get() is None
    assert module._VALIDATION_SCOPE_DEPTH.get() == 0


def test_python_validation_cache_is_isolated_between_threads(
    tmp_path: Path,
) -> None:
    module = _load_python_module()
    path = tmp_path / "receipt.json"
    path.write_bytes(b'{"value":"first"}\n')
    first_cached = Event()
    second_cached = Event()
    release_second = Event()
    outcomes: dict[str, object] = {}

    @module._validation_scope
    def nested_cache_identity() -> int:
        cache = module._VALIDATED_FILE_SHA256.get()
        assert cache is not None
        return id(cache)

    @module._validation_scope
    def first_validation() -> None:
        module.read_json(path)
        first_cached.set()
        assert second_cached.wait(5)

    @module._validation_scope
    def second_validation() -> None:
        assert first_cached.wait(5)
        module.read_json(path)
        cache = module._VALIDATED_FILE_SHA256.get()
        assert cache is not None
        assert nested_cache_identity() == id(cache)
        second_cached.set()
        assert release_second.wait(5)
        try:
            module._sha256_file(path)
        except module.InstallationContextError as error:
            outcomes["second"] = error
        else:
            outcomes["second"] = "accepted"

    first = Thread(target=first_validation)
    second = Thread(target=second_validation)
    first.start()
    second.start()
    assert second_cached.wait(5)
    first.join(5)
    assert not first.is_alive()
    path.write_bytes(b'{"value":"other"}\n')
    release_second.set()
    second.join(5)
    assert not second.is_alive()

    error = outcomes.get("second")
    assert isinstance(error, module.InstallationContextError)
    assert "changed after it was validated" in str(error)
    assert module._VALIDATED_FILE_SHA256.get() is None
    assert module._VALIDATION_SCOPE_DEPTH.get() == 0


def test_completion_read_stability_matches_marker_mutability() -> None:
    python = _assembled_python_source()
    posix = POSIX_SCRIPT.read_text(encoding="utf-8")

    reader = python.split("def _read_regular_json_object(", 1)[1].split(
        "def _validated_build_completion",
        1,
    )[0]
    assert "require_stable_identity: bool = False" in reader
    assert "require_stable_identity=require_stable_identity" in reader
    completion = python.split("def _validated_runtime_slot_completion(", 1)[1].split(
        "@_validation_scope",
        1,
    )[0]
    assert "require_stable_identity=True" in completion
    build = python.split("def _validated_build_completion(", 1)[1].split(
        "def _runtime_slot_completion_value",
        1,
    )[0]
    assert "require_stable_identity=True" not in build

    assert (
        'capture_regular_file \\\n'
        '        "$RUNTIME_BUILD_PATH" "Build completion evidence" "$capture" false true'
        in posix
    )
    assert (
        'capture_json_for_validation actual_document "$actual" "Runtime slot completion"'
        in posix
    )


def test_digest_limits_and_scalable_sorting_are_present_everywhere() -> None:
    python = _assembled_python_source()
    posix = POSIX_SCRIPT.read_text(encoding="utf-8")
    python_digest = python.split("def _snapshot_content_sha256(", 1)[1].split(
        "def _atomic_write_json(",
        1,
    )[0]

    assert "MAX_SNAPSHOT_ENTRIES = 100_000" in python
    assert "MAX_SNAPSHOT_PATH_BYTES = 4_096" in python
    assert "MAX_SNAPSHOT_CONTENT_BYTES = 4_294_967_296" in python
    assert "list(os.scandir(" not in python_digest
    assert "visit_descriptor(" not in python_digest
    assert "visit_path(" not in python_digest
    assert "MAX_SNAPSHOT_ENTRIES=100000" in posix
    assert "MAX_SNAPSHOT_PATH_BYTES=4096" in posix
    assert "MAX_SNAPSHOT_CONTENT_BYTES=4294967296" in posix
    assert "sort -t $'\\t' -k1,1" in posix
    assert "with os.scandir(" in python_digest
    assert "sorted(records, key=lambda item: item[0])" in python_digest

    for script in EXEMPLAR_SCRIPTS:
        source = script.read_text(encoding="utf-8")
        assert "100000" in source
        assert "4096" in source
        assert "4294967296" in source
        if script.suffix == ".sh":
            assert "sort -t $'\\t' -k1,1" in source
            assert "local __entries=()" not in source
        else:
            assert "SortedDictionary[string, object]" in source
            payload_hash = source.split("function Get-PayloadHash(", 1)[1].split(
                "function Invoke-VersionedSlotClean",
                1,
            )[0]
            assert "[IO.Directory]::EnumerateFileSystemEntries(" in payload_hash
            assert "foreach ($entry in @(" not in payload_hash
            assert "for ($index = 1; $index -lt $files.Count; $index++)" not in source


def test_slot_complete_reconfirms_digest_immediately_before_publication() -> None:
    python = _assembled_python_source()
    posix = POSIX_SCRIPT.read_text(encoding="utf-8")

    python_publish = python.split(
        "desired = _runtime_slot_completion_value(",
        1,
    )[1].split("result = _validated_runtime_slot_completion", 1)[0]
    assert python_publish.count("_snapshot_content_sha256(") == 1
    assert python_publish.index("_snapshot_content_sha256(") < python_publish.index(
        "_publish_json_no_replace("
    )
    posix_publish = posix.split("complete_runtime_slot() {", 1)[1].split(
        "\nemit_source_identity()",
        1,
    )[0]
    assert 'confirmed_snapshot_content_sha256="$(digest_snapshot_contents' in posix_publish
    assert posix_publish.index("confirmed_snapshot_content_sha256") < posix_publish.index(
        "publish_completion_json_no_replace"
    )


@pytest.mark.parametrize(
    "shell",
    (
        *(("posix",) if os.name != "nt" else ()),
        *(("powershell",) if POWERSHELL is not None else ()),
    ),
)
def test_canonical_shell_digest_limit_boundaries_are_inclusive(
    shell: str,
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "a").write_bytes(b"x")
    accepted = _run_canonical_shell_digest(
        shell,
        root,
        tmp_path / "accepted",
        max_entries=1,
        max_path_bytes=1,
        max_content_bytes=1,
    )
    assert accepted.returncode == 0, accepted.stderr
    assert len(accepted.stdout.strip()) == 64

    for name, limits, message in (
        (
            "entries",
            {"max_entries": 0, "max_path_bytes": 1, "max_content_bytes": 1},
            "entry limit",
        ),
        (
            "path",
            {"max_entries": 1, "max_path_bytes": 0, "max_content_bytes": 1},
            "utf-8 limit",
        ),
        (
            "content",
            {"max_entries": 1, "max_path_bytes": 1, "max_content_bytes": 0},
            "regular-file limit",
        ),
    ):
        rejected = _run_canonical_shell_digest(
            shell,
            root,
            tmp_path / name,
            **limits,
        )
        assert rejected.returncode != 0
        assert message in rejected.stderr.lower()


@pytest.mark.parametrize("descriptor_walker", (True, False))
def test_python_wide_directory_stops_on_first_over_limit_entry(
    descriptor_walker: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_python_module()
    root = tmp_path / "snapshot"
    root.mkdir()
    for index in range(32):
        (root / f"{index:02d}").write_bytes(b"x")
    real_scandir = module.os.scandir

    class GuardedScandir:
        def __init__(self, source: object) -> None:
            self._iterator = real_scandir(source)
            self._consumed = 0

        def __enter__(self):
            self._iterator.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._iterator.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            self._consumed += 1
            if self._consumed > 4:
                raise AssertionError("enumeration continued beyond max_entries + 1")
            return next(self._iterator)

    def guarded_scandir(source: object) -> GuardedScandir:
        return GuardedScandir(source)

    monkeypatch.setattr(module.os, "scandir", guarded_scandir)
    if descriptor_walker:
        supported = set(module.os.supports_fd)
        supported.discard(real_scandir)
        supported.add(guarded_scandir)
        monkeypatch.setattr(module.os, "supports_fd", supported)
    else:
        monkeypatch.setattr(module.os, "supports_fd", set())

    with pytest.raises(module.InstallationContextError, match="3-entry limit"):
        module._snapshot_content_sha256(root, max_entries=3)


@pytest.mark.parametrize("descriptor_walker", (True, False))
def test_python_final_membership_scan_is_bounded(
    descriptor_walker: bool,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_python_module()
    root = tmp_path / "snapshot"
    root.mkdir()
    (root / "original").write_bytes(b"x")
    real_scandir = module.os.scandir
    scan_count = 0

    class GuardedScandir:
        def __init__(self, source: object, limit: int | None) -> None:
            self._iterator = real_scandir(source)
            self._limit = limit
            self._consumed = 0

        def __enter__(self):
            self._iterator.__enter__()
            return self

        def __exit__(self, *args: object) -> object:
            return self._iterator.__exit__(*args)

        def __iter__(self):
            return self

        def __next__(self):
            self._consumed += 1
            if self._limit is not None and self._consumed > self._limit:
                raise AssertionError("final comparison scan was not bounded")
            return next(self._iterator)

    def guarded_scandir(source: object) -> GuardedScandir:
        nonlocal scan_count
        scan_count += 1
        if scan_count == 2:
            for index in range(32):
                (root / f"added-{index:02d}").write_bytes(b"x")
            return GuardedScandir(source, 2)
        return GuardedScandir(source, None)

    monkeypatch.setattr(module.os, "scandir", guarded_scandir)
    if descriptor_walker:
        supported = set(module.os.supports_fd)
        supported.discard(real_scandir)
        supported.add(guarded_scandir)
        monkeypatch.setattr(module.os, "supports_fd", supported)
    else:
        monkeypatch.setattr(module.os, "supports_fd", set())

    with pytest.raises(module.InstallationContextError, match="tree changed"):
        module._snapshot_content_sha256(root)


def test_python_deep_snapshot_tree_exceeds_default_recursion_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    module = _load_python_module()
    depth = sys.getrecursionlimit() + 32
    root = tmp_path / "snapshot"
    root.mkdir()
    directories: list[Path] = []
    current = root
    try:
        for _ in range(depth):
            current = current / "d"
            current.mkdir()
            directories.append(current)
        leaf = current / "f"
        leaf.write_bytes(b"x")
        max_entries = depth + 1
        max_path_bytes = len(("d/" * depth + "f").encode("utf-8"))
        real_open = module.os.open
        real_close = module.os.close
        live_descriptors: set[int] = set()
        descriptor_high_water = 0

        def tracking_open(*args: object, **kwargs: object) -> int:
            nonlocal descriptor_high_water
            descriptor = real_open(*args, **kwargs)
            live_descriptors.add(descriptor)
            descriptor_high_water = max(
                descriptor_high_water,
                len(live_descriptors),
            )
            return descriptor

        def tracking_close(descriptor: int) -> None:
            live_descriptors.discard(descriptor)
            real_close(descriptor)

        monkeypatch.setattr(module.os, "open", tracking_open)
        monkeypatch.setattr(module.os, "close", tracking_close)

        descriptor_digest = module._snapshot_content_sha256(
            root,
            max_entries=max_entries,
            max_path_bytes=max_path_bytes,
            max_content_bytes=1,
        )
        assert descriptor_high_water <= 3
        assert not live_descriptors
        monkeypatch.setattr(module.os, "open", real_open)
        monkeypatch.setattr(module.os, "close", real_close)
        monkeypatch.setattr(module.os, "supports_fd", set())
        fallback_digest = module._snapshot_content_sha256(
            root,
            max_entries=max_entries,
            max_path_bytes=max_path_bytes,
            max_content_bytes=1,
        )
        assert fallback_digest == descriptor_digest
    except OSError as error:
        pytest.skip(f"native platform path limit prevents deep-tree coverage: {error}")
    finally:
        try:
            (current / "f").unlink()
        except OSError:
            pass
        for directory in reversed(directories):
            try:
                directory.rmdir()
            except OSError:
                pass


def test_deep_snapshot_tree_cross_runner_parity_under_recursion_limit(
    tmp_path: Path,
) -> None:
    module = _load_python_module()
    original_recursion_limit = sys.getrecursionlimit()
    recursion_limit = 128
    depth = recursion_limit + 32
    root = tmp_path / "snapshot"
    root.mkdir()
    directories: list[Path] = []
    current = root
    try:
        sys.setrecursionlimit(recursion_limit)
        for _ in range(depth):
            current = current / "d"
            current.mkdir()
            directories.append(current)
        leaf = current / "f"
        leaf.write_bytes(b"x")
        max_entries = depth + 1
        max_path_bytes = len(("d/" * depth + "f").encode("utf-8"))
        expected = module._snapshot_content_sha256(
            root,
            max_entries=max_entries,
            max_path_bytes=max_path_bytes,
            max_content_bytes=1,
        )

        shells = (
            *(("posix",) if os.name != "nt" else ()),
            *(("powershell",) if POWERSHELL is not None else ()),
        )
        for shell in shells:
            result = _run_canonical_shell_digest(
                shell,
                root,
                tmp_path / f"deep-{shell}",
                max_entries=max_entries,
                max_path_bytes=max_path_bytes,
                max_content_bytes=1,
            )
            assert result.returncode == 0, result.stderr
            assert result.stdout.strip() == expected
    except OSError as error:
        pytest.skip(f"native platform path limit prevents deep-tree coverage: {error}")
    finally:
        sys.setrecursionlimit(original_recursion_limit)
        try:
            (current / "f").unlink()
        except OSError:
            pass
        for directory in reversed(directories):
            try:
                directory.rmdir()
            except OSError:
                pass


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_powershell_wide_directory_rejects_small_limit(
    tmp_path: Path,
) -> None:
    root = tmp_path / "snapshot"
    root.mkdir()
    for index in range(256):
        (root / f"{index:03d}").write_bytes(b"x")

    result = _run_canonical_shell_digest(
        "powershell",
        root,
        tmp_path / "wide-powershell",
        max_entries=3,
        max_path_bytes=3,
        max_content_bytes=256,
    )

    assert result.returncode != 0
    assert "3-entry limit" in result.stderr.lower()


@pytest.mark.skipif(os.name == "nt", reason="POSIX FIFO behavior")
@pytest.mark.parametrize("bash", (
    shutil.which("bash"),
    *((os.environ["INSTALLATION_CONTEXT_BASH32"],) if os.environ.get("INSTALLATION_CONTEXT_BASH32") else ()),
))
def test_posix_safe_open_rejects_fifo_without_blocking(tmp_path: Path, bash: str) -> None:
    script = _posix_source()
    prefix, marker, _ = script.partition('\nACTION="${1:-}"')
    assert marker
    probe = tmp_path / "fifo-probe.sh"
    probe.write_text(
        prefix
        + r'''
shift
capture_regular_file "$1" "Probe file" "$2" true true
''',
        encoding="utf-8",
    )
    probe.chmod(0o755)
    fifo = tmp_path / "receipt.fifo"
    os.mkfifo(fifo)
    started = time.monotonic()

    result = subprocess.run(
        (bash, str(probe), "status", str(fifo), str(tmp_path / "capture")),
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**os.environ, "BASHPID": "not-a-pid"},
        timeout=5,
        check=False,
    )

    assert time.monotonic() - started < 5
    assert result.returncode != 0
    assert "without blocking" in result.stderr.lower()


@pytest.mark.skipif(POWERSHELL is None, reason="PowerShell is not installed")
def test_powershell_cache_hit_hashes_same_size_same_second_rewrite(
    tmp_path: Path,
) -> None:
    target = tmp_path / "target.txt"
    replacement = tmp_path / "replacement.txt"
    target.write_bytes(b"original")
    replacement.write_bytes(b"modified")
    assert replacement.stat().st_size == target.stat().st_size
    environment = os.environ.copy()
    environment["CE_DIGEST_CACHE_PATH"] = str(target)
    environment["CE_DIGEST_CACHE_REPLACEMENT"] = str(replacement)

    result = _run_owner_probe(
        tmp_path,
        """@_validation_scope
def probe():
    path = Path(os.environ['CE_DIGEST_CACHE_PATH'])
    replacement = Path(os.environ['CE_DIGEST_CACHE_REPLACEMENT'])
    _sha256_file(path)
    original = path.stat()
    path.write_bytes(replacement.read_bytes())
    os.utime(path, ns=(original.st_atime_ns, original.st_mtime_ns))
    current = path.stat()
    cache = _VALIDATED_FILE_SHA256.get()
    assert cache is not None
    key = _file_cache_key(path)
    cached = cache[key]
    cache[key] = _ValidatedFileDigest(
        digest=cached.digest, identity=_stat_identity(current), metadata=_stat_metadata(current),
    )
    _sha256_file(path)
probe()""",
        environment=environment,
    )

    assert result.returncode != 0
    assert "changed after it was validated" in result.stderr.lower()
