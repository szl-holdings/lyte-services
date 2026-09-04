FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PORT=7860 \
    LYTE_STATE_PATH=/tmp/lyte-enterprise.sqlite3
ARG LYTE_SOURCE_REVISION=UNAVAILABLE
ENV LYTE_SOURCE_REVISION=${LYTE_SOURCE_REVISION}

WORKDIR /app
COPY requirements.txt ./requirements.txt
RUN python -m pip install --disable-pip-version-check --no-cache-dir -r requirements.txt

COPY a11oy_factory ./a11oy_factory
COPY lyte_api ./lyte_api
COPY lyte_engine ./lyte_engine
COPY lyte_runtime.py ./lyte_runtime.py
COPY space ./space
RUN printf '%s\n' "$LYTE_SOURCE_REVISION" > /app/source_revision.txt \
    && useradd --create-home --uid 1000 --shell /usr/sbin/nologin lyte \
    && chown -R lyte:lyte /app /tmp
USER lyte

EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/healthz', timeout=4).read()"

CMD ["uvicorn", "space.server:app", "--host", "0.0.0.0", "--port", "7860", "--no-server-header"]
