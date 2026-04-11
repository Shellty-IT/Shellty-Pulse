# ===========================================
# Shellty Pulse — Service Health Monitor
# ===========================================

FROM python:3.12-slim

# --- OCI Metadata ---
LABEL maintainer="Shellty IT" \
      description="Shellty Pulse — Service Health Monitor" \
      version="1.0.0" \
      org.opencontainers.image.title="Shellty Pulse" \
      org.opencontainers.image.source="https://github.com/YOUR-REPO"

# --- System dependencies ---
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- Non-root user ---
RUN groupadd -r pulse && \
    useradd -r -g pulse -d /app -s /sbin/nologin pulse

WORKDIR /app

# --- Python dependencies ---
# Versions must match CI workflow (.github/workflows/ci.yml)
ENV PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

RUN pip install \
    flask==3.1.1 \
    apscheduler==3.10.4 \
    requests==2.32.3 \
    gunicorn==23.0.0

# --- Application code ---
COPY --chown=pulse:pulse app.py .
COPY --chown=pulse:pulse pulse/ ./pulse/

# --- Switch to non-root ---
USER pulse

# --- Runtime configuration ---
EXPOSE 5000
STOPSIGNAL SIGTERM

ENV PORT=5000 \
    PING_INTERVAL=900 \
    REQUEST_TIMEOUT=10 \
    MAX_SERVICES=50 \
    PYTHONUNBUFFERED=1

HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:5000/health || exit 1

# NOTE: Must use --workers 1 (in-memory state + background scheduler)
CMD ["gunicorn", "--bind", "0.0.0.0:5000", \
     "--workers", "1", "--threads", "2", \
     "--timeout", "30", "--graceful-timeout", "10", \
     "--access-logfile", "-", \
     "app:app"]