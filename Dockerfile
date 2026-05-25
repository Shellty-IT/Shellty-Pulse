FROM python:3.12-slim

LABEL maintainer="Shellty IT" \
      description="Shellty Pulse — Service Health Monitor" \
      version="1.0.0" \
      org.opencontainers.image.title="Shellty Pulse" \
      org.opencontainers.image.source="https://github.com/Shellty-IT/Shellty-Pulse"

RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

RUN groupadd -r pulse && \
    useradd -r -g pulse -d /app -s /sbin/nologin pulse

WORKDIR /app

ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY --chown=pulse:pulse app.py .
COPY --chown=pulse:pulse pulse/ ./pulse/

USER pulse

EXPOSE 5000
STOPSIGNAL SIGTERM

ENV PORT=5000 \
    PING_INTERVAL=900 \
    REQUEST_TIMEOUT=90 \
    MAX_SERVICES=50 \
    PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:5000/health || exit 1

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "2", "--timeout", "30", "--graceful-timeout", "10", "--access-logfile", "-", "app:app"]
