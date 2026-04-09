FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt update && apt install -y --no-install-recommends \
	sqlite3 \
	&& rm -rf /var/lib/apt/lists/*

# Enable BuildKit cache mounts for uv
# syntax=docker/dockerfile:1.7-labs

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY src /app/src

ENV UV_CACHE_DIR=/root/.cache/uv

RUN --mount=type=cache,target=/root/.cache/uv \
	uv pip install --system .

CMD ["botka"]
