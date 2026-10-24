"""Synthetic wheel bytes shared by byte-only and native inspection controls."""
import base64
import csv
import hashlib
import io
import zipfile

PREFIX = "demo_pkg-1.0.dist-info/"


def wheel_bytes(*, metadata=None, wheel=None, extra=None, record_mutator=None,
                compression=zipfile.ZIP_DEFLATED, prefix=PREFIX, name="demo_pkg"):
    entries = {
        name + "/__init__.py": b"VALUE = 1\n",
        prefix + "METADATA": metadata if metadata is not None else (
            b"Metadata-Version: 2.3\nName: demo-pkg\nVersion: 1.0\n"
            b"Requires-Python: >=3.10\nProvides-Extra: fast\n"
            b"Requires-Dist: helper>=2; extra == 'fast'\n\n"
        ),
        prefix + "WHEEL": wheel if wheel is not None else (
            b"Wheel-Version: 1.0\nRoot-Is-Purelib: true\nTag: py3-none-any\n\n"
        ),
        **(extra or {}),
    }
    rows = [
        [name, "sha256=" + base64.urlsafe_b64encode(hashlib.sha256(data).digest())
         .rstrip(b"=").decode(), str(len(data))]
        for name, data in entries.items()
    ]
    rows.append([prefix + "RECORD", "", ""])
    if record_mutator:
        record_mutator(rows)
    record = io.StringIO(newline="")
    csv.writer(record, lineterminator="\n").writerows(rows)
    entries[prefix + "RECORD"] = record.getvalue().encode()
    result = io.BytesIO()
    with zipfile.ZipFile(result, "w", compression=compression) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return result.getvalue()
