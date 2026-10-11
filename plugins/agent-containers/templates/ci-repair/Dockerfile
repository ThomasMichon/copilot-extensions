ARG UV_IMAGE
FROM node:22-bookworm AS node-tools
FROM ${UV_IMAGE} AS uv-tools
FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm
COPY --from=uv-tools /uv /uvx /usr/local/bin/
COPY --from=node-tools /usr/local/bin/ /usr/local/bin/
COPY --from=node-tools /usr/local/lib/node_modules/ /usr/local/lib/node_modules/

# The adopter supplies a reviewed CLI version, not an account or credential.
ARG COPILOT_VERSION
RUN test -n "$COPILOT_VERSION" \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && npm install --global "@github/copilot@$COPILOT_VERSION" \
    && apt-get clean
USER vscode
WORKDIR /workspace
