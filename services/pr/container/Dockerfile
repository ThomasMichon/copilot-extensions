ARG PYTHON_BASE
FROM ${PYTHON_BASE}
ARG PYTHON_BASE
RUN python -I -c "import os,re; assert re.fullmatch(r'[^@]+@sha256:[0-9a-f]{64}', os.environ['PYTHON_BASE'])"
COPY wheels/ /tmp/pr-wheels/
COPY requirements.lock /tmp/pr-requirements.lock
RUN python -I -m pip install --no-index --find-links=/tmp/pr-wheels --only-binary=:all: --require-hashes --no-cache-dir -r /tmp/pr-requirements.lock \
    && python -I -c "import agent_pull_requests_service,agent_pull_requests.watch_service,fleet_manager.credential_references,agent_worktrees" \
    && rm -rf /tmp/pr-wheels /tmp/pr-requirements.lock \
    && mkdir /data && chown 65532:65532 /data
USER 65532:65532
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
WORKDIR /data
STOPSIGNAL SIGTERM
HEALTHCHECK --interval=5s --timeout=3s --start-period=10s --retries=2 \
    CMD ["python", "-I", "-m", "agent_pull_requests_service", "ready", "--status", "/data/pr-watch/service-status.json"]
ENTRYPOINT ["python", "-I", "-m", "agent_pull_requests_service"]
CMD ["serve", "--config", "/run/config/pr-service.json"]
