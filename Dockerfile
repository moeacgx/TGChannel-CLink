FROM python:3.11-slim

# Prevents Python from writing .pyc files and enables unbuffered logs
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Install dependencies first for better caching
COPY requirements.txt /app/requirements.txt

# Optional build-time settings for network-restricted environments
ARG PIP_INDEX_URL
ARG HTTP_PROXY
ARG HTTPS_PROXY
ARG NO_PROXY

# Use mirror if provided, otherwise default PyPI
RUN if [ -n "$PIP_INDEX_URL" ]; then \
      pip install --no-cache-dir -i "$PIP_INDEX_URL" -r /app/requirements.txt; \
    else \
      pip install --no-cache-dir -r /app/requirements.txt; \
    fi

# Copy source
COPY . /app

# Default command
CMD ["python", "-u", "simple_relay.py"]
