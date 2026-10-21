"""Creation-only OS errors, without changing native missing-file handling."""


class SyncProcessLaunchError(OSError):
    """Process creation failed before any child or Job handle was returned."""

    def __init__(self, error: OSError):
        super().__init__(*error.args)
        for name in ("errno", "strerror", "filename", "filename2", "winerror"):
            if hasattr(error, name):
                setattr(self, name, getattr(error, name))


class SyncProcessNotFoundError(SyncProcessLaunchError, FileNotFoundError):
    """Keep native missing-file handling compatible with the typed boundary."""
