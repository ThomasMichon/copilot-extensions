"""Self-authored nested code captured from native CPython 3.10--3.13."""

import base64
import marshal
import struct

FILENAME = "/fixture/runtime/versions/retired/module.py"
BYTECODE = {
    "310": "bw0NCgAAAAAAAAAAAAAAAGMAAAAAAAAAAAAAAAAAAAAAAgAAAEAAAABzEAAAAGQAWgBkAWQChABaAWQDUwApBOkqAAAAYwEAAAAAAAAAAAAAAAEAAAADAAAAAwAAAHMMAAAAhwBmAWQBZAKECFMAKQNOYwAAAAAAAAAAAAAAAAAAAAAEAAAAEwAAAHMMAAAAiAB0AGQBZAJmBFMAKQNOdQUAAABjYWbDqXMFAAAAYnl0ZXMpAdoGYW5zd2VyqQCpAdoFdmFsdWVyAgAAAPorL2ZpeHR1cmUvcnVudGltZS92ZXJzaW9ucy9yZXRpcmVkL21vZHVsZS5wedoIPGxhbWJkYT4DAAAAcwIAAAAMAHoYbmVzdGVkLjxsb2NhbHM+LjxsYW1iZGE+cgIAAAByAwAAAHICAAAAcgMAAAByBQAAANoGbmVzdGVkAgAAAHMCAAAADAFyBwAAAE4pAnIBAAAAcgcAAAByAgAAAHICAAAAcgIAAAByBQAAANoIPG1vZHVsZT4BAAAAcwQAAAAEAAwB",
    "311": "pw0NCgAAAAAAAAAAAAAAAGMAAAAAAAAAAAAAAAABAAAAAAAAAPMQAAAAlwBkAFoAZAGEAFoBZAJTACkD6SoAAABjAQAAAAAAAAAAAAAAAgAAAAMAAADzDgAAAIcAlwCIAGYBZAGECFMAKQJOYwAAAAAAAAAAAAAAAAQAAAATAAAA8xoAAACVAZcAiQB0AAAAAAAAAAAAAABkAWQCZgRTACkDTnUFAAAAY2Fmw6lzBQAAAGJ5dGVzKQHaBmFuc3dlcqkB2gV2YWx1ZXMBAAAAgPorL2ZpeHR1cmUvcnVudGltZS92ZXJzaW9ucy9yZXRpcmVkL21vZHVsZS5wefoIPGxhbWJkYT56GG5lc3RlZC48bG9jYWxzPi48bGFtYmRhPgMAAABzEAAAAPiAAJAVnQagB6gY0BAygADzAAAAAKkAcgUAAABzAQAAAGByBwAAANoGbmVzdGVkcgsAAAACAAAAcxIAAAD4gADYCDLQCDLQCDLQCDLQATJyCQAAAE4pAnIEAAAAcgsAAAByCgAAAHIJAAAAcgcAAAD6CDxtb2R1bGU+cgwAAAABAAAAcyMAAADwAwEBAdgJC4AG8AIBATPwAAEBM/AAAQEz8AABATPwAAEBM3IJAAAA",
    "312": "yw0NCgAAAAAAAAAAAAAAAGMAAAAAAAAAAAAAAAABAAAAAAAAAPMOAAAAlwBkAFoAZAGEAFoBeQIpA+kqAAAAYwEAAAAAAAAAAAAAAAIAAAADAAAA8w4AAACHAJcAiABmAWQBhAhTACkCTmMAAAAAAAAAAAAAAAAEAAAAEwAAAPMYAAAAlQGXAIkAdAAAAAAAAAAAAGQBZAJmBFMAKQNOdQUAAABjYWbDqXMFAAAAYnl0ZXMpAdoGYW5zd2VyqQHaBXZhbHVlcwEAAACA+isvZml4dHVyZS9ydW50aW1lL3ZlcnNpb25zL3JldGlyZWQvbW9kdWxlLnB5+gg8bGFtYmRhPnoYbmVzdGVkLjxsb2NhbHM+LjxsYW1iZGE+AwAAAHMQAAAA+IAAkBWcBqAHqBjQEDKAAPMAAAAAqQByBQAAAHMBAAAAYHIHAAAA2gZuZXN0ZWRyCwAAAAIAAABzCQAAAPiAANsIMtABMnIJAAAATikCcgQAAAByCwAAAHIKAAAAcgkAAAByBwAAAPoIPG1vZHVsZT5yDAAAAAEAAABzDwAAAPADAQEB2AkLgAbzAgEBM3IJAAAA",
    "313": "8w0NCgAAAAAAAAAAAAAAAGMAAAAAAAAAAAAAAAABAAAAAAAAAPMOAAAAlQBTAHIAUwEaAHIBZwIpA+kqAAAAYwEAAAAAAAAAAAAAAAIAAAADAAAA8xAAAABeAJUAVQA0AVMBGgBqCCQAKQJOYwAAAAAAAAAAAAAAAAQAAAATAAAA8xgAAAA+AZUAVABbAAAAAAAAAAAAUwFTAjQEJAApA051BQAAAGNhZsOpcwUAAABieXRlcykB2gZhbnN3ZXKpAdoFdmFsdWVzAQAAAIDaKy9maXh0dXJlL3J1bnRpbWUvdmVyc2lvbnMvcmV0aXJlZC9tb2R1bGUucHnaCDxsYW1iZGE+2hhuZXN0ZWQuPGxvY2Fscz4uPGxhbWJkYT4DAAAAcw4AAAD4gACQFZwGoAeoGNEQMvMAAAAAqQByBQAAAHMBAAAAYHIHAAAA2gZuZXN0ZWRyDAAAAAIAAABzCQAAAPiAANwIMtABMnIKAAAATikCcgQAAAByDAAAAHILAAAAcgoAAAByBwAAANoIPG1vZHVsZT5yDQAAAAEAAABzDwAAAPADAQEB2AkLgAbzAgEBM3IKAAAA",
}


def code_record(version="313", *, raw_fields=None, **changes):
    """Build field-framing controls without loading or executing a code object."""
    fields = {
        "code": b"\x64\x00\x53\x00", "consts": (None,), "names": (), "locals": (),
        "free": (), "cells": (), "kinds": b"", "filename": FILENAME,
        "name": "<module>", "qualname": "<module>", "line": b"", "exception": b"",
    }
    assert set(changes) <= fields.keys()
    fields.update(changes)
    old = version == "310"
    order = ("code", "consts", "names", "locals", "free", "cells") if old else (
        "code", "consts", "names", "locals", "kinds",
    )
    order += ("filename", "name") + (() if old else ("qualname",))
    numbers = (0, 0, 0, 0, 1, 0) if old else (0, 0, 0, 1, 0)
    raw_fields = raw_fields or {}
    assert set(raw_fields) <= fields.keys()

    def field(name):
        return raw_fields[name] if name in raw_fields else marshal.dumps(fields[name], 0)

    data = base64.b64decode(BYTECODE[version])[:16] + b"c"
    data += struct.pack("<" + "i" * len(numbers), *numbers)
    data += b"".join(field(name) for name in order) + struct.pack("<i", 1)
    return data + field("line") + (b"" if old else field("exception"))
