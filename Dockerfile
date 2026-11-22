FROM python:3.14.10-slim-trixie AS builder

COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_PREFERENCE=only-system

WORKDIR /app

COPY pyproject.toml uv.lock .python-version ./
RUN uv sync --locked --no-dev --no-install-project --no-cache


FROM python:3.14.10-slim-trixie

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PATH="/app/.venv/bin:$PATH"

RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY --from=builder /app/.venv /app/.venv
COPY main.py .

CMD ["python", "main.py"]
