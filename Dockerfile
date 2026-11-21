FROM python:3.12-slim

RUN apt-get update \
 && apt-get install -y --no-install-recommends ffmpeg fonts-dejavu-core fonts-noto-core tini \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY pyproject.toml README.md LICENSE NOTICE ./
COPY src ./src
RUN pip install --no-cache-dir ".[download,whisper,postgres,mcp]"

RUN useradd --create-home --uid 10001 reelwright && mkdir -p /data && chown reelwright /data
USER reelwright
ENV REELWRIGHT_DATA_DIR=/data HF_HOME=/data/models
VOLUME /data
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/healthz', timeout=4)"
ENTRYPOINT ["tini", "--"]
CMD ["reelwright", "serve", "--host", "0.0.0.0", "--port", "8000"]
