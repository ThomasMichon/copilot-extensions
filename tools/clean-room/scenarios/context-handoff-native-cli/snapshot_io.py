"""Descriptor-relative, no-follow I/O for the Linux evidence venue."""

from contextlib import contextmanager
import hashlib
import os
from pathlib import Path
import stat
import uuid


@contextmanager
def directory(path, create=False):
    if os.name != "posix" or not hasattr(os, "O_NOFOLLOW"):
        raise OSError("no-follow evidence I/O requires the Linux fixture")
    path = Path(os.path.abspath(path))
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(path.anchor, flags)
    try:
        for name in path.parts[1:]:
            if create:
                try:
                    os.mkdir(name, mode=0o700, dir_fd=descriptor)
                except FileExistsError:
                    pass
            child = os.open(name, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
        yield descriptor
    finally:
        os.close(descriptor)


def matches_identity(info, actual):
    return (isinstance(info, dict) and type(info.get("device")) is int and type(info.get("inode")) is int
            and info["device"] == actual.st_dev and info["inode"] == actual.st_ino)


def read_at(parent, name, expected=None):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("evidence input is not a singly-linked regular file")
        content = stream.read()
        if expected is not None and (not matches_identity(expected, info)
                                     or expected.get("sha256") != hashlib.sha256(content).hexdigest()):
            raise OSError("snapshot file identity or content changed")
        return content


def write_at(parent, name, content):
    try:
        info = os.stat(name, dir_fd=parent, follow_symlinks=False)
    except FileNotFoundError:
        info = None
    if info is not None and (not stat.S_ISREG(info.st_mode) or info.st_nlink != 1):
        raise OSError("evidence destination is not a singly-linked regular file")
    temporary = ".snapshot-" + uuid.uuid4().hex
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW,
                         0o600, dir_fd=parent)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            info = os.fstat(stream.fileno())
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
        return {"device": info.st_dev, "inode": info.st_ino, "sha256": hashlib.sha256(content).hexdigest()}
    finally:
        try:
            os.unlink(temporary, dir_fd=parent)
        except FileNotFoundError:
            pass


def read_bytes(path):
    path = Path(path)
    with directory(path.parent) as parent:
        return read_at(parent, path.name)


def write_bytes(path, content):
    path = Path(path)
    with directory(path.parent, create=True) as parent:
        write_at(parent, path.name, content)


class EventPath:
    def __init__(self, path, snapshot=None):
        self.path = path
        self.snapshot = snapshot

    def read_text(self, encoding="utf-8"):
        content = (read_snapshot(*self.snapshot, Path(self.path).relative_to(self.snapshot[0]).as_posix())
                   if self.snapshot is not None else read_bytes(self.path))
        return content.decode(encoding)


class SnapshotReceipt(set):
    def __init__(self):
        super().__init__()
        self.bindings = {"root": None, "files": {}}


def read_snapshot(root, bindings, relative):
    relative_path = Path(relative)
    if (relative_path.is_absolute() or not relative_path.parts or ".." in relative_path.parts
            or not isinstance(bindings, dict) or not isinstance(bindings.get("files"), dict)):
        raise OSError("invalid snapshot file binding")
    expected = bindings["files"].get(relative_path.as_posix())
    if not isinstance(expected, dict):
        raise OSError("snapshot file was not copied in this invocation")
    with directory(root) as root_fd:
        if not matches_identity(bindings.get("root"), os.fstat(root_fd)):
            raise OSError("snapshot root was replaced")
        parent = os.dup(root_fd)
        try:
            for name in relative_path.parts[:-1]:
                child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                os.close(parent)
                parent = child
            content = read_at(parent, relative_path.name, expected)
        finally:
            os.close(parent)
        with directory(root) as current:
            if not matches_identity(bindings.get("root"), os.fstat(current)):
                raise OSError("snapshot root changed during consumption")
        return content


def snapshot(source, destination, errors, label, excluded=()):
    copied = SnapshotReceipt()
    previous = set()
    def inspect_destination(parent, relative):
        for name in os.listdir(parent):
            path = relative / name
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        inspect_destination(child, path)
                    finally:
                        os.close(child)
                elif not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
                    raise OSError("unsupported destination entry")
                else:
                    previous.add(path.as_posix())
            except OSError:
                errors.append({"source": label, "path": path.as_posix(),
                               "error": "existing snapshot entry unavailable or unsafe; quarantined"})

    def visit(source_fd, destination_fd, relative):
        for name in os.listdir(source_fd):
            if name in excluded or Path(name).suffix in (".lock", ".hold"):
                continue
            path = relative / name
            try:
                info = os.stat(name, dir_fd=source_fd, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
                    child = os.open(name, flags, dir_fd=source_fd)
                    try:
                        try:
                            os.mkdir(name, mode=0o700, dir_fd=destination_fd)
                        except FileExistsError:
                            pass
                        target = os.open(name, flags, dir_fd=destination_fd)
                        try:
                            visit(child, target, path)
                        finally:
                            os.close(target)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    identity = write_at(destination_fd, name, read_at(source_fd, name))
                    copied.bindings["files"][path.as_posix()] = identity
                    copied.add(path.as_posix())
                else:
                    raise OSError("unsupported snapshot entry")
            except OSError:
                errors.append({"source": label, "path": path.as_posix(),
                               "error": "snapshot entry unavailable or unsafe; quarantined"})
    try:
        with directory(source) as source_fd, directory(destination, create=True) as destination_fd:
            info = os.fstat(destination_fd)
            copied.bindings["root"] = {"device": info.st_dev, "inode": info.st_ino}
            inspect_destination(destination_fd, Path())
            visit(source_fd, destination_fd, Path())
            for path in sorted(previous - copied):
                errors.append({"source": label, "path": path,
                               "error": "previous snapshot entry not copied in this invocation; quarantined"})
            with directory(destination) as current:
                if not matches_identity(copied.bindings["root"], os.fstat(current)):
                    raise OSError("snapshot destination changed during copying")
    except OSError:
        copied.clear()
        errors.append({"source": label, "error": "snapshot root unavailable or unsafe; quarantined"})
    return copied


def redact_tree(root, secret, errors):
    touched = []

    def visit(parent, relative):
        for name in os.listdir(parent):
            path = relative / name
            try:
                info = os.stat(name, dir_fd=parent, follow_symlinks=False)
                if stat.S_ISDIR(info.st_mode):
                    child = os.open(name, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW, dir_fd=parent)
                    try:
                        visit(child, path)
                    finally:
                        os.close(child)
                elif stat.S_ISREG(info.st_mode):
                    content = read_at(parent, name)
                    if secret and secret in content:
                        write_at(parent, name, content.replace(secret, b"[REDACTED]"))
                        touched.append(path.as_posix())
                else:
                    raise OSError("unsupported redaction entry")
            except OSError:
                errors.append({"source": "credential redaction", "path": path.as_posix(),
                               "error": "redaction entry unavailable or unsafe; quarantined"})
    try:
        with directory(root) as descriptor:
            visit(descriptor, Path())
    except OSError:
        errors.append({"source": "credential redaction", "error": "redaction root unavailable or unsafe"})
    return touched
