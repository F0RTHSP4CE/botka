# F0RTHSP4CE Telegram Bot (Python Version)

A simplified Python implementation of the F0RTHSP4CE Telegram bot, designed to run in Docker.

## Features

### Core Commands
- `/help` - Display available commands
- `/version` - Show bot version
- `/status` - Show bot status
- `/count` - Count devices online (via Mikrotik)

### Resident Commands (requires residency)
- `/residents` - List current residents
- `/needs` - Show shopping list
- `/need <item>` - Add item to shopping list
- `/open` - Open the door (Butler integration)
- `/racovina` - Show racovina camera image

### User Settings
- `/userctl` - Show your settings
- `/add_mac XX:XX:XX:XX:XX:XX` - Add MAC address for presence detection
- `/remove_mac XX:XX:XX:XX:XX:XX` - Remove MAC address

## Quick Start

### 1. Configure the bot

```bash
cp config.example.yaml config.yaml
# Edit config.yaml with your settings
```

Key configuration:
- `telegram.token` - Your bot token from [@BotFather](https://t.me/BotFather)
- `telegram.admins` - List of admin Telegram user IDs
- `telegram.chats.residential` - List of residential chat IDs
- `services.mikrotik` - Mikrotik router credentials (for device counting)

### 2. Run with Docker Compose

```bash
docker-compose up -d
```

### 3. View logs

```bash
docker-compose logs -f
```

## Development

### Local Setup with uv (recommended)

```bash
# Install uv if not already installed
curl -LsSf https://astral.sh/uv/install.sh | sh

# Sync dependencies
uv sync

# Copy and configure
cp config.example.yaml config.yaml
# Edit config.yaml

# Run
uv run python -m botka
```

### Local Setup with pip

```bash
# Create virtual environment
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -e .

# Copy and configure
cp config.example.yaml config.yaml
# Edit config.yaml

# Run
python -m botka
```

### Project Structure

```
python-version/
├── Dockerfile
├── docker-compose.yml
├── requirements.txt
├── config.example.yaml
├── README.md
└── botka/
    ├── __init__.py
    ├── __main__.py      # Entry point
    ├── bot.py           # Main bot logic and handlers
    ├── config.py        # Configuration management
    ├── db.py            # Database models (SQLAlchemy)
    └── services.py      # External services (Mikrotik, camera, etc.)
```

## Configuration Reference

See [config.example.yaml](config.example.yaml) for all available options.

### Environment Variables

- `CONFIG_PATH` - Path to config file (default: `config.yaml`)
- `DB_PATH` - Path to SQLite database (default: `db.sqlite3`)

## Differences from Rust Version

This Python version is a **simplified implementation** focusing on core functionality:

**Included:**
- Basic commands (help, version, status, count)
- Resident management and listing
- Shopping list (needs)
- MAC address monitoring for presence detection
- Door opening (Butler)
- Camera image fetching
- User settings (MAC management)

**Not Included:**
- NLP/AI features
- LDAP integration
- Wiki.js integration
- Borrowed items tracking
- Dashboard
- Polls
- Message forwarding
- Vortex of doom
- TL;DR summaries
- Broadcast messages

## License

Dual-licensed under Unlicense OR MIT.
