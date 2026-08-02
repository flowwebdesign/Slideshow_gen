FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    SLIDESHOW_DATA_DIR=/data

RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-liberation \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml ./
RUN pip install --upgrade pip && pip install ".[test]"

COPY app ./app
COPY tests ./tests
COPY README.md DEPLOYMENT_NEXT.md Makefile ./

RUN addgroup --system --gid 10001 slideshow \
    && adduser --system --uid 10001 --ingroup slideshow --home /app slideshow \
    && mkdir -p /data/jobs \
    && chown -R slideshow:slideshow /app /data

USER slideshow
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/', timeout=3)" || exit 1

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--no-access-log"]

