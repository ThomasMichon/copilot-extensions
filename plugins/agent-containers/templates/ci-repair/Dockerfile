FROM node:22-bookworm AS node-tools
FROM mcr.microsoft.com/devcontainers/python:3.12-bookworm
COPY --from=node-tools /usr/local/bin/ /usr/local/bin/
COPY --from=node-tools /usr/local/lib/node_modules/ /usr/local/lib/node_modules/

# The adopter supplies a reviewed CLI version, not an account or credential.
ARG COPILOT_VERSION
RUN test -n "$COPILOT_VERSION" \
    && apt-get update \
    && apt-get install -y --no-install-recommends gh \
    && npm install --global "@github/copilot@$COPILOT_VERSION" \
    && pip install --no-cache-dir uv \
    && apt-get clean
USER vscode
WORKDIR /workspace
