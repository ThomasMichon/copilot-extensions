ARG PYTHON_BASE
FROM ${PYTHON_BASE}
ARG PYTHON_BASE
ARG NATIVE_RELAY=false
USER 0
RUN python -I -c "import os,re; assert re.fullmatch(r'[^@]+@sha256:[0-9a-f]{64}', os.environ['PYTHON_BASE'])"
RUN python -I -c "import os,pathlib,pwd; mode=os.environ['NATIVE_RELAY']; assert mode in ('true','false'); assert mode=='false' or (all(pathlib.Path(p).is_file() and os.access(p,os.X_OK) for p in ('/usr/sbin/sshd','/usr/bin/ssh-keygen','/bin/false')) and pathlib.Path('/run/sshd').is_dir() and pwd.getpwuid(65532).pw_name != 'root')"
RUN set -eu; \
    test ! -L /data; \
    if ! getent group 65532 >/dev/null; then groupadd --gid 65532 agent-ssh-service; fi; \
    if getent passwd 65532 >/dev/null; then \
        account="$(getent passwd 65532 | cut -d: -f1)"; \
        usermod --gid 65532 --home /data "$account"; \
    else \
        useradd --uid 65532 --gid 65532 --home-dir /data --no-create-home \
            --shell /usr/sbin/nologin agent-ssh-service; \
    fi; \
    install -d -m 700 -o 65532 -g 65532 /data
COPY wheels/ /tmp/ssh-wheels/
COPY requirements.lock /tmp/ssh-requirements.lock
RUN python -I -m pip install --no-index --find-links=/tmp/ssh-wheels --only-binary=:all: --require-hashes --no-cache-dir -r /tmp/ssh-requirements.lock \
    && python -I -c "import agent_ssh_service.hosting,fleet_manager.ssh_directory" \
    && rm -rf /tmp/ssh-wheels /tmp/ssh-requirements.lock
USER 65532:65532
ENV HOME=/data PYTHONDONTWRITEBYTECODE=1
WORKDIR /data
STOPSIGNAL SIGTERM
ENTRYPOINT ["python", "-I", "-m", "agent_ssh_service"]
CMD ["--config", "/run/config/ssh-service.json"]
