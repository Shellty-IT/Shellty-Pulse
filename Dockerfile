# ============================================
# Shellty Pulse — Service Health Monitor
# Multi-stage optimized Docker image
# ============================================

# --- Base image ---
FROM python:3.12-slim

# --- Metadata ---
LABEL maintainer="Shellty IT"
LABEL description="Shellty Pulse — Service Health Monitor"
LABEL version="1.0"

# --- System dependencies ---
# curl is needed for Docker HEALTHCHECK (not included in slim)
RUN apt-get update && \
    apt-get install -y --no-install-recommends curl && \
    apt-get clean && \
    rm -rf /var/lib/apt/lists/*

# --- Working directory ---
WORKDIR /app

# --- Python dependencies ---
# Installed inline (no requirements.txt needed for 3 packages)
# Pinned versions for reproducible builds
RUN pip install --no-cache-dir \
    flask==3.1.1 \
    apscheduler==3.10.4 \
    requests==2.32.3

# --- Application code ---
COPY app.py .

# --- Port exposure ---
EXPOSE 5000

# --- Health check ---
# Docker will automatically monitor container health
# Checks every 30s, timeout 10s, retries 3 times before marking unhealthy
HEALTHCHECK --interval=30s --timeout=10s --retries=3 --start-period=15s \
    CMD curl -f http://localhost:5000/health || exit 1

# --- Default environment variables ---
ENV PING_INTERVAL=600 \
    REQUEST_TIMEOUT=10 \
    PYTHONUNBUFFERED=1

# --- Run application ---
CMD ["python", "app.py"]