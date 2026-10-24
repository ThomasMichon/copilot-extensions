"""Fixed Linux owned-test helper build inputs; no workload launch or admission.

Runtime-only linking deliberately omits CRT/default libraries for a C DSO whose
constructor needs no executable startup. Actual loaded symbol providers are
linked explicitly with undefined-symbol rejection. This is only a build
candidate: ELF shape cannot replace genuine child constructor/source/line proof.
"""

import ctypes
import hashlib
import os
from pathlib import Path
import shutil
import stat
import struct
import sys
import zipfile

WHEEL = "ziglang-0.14.1-py3-none-manylinux_2_12_x86_64.manylinux2010_x86_64.musllinux_1_1_x86_64.whl"
WHEEL_SHA256 = "6eb9d4d759b292c83810dbee2e9e8e3fbfbf01d864e6e9811bae711fd74e1c2f"
C_SHA256 = "7bd778c56c560dd38475d58dcbd5f6c74c8dd6d552e3ee6232ded00fa180aa61"
RUNTIME_SYMBOLS = (
    "clock_gettime", "getenv", "socket", "strlen", "close", "strcpy", "snprintf",
    "getpid", "getppid", "sendmsg", "dlsym", "strcmp", "open", "memcpy",
    "__tls_get_addr",
)


def entry_source():
    """Inline the shared codec into the source covered by the existing entry pin."""
    import inspect
    from .measurement_launch import ChildJSON

    template = Path(__file__).with_name("native_observer_entry.py").read_text(encoding="utf-8")
    declaration = "from tools.coverage_guided_selection.measurement_launch import ChildJSON\n"
    if template.count(declaration) != 1:
        raise ValueError("native entry requires exactly one shared codec declaration")
    return template.replace(declaration, inspect.getsource(ChildJSON) + "\n").encode("utf-8")


def _digest(path):
    with open(path, "rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def extract_toolchain(wheel, destination):
    """Extract all compiler/lib bytes freshly, never installing or pruning libraries."""
    wheel, destination = Path(wheel), Path(destination)
    if wheel.name != WHEEL:
        raise ValueError("observer compiler wheel identity refused")
    # Hash and parse the same open file, not a subsequently re-resolved pathname.
    wheel_file = wheel.open("rb")
    try:
        if hashlib.file_digest(wheel_file, "sha256").hexdigest() != WHEEL_SHA256:
            raise ValueError("observer compiler wheel identity refused")
        wheel_file.seek(0)
        destination.mkdir(mode=0o755)
    except BaseException:
        wheel_file.close()
        raise
    try:
        count = size = 0
        with zipfile.ZipFile(wheel_file) as archive:
            selected = [row for row in archive.infolist()
                        if row.filename == "ziglang/zig" or row.filename.startswith("ziglang/lib/")]
            names = set()
            for row in selected:
                parts = row.filename.rstrip("/").split("/")
                kind = stat.S_IFMT(row.external_attr >> 16)
                if (any(part in {"", ".", ".."} or "\\" in part or ":" in part for part in parts)
                        or row.filename in names or kind not in {0, stat.S_IFREG, stat.S_IFDIR}):
                    raise ValueError("observer compiler archive entry refused")
                names.add(row.filename)
                target = destination.joinpath(*parts)
                if row.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                else:
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with archive.open(row) as source, target.open("xb") as output:
                        shutil.copyfileobj(source, output)
                    target.chmod(0o755 if row.filename == "ziglang/zig" else 0o644)
                    if target.stat().st_size != row.file_size:
                        raise ValueError("incomplete observer compiler extraction")
                    count += 1
                    size += row.file_size
        compiler = destination / "ziglang" / "zig"
        if not compiler.is_file() or not (destination / "ziglang" / "lib").is_dir():
            raise ValueError("observer compiler or complete library tree absent")
        wheel_file.seek(0)
        if hashlib.file_digest(wheel_file, "sha256").hexdigest() != WHEEL_SHA256:
            raise ValueError("observer compiler wheel changed during extraction")
        return {"compiler": str(compiler), "wheel_sha256": WHEEL_SHA256,
                "extracted_files": count, "extracted_bytes": size}
    except BaseException:
        shutil.rmtree(destination)
        raise
    finally:
        wheel_file.close()


def _elf(path):
    if Path(path).stat().st_size > 128 * 1024 * 1024:
        raise ValueError("observer runtime ELF exceeds input bound")
    content = Path(path).read_bytes()
    if (len(content) < 64 or content[:7] != b"\x7fELF\x02\x01\x01"
            or struct.unpack_from("<HH", content, 16) != (3, 62)):
        raise ValueError("observer requires actual x86-64 Linux runtime ELF")
    return content


def runtime_link_inputs():
    """Bind required C symbols to their actual loaded, protected runtime providers."""
    if sys.platform != "linux" or ctypes.sizeof(ctypes.c_void_p) != 8 or sys.byteorder != "little":
        raise ValueError("runtime-only observer linking requires Linux x86-64")

    class DlInfo(ctypes.Structure):
        _fields_ = [("path", ctypes.c_char_p), ("base", ctypes.c_void_p),
                    ("symbol", ctypes.c_char_p), ("address", ctypes.c_void_p)]

    process = ctypes.CDLL(None)
    dladdr = process.dladdr
    dladdr.argtypes = [ctypes.c_void_p, ctypes.POINTER(DlInfo)]
    dladdr.restype = ctypes.c_int
    providers = {}
    for name in RUNTIME_SYMBOLS:
        symbol = getattr(process, name)
        pointer = ctypes.cast(symbol, ctypes.c_void_p)
        info = DlInfo()
        if not pointer.value or dladdr(pointer, ctypes.byref(info)) != 1 or not info.path:
            raise ValueError("missing actual observer runtime symbol provider")
        path = Path(os.fsdecode(info.path))
        if not path.is_absolute():
            raise ValueError("relative observer runtime provider refused")
        path = path.resolve(strict=True)
        metadata = path.stat()
        if metadata.st_uid != 0 or metadata.st_mode & 0o022 or not stat.S_ISREG(metadata.st_mode):
            raise ValueError("unprotected observer runtime provider")
        _elf(path)
        key = str(path)
        if key not in providers:
            providers[key] = {"path": key, "sha256": _digest(path),
                              "device": metadata.st_dev, "inode": metadata.st_ino, "symbols": []}
        providers[key]["symbols"].append(name)
    return sorted(providers.values(), key=lambda row: row["path"])


def verify_runtime_inputs(providers):
    symbols = []
    paths = set()
    if not providers:
        raise ValueError("missing observer runtime libraries")
    for row in providers:
        path = Path(row["path"])
        metadata = path.stat()
        if (not path.is_absolute() or str(path) in paths or metadata.st_uid != 0
                or metadata.st_mode & 0o022
                or not stat.S_ISREG(metadata.st_mode)
                or (metadata.st_dev, metadata.st_ino) != (row["device"], row["inode"])
                or _digest(path) != row["sha256"]):
            raise ValueError("observer runtime provider identity changed")
        _elf(path)
        paths.add(str(path))
        symbols.extend(row["symbols"])
    if sorted(symbols) != sorted(RUNTIME_SYMBOLS):
        raise ValueError("incomplete or duplicate observer runtime symbols")


def build_argv(compiler, source, output, entry_sha, *, variant, providers=()):
    if variant not in {"original", "runtime-link-only"}:
        raise ValueError("unknown observer build variant")
    if _digest(source) != C_SHA256 or len(entry_sha) != 64 or any(c not in "0123456789abcdef" for c in entry_sha):
        raise ValueError("observer C source or entry identity changed")
    args = [str(compiler), "cc", "-shared", "-fPIC", "-O1",
            '-DENTRY_SHA="' + entry_sha + '"', str(source), "-o", str(output)]
    if variant == "original":
        return args + ["-ldl"]
    verify_runtime_inputs(providers)
    return args + ["-nostdlib", "-nostartfiles", "-nodefaultlibs", "-Wl,-z,defs",
                   *[row["path"] for row in providers]]


def verify_artifact(path, entry_sha):
    """Require real constructor relocation and bound entry bytes; not ABI admission."""
    if len(entry_sha) != 64 or any(c not in "0123456789abcdef" for c in entry_sha):
        raise ValueError("invalid observer entry identity")
    content = _elf(path)
    phoff = struct.unpack_from("<Q", content, 32)[0]
    entsize, count = struct.unpack_from("<HH", content, 54)
    if entsize != 56 or not 0 < count <= 128 or phoff + count * entsize > len(content):
        raise ValueError("invalid observer ELF program headers")
    segments = [struct.unpack_from("<IIQQQQQQ", content, phoff + index * entsize)
                for index in range(count)]

    def at(address, size):
        for kind, flags, offset, virtual, physical, filesz, memsz, align in segments:
            if kind == 1 and virtual <= address and address + size <= virtual + filesz:
                start = offset + address - virtual
                if start + size <= len(content):
                    return content[start:start + size]
        raise ValueError("unmapped observer ELF initialization")

    dynamic = [row for row in segments if row[0] == 2]
    if len(dynamic) != 1:
        raise ValueError("missing observer ELF dynamic section")
    raw = at(dynamic[0][3], dynamic[0][5])
    tags = {}
    for offset in range(0, len(raw) - 15, 16):
        kind, value = struct.unpack_from("<qQ", raw, offset)
        if kind == 0:
            break
        tags[kind] = value
    if not tags.get(25) or not tags.get(27) or tags[27] % 8 or tags[27] > 1024:
        raise ValueError("missing observer constructor array")
    initializers = at(tags[25], tags[27])
    targets = [struct.unpack_from("<Q", initializers, offset)[0]
               for offset in range(0, len(initializers), 8)]
    relocated = set()
    if tags.get(7):
        if tags.get(9) != 24 or tags.get(8, 0) > 1_048_576 or tags.get(8, 0) % 24:
            raise ValueError("unsupported observer constructor relocations")
        relocations = at(tags[7], tags.get(8, 0))
        for offset in range(0, len(relocations), 24):
            address, info, addend = struct.unpack_from("<QQq", relocations, offset)
            if tags[25] <= address < tags[25] + tags[27]:
                if info != 8 or (address - tags[25]) % 8:
                    raise ValueError("unknown observer constructor relocation")
                index = (address - tags[25]) // 8
                if index in relocated:
                    raise ValueError("duplicate observer constructor relocation")
                relocated.add(index)
                targets[index] = addend
    if relocated != set(range(len(targets))):
        raise ValueError("unresolved observer constructor relocation")
    if not all(target > 0 and any(row[0] == 1 and row[1] & 1 and row[3] <= target < row[3] + row[5]
                   for row in segments) for target in targets):
        raise ValueError("observer constructor does not resolve to executable code")
    if entry_sha.encode("ascii") not in content:
        raise ValueError("observer native artifact lacks exact entry identity")
    return {"sha256": hashlib.sha256(content).hexdigest(), "bytes": len(content),
            "constructor_entries": len(targets), "entry_sha256": entry_sha,
            "static_shape_only": True}
