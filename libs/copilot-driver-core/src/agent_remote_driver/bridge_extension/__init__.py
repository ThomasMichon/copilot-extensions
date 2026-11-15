"""Canonical Bridge registration and delivery extension resources."""

from importlib.resources import files

ASSET_NAMES = (
    "announce.mjs",
    "bridge-endpoint.mjs",
    "delivery.mjs",
    "delivery-state.mjs",
    "extension.mjs",
    "machine-context.mjs",
    "metadata.mjs",
    "plugin-readiness.mjs",
    "questions.mjs",
    "remote-driver.mjs",
    "retained-delivery.mjs",
)


def extension_sources() -> tuple[tuple[str, bytes], ...]:
    """Export the selected Core's complete SDK registration/delivery closure."""
    root = files(__package__)
    return tuple((name, root.joinpath(name).read_bytes()) for name in ASSET_NAMES)
