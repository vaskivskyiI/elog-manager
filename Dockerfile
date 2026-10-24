FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY elogmgr ./elogmgr
# State (backups, change log, session key). Sticky and world-writable because the
# container runs as whatever uid owns elogd.cfg; docker-compose.yml mounts a volume here.
RUN mkdir /state && chmod 1777 /state
ENV ELOGMGR_STATE_DIR=/state

# docker-compose.yml overrides this with the uid/gid that owns elogd.cfg
USER 1000:1000
EXPOSE 8090
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8090/api/me', timeout=4)"

ENTRYPOINT ["python", "-m", "elogmgr", "--host", "0.0.0.0", "--port", "8090"]
