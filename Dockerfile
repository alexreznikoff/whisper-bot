FROM ghcr.io/astral-sh/uv:python3.13-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    HF_HOME=/root/.cache/huggingface

# Устанавливаем ffmpeg
RUN apt-get update && \
    apt-get install -y --no-install-recommends ffmpeg && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Сначала копируем только файлы зависимостей для кэширования слоя Docker
COPY pyproject.toml uv.lock* ./

# Устанавливаем зависимости в системный Python контейнера (или изолированный .venv)
RUN uv sync --no-dev --no-install-project

# Копируем код бота
COPY main.py .

# Запускаем через uv run (без повторной синхронизации)
CMD ["uv", "run", "--no-sync", "python", "main.py"]
