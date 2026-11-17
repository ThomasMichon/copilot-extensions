"""In-memory descriptor and namespace owner for exclusive export controls."""

from __future__ import annotations

from dataclasses import dataclass, field
import os
import stat
from types import SimpleNamespace


@dataclass
class Node:
    mode: int
    inode: int
    uid: int = 1001
    links: int = 1
    data: bytes = b""
    children: dict[str, Node] = field(default_factory=dict)

    def info(self) -> os.stat_result:
        return os.stat_result((
            self.mode, self.inode, 1, self.links, self.uid, self.uid,
            len(self.data), 0, 0, 0,
        ))


class Filesystem:
    def __init__(self) -> None:
        self.root = Node(stat.S_IFDIR | 0o755, 1, uid=0)
        self.parent = Node(stat.S_IFDIR | 0o755, 2)
        self.destination = Node(stat.S_IFDIR | 0o700, 3)
        self.root.children["safe"] = self.parent
        self.parent.children["dest"] = self.destination
        self.handles: dict[int, Node] = {}
        self.labels: dict[int, str] = {}
        self.events: list[str] = []
        self.fault = ""
        self.next_fd = 20
        self.os = SimpleNamespace(
            name="posix", O_RDONLY=0, O_WRONLY=1, O_CREAT=64, O_EXCL=128,
            O_DIRECTORY=65536, O_NOFOLLOW=131072, O_CLOEXEC=524288, O_NONBLOCK=2048,
            getuid=lambda: 1001, open=self.open, stat=self.stat, fstat=self.fstat,
            write=self.write, fsync=self.fsync, close=self.close,
            supports_dir_fd={self.open, self.stat},
            supports_follow_symlinks={self.stat},
        )

    def open(self, name: str, flags: int, mode: int = 0o777, *, dir_fd: int | None = None) -> int:
        if flags & self.os.O_CREAT:
            assert dir_fd is not None
            parent = self.handles[dir_fd]
            assert flags & self.os.O_EXCL and flags & self.os.O_NOFOLLOW
            assert mode == 0o600
            if self.fault == "second-collision" and name.endswith(".pub"):
                parent.children[name] = Node(stat.S_IFREG | 0o600, 900, data=b"keep")
            if name in parent.children:
                raise FileExistsError(name)
            node = Node(stat.S_IFREG | mode, self.next_fd)
            if self.fault == "mode":
                node.mode = stat.S_IFREG | 0o644
            if self.fault == "hardlink":
                node.links = 2
            parent.children[name] = node
            self.events.append("create:" + name)
        else:
            assert flags & self.os.O_DIRECTORY and flags & self.os.O_NOFOLLOW
            if name == "/":
                node = self.root
            else:
                assert dir_fd is not None
                node = self.handles[dir_fd].children[name]
        descriptor = self.next_fd
        self.next_fd += 1
        self.handles[descriptor] = node
        self.labels[descriptor] = name
        return descriptor

    def stat(
        self, name: str, *, dir_fd: int | None = None, follow_symlinks: bool = True,
    ) -> os.stat_result:
        assert follow_symlinks is False
        if name == "/" and dir_fd is None:
            return self.root.info()
        assert dir_fd is not None
        try:
            return self.handles[dir_fd].children[name].info()
        except KeyError:
            raise FileNotFoundError(name) from None

    def fstat(self, descriptor: int) -> os.stat_result:
        return self.handles[descriptor].info()

    def write(self, descriptor: int, value: bytes) -> int:
        assert set(self.destination.children) == {"example.key", "example.key.pub"}
        self.events.append("write:" + self.labels[descriptor])
        if self.fault == "interrupt":
            raise KeyboardInterrupt
        if self.fault == "write":
            raise OSError("synthetic private detail")
        if self.fault == "zero":
            return 0
        count = min(2, len(value)) if self.fault == "short-write" else len(value)
        self.handles[descriptor].data += value[:count]
        if self.fault == "swap":
            self.destination.children["example.key"] = Node(
                stat.S_IFREG | 0o600, 901, data=b"untouched",
            )
        if self.fault == "parent-swap":
            self.parent.children["dest"] = Node(stat.S_IFDIR | 0o700, 902)
        return count

    def fsync(self, descriptor: int) -> None:
        self.events.append("sync:" + self.labels[descriptor])
        directory = stat.S_ISDIR(self.handles[descriptor].mode)
        if self.fault == ("directory-sync" if directory else "file-sync"):
            raise OSError("synthetic sync detail")

    def close(self, descriptor: int) -> None:
        node = self.handles.pop(descriptor)
        self.labels.pop(descriptor)
        if self.fault == "close" and stat.S_ISREG(node.mode):
            raise OSError("synthetic close outcome")
