# syntax=docker/dockerfile:1
# ----------------------------------------------------------------------------
# QueueStorm Ticket Sorter — production image
# Small, dependency-light, runs as a non-root user, honours $PORT.
# ----------------------------------------------------------------------------
FROM python:3.12-slim

# Faster, cleaner Python in containers.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=8000 \
    WEB_CONCURRENCY=1

WORKDIR /app

# Install dependencies first so this layer is cached across code changes.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Application code.
COPY app ./app

# Run as an unprivileged user.
RUN useradd --create-home --uid 10001 appuser
USER appuser

EXPOSE 8000

# Container-native healthcheck (no curl needed in slim images).
HEALTHCHECK --interval=30s --timeout=5s --start-period=5s --retries=3 \
    CMD python -c "import os,urllib.request,sys; \
url='http://127.0.0.1:'+os.getenv('PORT','8000')+'/health'; \
sys.exit(0 if urllib.request.urlopen(url, timeout=4).status==200 else 1)"

# Bind to 0.0.0.0 and the platform-provided $PORT (Render/Railway/Fly/EC2).
# WEB_CONCURRENCY worker processes use multiple cores; --proxy-headers makes the
# real client IP (from the platform's X-Forwarded-For) available to rate limiting.
CMD ["sh", "-c", "exec uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000} --workers ${WEB_CONCURRENCY:-1} --proxy-headers --forwarded-allow-ips='*'"]
