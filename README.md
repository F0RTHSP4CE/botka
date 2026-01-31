# Botka

Telegram bot backend with async handlers, SQLAlchemy, and Dishka DI.

## Requirements
- Python 3.12+
- uv
- Docker (optional)

## Setup
1) Copy env file:

```
cp .env.example .env
```

2) Edit `.env` with your bot token and database URL (SQLite by default).

## Run locally with uv
```
uv sync
uv run botka
```

## Run with Docker Compose
```
docker compose up --build
```
