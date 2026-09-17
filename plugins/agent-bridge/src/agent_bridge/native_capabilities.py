"""Only capabilities implemented by the native Session Host and live registry."""


def capabilities() -> dict:
    return {
        "terminal": {
            "protocol": "native.v1", "writer": "exclusive", "observers": True,
            "takeover": "explicit", "replayBytes": 1048576,
            "nonretryableCloseCodes": {"4409": "writer_busy", "4410": "writer_revoked", "4403": "read_only"},
        },
        "observation": {
            "kind": "represented-result-snapshot", "cursor": "position",
            "retention": "hosting-bridge-process", "gaps": "snapshot-coverage",
            "requiresRepresentation": True,
        },
        "control": {
            "promptAdmission": True, "promptIdempotency": True, "retireExecution": True,
            "typedInterrupt": False, "permissionResponses": False, "hiddenReasoning": False,
        },
    }
