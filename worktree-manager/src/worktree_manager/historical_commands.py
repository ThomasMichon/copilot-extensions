"""Selected Manager prototype command removal under the real writer's custody.

Only Windows handle-bound disposal is qualified here; POSIX never falls back to
pathname unlink. Selected or unresolved runtime routes always remain protected.
"""

from __future__ import annotations

import hashlib
import os
import stat
from pathlib import Path

from . import historical_command_owner
from .native_path_retirement import PinnedPath
from .self_install import _KNOWN_LEGACY_BINSTUB_SIGNATURES, _identify_legacy_binstub

DETECTOR = 'manager-prototype-commands-v1'
REVISION = 1
_MAX_COMMAND_BYTES = 1024 * 1024


class _Preserve(Exception):
    pass


def _unredirected(path: Path) -> None:
    for component in (path, *path.parents):
        try:
            state = component.lstat()
        except FileNotFoundError:
            continue
        if stat.S_ISLNK(state.st_mode) or getattr(state, 'st_file_attributes', 0) & 0x400:
            raise _Preserve('linked-or-reparse-path')


def _scope(root: Path, bin_dir: Path) -> str | None:
    try:
        for path in (root, bin_dir):
            if (not path.is_absolute() or '..' in path.parts
                    or any(char in str(path) for char in '\r\n\0')):
                return 'invalid-owner-scope'
            _unredirected(path)
            if os.name == 'nt' and (
                any(part.endswith((' ', '.')) for part in path.parts)
                or os.path.normcase(str(path.resolve())) != os.path.normcase(str(path))
            ):
                return 'noncanonical-owner-scope'
            if path.exists() and not path.is_dir():
                return 'owner-scope-not-directory'
    except _Preserve as exc:
        return str(exc)
    except OSError:
        return 'owner-scope-unreadable'
    return None


def _ordinary(path: Path):
    _unredirected(path)
    state = path.lstat()
    if not stat.S_ISREG(state.st_mode) or state.st_nlink != 1:
        raise _Preserve('not-an-unshared-regular-file')
    return state


def _identity(state) -> tuple:
    return (state.st_dev, state.st_ino, state.st_size, state.st_mtime_ns, state.st_ctime_ns)


def _observe(path: Path) -> tuple[bytes | None, str | None, str | None, dict | None]:
    try:
        before = _ordinary(path)
        if before.st_size > _MAX_COMMAND_BYTES:
            return None, None, 'command-exceeds-inspection-bound', None
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        flags |= getattr(os, 'O_NONBLOCK', 0)
        descriptor = os.open(path, flags)
        try:
            opened = os.fstat(descriptor)
            if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1
                    or not os.path.samestat(before, opened) or before.st_size != opened.st_size):
                return None, None, 'physical-file-changed', None
            with os.fdopen(descriptor, 'rb', closefd=False) as stream:
                data = stream.read(_MAX_COMMAND_BYTES + 1)
            if (_identity(opened) != _identity(os.fstat(descriptor))
                    or _identity(before) != _identity(_ordinary(path))):
                return None, None, 'physical-file-changed', None
        finally:
            os.close(descriptor)
        if len(data) > _MAX_COMMAND_BYTES:
            return None, None, 'command-exceeds-inspection-bound', None
        return data, hashlib.sha256(data).hexdigest(), None, _physical(before)
    except FileNotFoundError:
        return None, None, 'already-absent', None
    except _Preserve as exc:
        return None, None, str(exc), None
    except OSError:
        return None, None, 'command-unreadable', None


def _physical(state) -> dict:
    return {"device": state.st_dev, "inode": state.st_ino,
            "size": state.st_size, "modified": state.st_mtime_ns,
            "created": state.st_ctime_ns}


def _binding(root: Path, bin_dir: Path) -> str | None:
    try:
        return historical_command_owner.historical_command_binding(root, bin_dir)
    except (OSError, ValueError, ImportError, RuntimeError, AttributeError):
        return "manager-command-binding-authority-unavailable"


def _selection(root: Path) -> str | None:
    try:
        return historical_command_owner.historical_command_selection(root)
    except (OSError, ValueError, ImportError, RuntimeError, AttributeError):
        return "manager-selection-authority-unavailable"


def _recognized(name: str, data: bytes) -> bool:
    try:
        return _identify_legacy_binstub(name, data.decode('utf-8')) is not None
    except UnicodeError:
        return False


def inspect(root: Path, bin_dir: Path) -> list[dict]:
    """Inspect only archived names; do not discover installations or change files."""
    scope_problem = _scope(root, bin_dir)
    problem = scope_problem or _binding(root, bin_dir) or _selection(root)
    findings = []
    for name in _KNOWN_LEGACY_BINSTUB_SIGNATURES:
        path = bin_dir / name
        data, digest, reason, physical = (
            (None, None, scope_problem, None) if scope_problem else _observe(path)
        )
        if reason == 'already-absent':
            continue
        reason = problem or reason
        recognized = reason is None and data is not None and _recognized(name, data)
        findings.append({
            'detector': DETECTOR, 'revision': REVISION, 'name': name,
            'resource': str(path), 'sha256': digest,
            'evidence': {'root': str(root), 'bin_dir': str(bin_dir), 'physical': physical},
            'status': 'candidate' if recognized else 'preserved',
            'reason': reason or ('exact-archived-prototype-command' if recognized
                                 else 'not-an-archived-prototype-command'),
        })
    return findings


def apply(root: Path, bin_dir: Path, finding: dict) -> dict:
    """Remove only a revalidated Windows handle, under native owner serialization."""
    resource = finding.get('resource') if isinstance(finding, dict) else None
    resource = resource if isinstance(resource, str) else ''
    def result(status, reason):
        return {'status': status, 'resource': resource, 'reason': reason}

    problem = _scope(root, bin_dir) or _binding(root, bin_dir)
    if problem:
        return result('preserved', problem)
    names = {str(bin_dir / name): name for name in _KNOWN_LEGACY_BINSTUB_SIGNATURES}
    if not isinstance(finding, dict) or resource not in names:
        return result('preserved', 'resource-outside-detector-scope')
    name = names[resource]
    if (finding.get('detector') != DETECTOR or type(finding.get('revision')) is not int
            or finding['revision'] != REVISION or finding.get('name', name) != name):
        return result('preserved', 'unsupported-detector-evidence')
    archived = _KNOWN_LEGACY_BINSTUB_SIGNATURES[name].encode('utf-8')
    reported = finding.get('sha256')
    if (finding.get('status') != 'candidate' or not isinstance(reported, str)
            or reported != hashlib.sha256(archived).hexdigest()):
        return result('preserved', 'invalid-reported-evidence')
    evidence = finding.get('evidence')
    if (not isinstance(evidence, dict) or evidence.get('root') != str(root)
            or evidence.get('bin_dir') != str(bin_dir)):
        return result('preserved', 'missing-owner-physical-evidence')
    physical = evidence.get('physical')
    keys = {'device', 'inode', 'size', 'modified', 'created'}
    if (not isinstance(physical, dict) or set(physical) != keys
            or any(type(value) is not int or value < 0 for value in physical.values())
            or physical['inode'] == 0):
        return result('preserved', 'missing-owner-physical-evidence')
    data, digest, reason, observed = _observe(bin_dir / name)
    if reason == 'already-absent':
        return result('already-absent', reason)
    if reason:
        return result('preserved', reason)
    if digest != reported:
        return result('preserved', 'reported-content-changed')
    if data is None or not _recognized(name, data):
        return result('preserved', 'not-an-archived-prototype-command')
    if observed != physical:
        return result('preserved', 'reported-physical-identity-changed')
    if os.name != 'nt':
        return result('preserved', 'posix-native-command-removal-unqualified')
    try:
        with historical_command_owner.historical_command_custody(root, [bin_dir / name]) as publication:
            problem = _scope(root, bin_dir) or _binding(root, bin_dir) or _selection(root)
            if problem:
                return result('preserved', problem)
            with _WindowsCommand(root, bin_dir, name) as held:
                problem = held.validate(physical, archived, reported, publication)
                if problem:
                    return result('preserved', problem)
                problem = _scope(root, bin_dir) or _binding(root, bin_dir) or _selection(root)
                if problem:
                    return result('preserved', problem)
                held.dispose()
            try:
                (bin_dir / name).lstat()
            except FileNotFoundError:
                return result('applied', 'archived-command-handle-removed')
            return result('failed', 'command-disposal-effect-unconfirmed')
    except FileNotFoundError:
        return result('preserved', 'command-disappeared-during-custody')
    except _Preserve as exc:
        return result('preserved', str(exc))
    except (OSError, ValueError, ImportError, RuntimeError, AttributeError) as exc:
        return result('failed', f'native-command-custody-or-disposal-failed: {type(exc).__name__}: {exc}')


class _WindowsCommand:
    """Apply command-owner admission to the shared handle-bound primitive."""

    def __init__(self, root: Path, bin_dir: Path, name: str):
        self.path = bin_dir / name
        self.root = root
        self.protect = (root, bin_dir)
        self.pinned = None

    def _open(self, path: Path, *, directory: bool):
        return PinnedPath(path, directory=directory, protect=self.protect)

    @property
    def fd(self):
        return self.pinned.fd

    def __enter__(self):
        self.pinned = self._open(self.path, directory=False)
        self.pinned.__enter__()
        return self

    def __exit__(self, *exc):
        return self.pinned.__exit__(*exc)

    def validate(self, expected: dict, archived: bytes, digest: str, publication) -> str | None:
        opened = self.pinned.state()
        if opened['kind'] != 'file' or opened['links'] != 1:
            return 'not-an-unshared-regular-file'
        named = _ordinary(self.path)
        if (_physical(named) != expected or not os.path.samestat(named, os.fstat(self.fd))
                or (opened['device'], opened['inode']) != (expected['device'], expected['inode'])):
            return 'reported-physical-identity-changed'
        state = publication.state_fd(self.fd)
        if state.digest != digest or state.size != len(archived):
            return 'reported-content-changed'
        os.lseek(self.fd, 0, os.SEEK_SET)
        if os.read(self.fd, len(archived) + 1) != archived:
            return 'not-an-archived-prototype-command'
        if (_physical(_ordinary(self.path)) != expected
                or self.pinned.state()['links'] != 1):
            return 'reported-physical-identity-changed'
        return None

    def dispose(self) -> None:
        problem = _binding(self.root, self.path.parent) or _selection(self.root)
        if problem:
            raise _Preserve(problem)
        state = self.pinned.state()
        if state['kind'] != 'file' or state['links'] != 1:
            raise _Preserve('not-an-unshared-regular-file')
        self.pinned.dispose()
