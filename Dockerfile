FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
COPY eval ./eval
RUN pip install --no-cache-dir ".[mcp]"

RUN useradd --create-home app && mkdir /data && chown app /data
USER app
ENV SQ_DB_PATH=/data/shop.db
EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=3s CMD python -c "import urllib.request as u; u.urlopen('http://127.0.0.1:8000/api/health')"
CMD ["sh", "-c", "[ -f \"$SQ_DB_PATH\" ] || sqlscout init-db; exec sqlscout serve --host 0.0.0.0 --port 8000"]
