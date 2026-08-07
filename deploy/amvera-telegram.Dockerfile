FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN groupadd --system agency && \
    useradd --system --gid agency agency && \
    mkdir -p /data && \
    chown agency:agency /data

COPY pyproject.toml README.md ./
COPY app ./app

RUN python -m pip install --upgrade pip && \
    python -m pip install .

USER agency

CMD ["python", "-m", "app.telegram.bot"]
