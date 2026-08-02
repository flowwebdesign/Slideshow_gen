FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SLIDESHOW_DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends curl ffmpeg fonts-dejavu-core fonts-lato fonts-liberation fonts-noto-core gosu \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[test]"

COPY app ./app
COPY tests ./tests
COPY README.md DEPLOYMENT_NEXT.md Makefile ./
COPY docker-entrypoint.sh /usr/local/bin/docker-entrypoint.sh

RUN python -c "from app.image_processing import heif_decoder_available; assert heif_decoder_available(), 'HEIC/HEIF decoder unavailable'"

RUN addgroup --system --gid 10001 slideshow \
    && adduser --system --uid 10001 --ingroup slideshow --home /app slideshow \
    && mkdir -p /data/jobs \
    && chown -R slideshow:slideshow /app /data \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD curl --fail --silent --show-error http://127.0.0.1:8000/health || exit 1

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--proxy-headers", "--forwarded-allow-ips=*", "--no-access-log"]
