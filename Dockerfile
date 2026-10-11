# Chanakya — small image for scheduled, unattended runs. No GPU needed (Ollama Cloud).
FROM python:3.12-slim AS base

ARG INSTALL_OCR=false
# PostgreSQL driver (psycopg 3, LGPL-3.0). Off by default: SQLite needs nothing extra.
ARG INSTALL_POSTGRES=false
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    TZ=Asia/Kolkata CHANAKYA_CONFIG=/app/config/chanakya.yaml

RUN if [ "$INSTALL_OCR" = "true" ]; then \
      apt-get update && apt-get install -y --no-install-recommends tesseract-ocr tesseract-ocr-hin && \
      rm -rf /var/lib/apt/lists/*; \
    fi

WORKDIR /app
COPY pyproject.toml README.md ./
COPY src ./src
RUN pip install . && if [ "$INSTALL_OCR" = "true" ]; then pip install ".[ocr]"; fi &&     if [ "$INSTALL_POSTGRES" = "true" ]; then pip install ".[postgres]"; fi

COPY config ./config
RUN useradd --create-home --uid 10001 chanakya && mkdir -p /app/data && chown -R chanakya /app/data
USER chanakya
VOLUME ["/app/data"]

HEALTHCHECK --interval=30m --timeout=30s --start-period=2m CMD chanakya healthcheck --max-age-hours 36 || exit 1
ENTRYPOINT ["chanakya"]
CMD ["run", "--dry-run"]
