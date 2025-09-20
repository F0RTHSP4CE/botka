# F0RTHSP4CE Telegram Bot

<p>
<a href="https://t.me/F0RTHSP4CE_bot"><img alt="Telegram Bot" src="https://img.shields.io/badge/Telegram-%40F0RTHSP4CE__bot-blue?logo=telegram"></a>
<a href="https://t.me/c/1900643629/7882"><img alt="Internal Discussion Topic" src="https://img.shields.io/badge/Internal_Discussion_Topic-Internal_issue_bot-blue?logo=data%3Aimage%2Fgif%3Bbase64%2CR0lGODlhEAAQAPQBAAAAAAEBASoUBQENN0sLA28TA05REn9%2BGwEVUQIaY4kYBLJICX6CGYuKHZ2cIbmaKamqIv%2BhHtTSK%2FP0MgIkijMviAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAACH5BAEAAAAALAAAAAAQABAAAAVWICCOQRmMKGmuKRAka3ySAwXLs2siFIKLsgRFppPVBrhkJRkrLCIPA7OgCDQmDImgVCQoCAEDRCJxcHWFQkkwmUhNIwHYdJ00pCicQzI55FRELYB%2FIiEAOw%3D%3D"></a>
<a href="https://wiki.f0rth.space/en/residents/telegram-bot"><img alt="Wiki" src="https://img.shields.io/badge/Wiki-Project_Page-blue?logo=wikidotjs"></a>
<a href="http://10.0.24.18:42777"><img alt="HTTP API" src="https://img.shields.io/badge/HTTP_API-10.0.24.18%3A42777-blue?logo=openapiinitiative"></a>
<a href="https://grafana.lo.f0rth.space/d/cbdbf909-7f4d-409b-9e6d-07dff89b3a10/botka"><img alt="Grafana Dashboard" src="https://img.shields.io/badge/Grafana_Dashboard-Botka-blue?logo=grafana"></a>
<img alt "License: Unlicense OR MIT" src="https://img.shields.io/badge/License-Unlicense%20OR%20MIT-blue?logo=unlicense">
</p>

## Build

This project uses Nix flakes to manage dependencies, ensuring a reliable and reproducible build environment. To get started:

1. Install Nix or NixOS by following instructions at [nixos.org/download](https://nixos.org/download).
2. Enable Nix flakes as per the guide on [NixOS Wiki](https://nixos.wiki/wiki/Flakes#Enable_flakes).

Alternatively, install `Cargo` and `Rust` using the instructions found at [Rust's official site](https://doc.rust-lang.org/cargo/getting-started/installation.html).

To build the project:

- For a release build, run `nix build`. The resulting binary can be found at `./result/bin/f0bot`.
- For setting up a development environment with necessary dependencies, run `nix develop`. Inside this environment, you can compile the project with `cargo build`.

## Running the Bot Locally

### Prerequisites

1. **Install diesel_cli with SQLite support:**
   ```bash
   cargo install diesel_cli --no-default-features --features sqlite
   ```

2. **Create local directory and configuration:**
   ```bash
   mkdir local
   cp config.example.yaml local/config.yaml
   ```

3. **Set up the database:**
   ```bash
   cd local
   diesel migration run --database-url db.sqlite3 --migration-dir ../migrations
   ```

### Bot Setup

1. Use [@BotFather](https://t.me/BotFather) to create a new Telegram bot, create a test chat with topics, and add the bot as an administrator.
2. Edit `local/config.yaml` and adjust it as needed, particularly the `telegram.token`.

### Running the Bot

To start the bot, run from the `local` directory:
```bash
cd local
cargo run bot ./config.yaml
```

Alternatively, you can build and run the release version:
```bash
cargo build --release
cd local
../target/release/f0bot bot ./config.yaml
```

Or using the Nix build:
```bash
nix build
cd local
../result/bin/f0bot bot ./config.yaml
```

## Bot Commands

### Basic Commands
- `/help` - Display all available commands
- `/status` - Show bot status
- `/version` - Show bot version
- `/topics` - Show topic list (private chat only)
- `/count` - Count devices online (via Mikrotik)

### Resident Commands (*)
These commands are available only to residents:

#### General
- `/residents` - List current residents
- `/residents_admin_table` - Show residents admin table
- `/residents_timeline` - Show residents timeline

#### Shopping List
- `/needs` - Show shopping list
- `/need <item>` - Add an item to the shopping list

#### User Control
- `/userctl` - Control personal configuration
  - `--add-mac XX:XX:XX:XX:XX:XX` - Add MAC address for presence detection
  - `--remove-mac XX:XX:XX:XX:XX:XX` - Remove MAC address
  - `--help` - Show userctl command help
- `/add_ssh <public_key>` - Add an SSH public key for yourself
- `/get_ssh <username>` - Get SSH public keys of a user by username

#### LDAP Integration
- `/ldap_register <args>` - Register in LDAP system
- `/ldap_reset_password` - Reset your LDAP password
- `/ldap_update <args>` - Update LDAP settings

#### Utilities
- `/tldr` - Summarize long discussion (TL;DR)
- `/racovina` - Show racovina camera image (in resident chat)
- `/hlam` - Show hlam camera image (in resident chat)
- `/open` - Open the door
- `/temp_open` - Generate a temporary guest door access link

### Admin Commands (**)
These commands are available only to bot technicians/admins:

- `/add_resident <username|ID>` - Add a user as a resident
- `/remove_resident <username|ID>` - Remove a user from residents
- `/broadcast` - Broadcast a message to all residents (use as a reply to the message you want to send)
- `/debug_update_dashboard <args>` - Debug dashboard update

### Notes
- Commands marked with * are available only to residents
- Commands marked with ** are available only to bot technicians/admins
- The bot supports various internal features like MAC address monitoring for presence detection
- For more details on specific commands, use the command with `--help` flag where available

## Development

### Linting and Code Quality

This project uses several tools to maintain code quality and consistency. Run the following command to check all linting rules and run tests:

```bash
just check
```

This command performs:
- **Nix linting**: `deadnix` and `statix` for Nix expressions
- **Python linting**: `mypy` type checking and `ruff` linting
- **Rust linting**: `cargo clippy` with strict warnings enabled
- **Tests**: Full Rust test suite

### Code Formatting

To format all code in the project:

```bash
just fmt
```

This formats Rust, Nix, Python, YAML, JSON, Markdown, and TypeScript files.

### Database Schema

To regenerate the database schema after migrations:

```bash
just schema
```

## Development Conventions

This project follows these conventions:

- **Code Style and Lints**: Refer to the [`./Justfile`](./Justfile).
- **Commit Messages**: [Conventional Commits](https://www.conventionalcommits.org/en/v1.0.0/).
- **Metric Naming**: [Prometheus metric and label naming guidelines](https://prometheus.io/docs/practices/naming/).
