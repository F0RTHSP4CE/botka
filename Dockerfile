FROM python:3.12-slim

WORKDIR /app

# Install uv
COPY --from=ghcr.io/astral-sh/uv:latest /uv /uvx /bin/

# Copy project files
COPY pyproject.toml .
COPY botka botka/

# Install dependencies with uv
RUN uv sync --frozen --no-dev

# Create data directory for SQLite database
RUN mkdir -p /data

ENV PYTHONUNBUFFERED=1

CMD ["uv", "run", "python", "-m", "botka"]
