# Portable CI repair venue

This is a buildable, inert image/fleet template, not a repair charter or an
activated worker. Build with Docker's `--build-arg COPILOT_VERSION=<reviewed-version>`
and `--build-arg UV_IMAGE=ghcr.io/astral-sh/uv@sha256:<reviewed-digest>`;
tag `ci-repair:local`. The uv binaries are copied from that adopter-reviewed
immutable image, never a fetched install script. Load the adjacent `containers.yaml` through the
ordinary `agent-containers` config surface; provisioning uses its existing
image-backed fleet lifecycle. Building/provisioning requires separate operator
authorization and is not part of offline regression validation.

No repository, charter, account or credential is baked into this image.
The image contains Python, uv, Git, gh, Node and Copilot CLI. A task selects a
container venue, reviewed charter, and workspace independently through
`agent-dispatch`'s CI repair binding. The dispatcher passes the selection to
agent-bridge's actual `create --target-dir ... --charter ...` transport.

The starter fleet is restricted and network-disconnected, with no host workspace
mount, repository-token forwarding, credential relay or permission bypass.
Its ephemeral workspace/home is empty: an adopter must separately arrange
reviewed workspace materialization and the existing host-custody inference
identity contract. Restricted mode deliberately cannot be opted into host
credential forwarding. This template does not provide or replace authentication
transport. Missing inference or repository authority must refuse live admission.
Do not mount real credential stores, supply tokens in config/image/env/argv,
or give inference credentials to gh/Git.

The emitter remains OFF pending a separately reviewed recurrence/attempt policy
and independent inference/repository authority proof for the selected venue.
Contained fake-source/coordinator tests can exercise transport without granting
production activation. No cloud workflow is changed.
