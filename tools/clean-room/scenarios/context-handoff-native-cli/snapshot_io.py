"""Descriptor-relative, no-follow I/O for the Linux evidence venue."""

from contextlib import contextmanager
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


def read_at(parent, name):
    descriptor = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=parent)
    with os.fdopen(descriptor, "rb") as stream:
        info = os.fstat(stream.fileno())
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1:
            raise OSError("evidence input is not a singly-linked regular file")
        return stream.read()


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
        os.replace(temporary, name, src_dir_fd=parent, dst_dir_fd=parent)
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
    def __init__(self, path):
        self.path = path

    def read_text(self, encoding="utf-8"):
        return read_bytes(self.path).decode(encoding)


def snapshot(source, destination, errors, label, excluded=()):
    copied = set()
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
                    write_at(destination_fd, name, read_at(source_fd, name))
                    copied.add(path.as_posix())
                else:
                    raise OSError("unsupported snapshot entry")
            except OSError:
                errors.append({"source": label, "path": path.as_posix(),
                               "error": "snapshot entry unavailable or unsafe; quarantined"})
    try:
        with directory(source) as source_fd, directory(destination, create=True) as destination_fd:
            inspect_destination(destination_fd, Path())
            visit(source_fd, destination_fd, Path())
    except OSError:
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
