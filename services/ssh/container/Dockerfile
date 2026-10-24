ARG PYTHON_BASE
FROM ${PYTHON_BASE}
ARG PYTHON_BASE
RUN python -I -c "import os,re; assert re.fullmatch(r'[^@]+@sha256:[0-9a-f]{64}', os.environ['PYTHON_BASE'])"
COPY wheels/ /tmp/ssh-wheels/
COPY requirements.lock /tmp/ssh-requirements.lock
RUN python -I -m pip install --no-index --find-links=/tmp/ssh-wheels --only-binary=:all: --require-hashes --no-cache-dir -r /tmp/ssh-requirements.lock \
    && python -I -c "import agent_ssh_service.hosting,fleet_manager.ssh_directory" \
    && rm -rf /tmp/ssh-wheels /tmp/ssh-requirements.lock \
    && mkdir /data && chown 65532:65532 /data
USER 65532:65532
ENV HOME=/tmp PYTHONDONTWRITEBYTECODE=1
WORKDIR /data
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "-I", "-m", "agent_ssh_service"]
CMD ["--config", "/run/config/ssh-service.json"]
