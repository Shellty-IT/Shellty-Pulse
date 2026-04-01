# ===========================================
# Shellty Pulse — Service Health Monitor
# Optimized Docker image with security best practices
# ===========================================

# --- Base image ---
FROM python:3.12-slim

# --- Metadata ---
LABEL maintainer="Shellty IT" \
      description="Shellty Pulse — Service Health Monitor" \
      version="1.0"

# --- System dependencies ---
# curl is needed for Docker HEALTHCHECK (not included in slim)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- Non-root user for security ---
RUN groupadd -r pulse && \
    useradd -r -g pulse -d /app -s /sbin/nologin pulse

# --- Working directory ---
WORKDIR /app

# --- Python dependencies ---
# Pinned versions for reproducible builds
# gunicorn as production WSGI server
RUN pip install --no-cache-dir \
    flask==3.1.1 \
    apscheduler==3.10.4 \
    requests==2.32.3 \
    gunicorn==23.0.0

# --- Application code ---
COPY --chown=pulse:pulse app.py .

# --- Switch to non-root user ---
USER pulse

# --- Port exposure ---
EXPOSE 5000

# --- Health check ---
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:5000/health || exit 1

# --- Default environment variables ---
ENV PING_INTERVAL=600 \
    REQUEST_TIMEOUT=10 \
    PYTHONUNBUFFERED=1

# --- Run with production WSGI server ---
# Single worker because app uses in-memory state with threading
# 2 threads for concurrent request handling
CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "2", "--access-logfile", "-", "app:app"]