# Lyte Enterprise Signal Lattice. Runtime actions remain human-sovereign.
FROM python:3.12-slim@sha256:423ed6ab25b1921a477529254bfeeabf5855151dc2c3141699a1bfc852199fbf

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PIP_NO_CACHE_DIR=1 \
    XDG_CACHE_HOME=/tmp/lyte-cache \
    PORT=7860 \
    LYTE_ENV=development \
    DATABASE_URL=sqlite+pysqlite:////data/lyte.db \
    LYTE_DEMO_MODE=true \
    LYTE_REQUIRE_SOURCE_BINDING=true

WORKDIR /app
RUN groupadd --system --gid 10001 lyte \
    && useradd --system --uid 10001 --gid lyte --home-dir /nonexistent --shell /usr/sbin/nologin lyte \
    && mkdir -p /data /tmp/lyte-cache \
    && chown 10001:10001 /data /tmp/lyte-cache

COPY requirements.txt ./requirements.txt
RUN python -m pip install --requirement requirements.txt \
    && python -m pip check

COPY lyte ./lyte
COPY lyte_engine ./lyte_engine
COPY lyte_api ./lyte_api
COPY space ./space
COPY alembic.ini README.md LICENSE NOTICE ./
COPY migrations ./migrations
ARG LYTE_SOURCE_REVISION=UNAVAILABLE
ENV LYTE_SOURCE_REVISION=${LYTE_SOURCE_REVISION}
COPY source_revision.txt ./source_revision.txt
RUN python -c "import os,pathlib,re; p=pathlib.Path('source_revision.txt'); marker=p.read_text(encoding='utf-8').strip().lower(); build=os.environ.get('LYTE_SOURCE_REVISION','').strip().lower(); valid=lambda value: re.fullmatch(r'[0-9a-f]{40}',value) is not None; assert valid(build) or valid(marker), 'exact source revision is required'; assert not (valid(build) and valid(marker) and build != marker), 'source revisions disagree'; p.write_text((build if valid(build) else marker)+'\n',encoding='utf-8')"

USER 10001:10001
EXPOSE 7860
HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
  CMD python -c "import os,urllib.request; urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','7860')+'/readyz', timeout=3).read()"
CMD ["python", "-m", "uvicorn", "lyte.app:app", "--host", "0.0.0.0", "--port", "7860", "--no-access-log"]
