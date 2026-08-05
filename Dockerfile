FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim

RUN apt update && apt install -y --no-install-recommends \
	sqlite3 \
	&& rm -rf /var/lib/apt/lists/*

WORKDIR /app

ENV UV_CACHE_DIR=/root/.cache/uv
ENV UV_LINK_MODE=copy
ENV PATH="/app/.venv/bin:$PATH"

COPY pyproject.toml uv.lock README.md /app/

# Cache downloaded packages separately from the frequently changing source.
RUN --mount=type=cache,target=/root/.cache/uv \
	uv sync --locked --no-install-project

COPY src /app/src

RUN --mount=type=cache,target=/root/.cache/uv \
	uv sync --locked --no-editable

CMD ["botka"]
