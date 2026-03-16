# Minecraft Server Manager Bot

A minimalist Telegram bot to manage Minecraft servers on a Linux VPS.

---

## Features

- Create Paper / Vanilla / Spigot servers with one click
- Start, stop, restart, view logs, delete servers
- Auto-download of latest server jars
- SQLite user registry (`users.db`)
- Inline keyboard — all navigation edits the same message
- Up to 5 concurrent servers

---

## Prerequisites

| Tool | Version |
|------|---------|
| Python | 3.11+ |
| Java (OpenJDK) | 17+ |
| pip | latest |

---

## Quick start (local)

### 1. Clone / copy the project

```bash
git clone <repo> mc-bot && cd mc-bot
```

### 2. Configure `.env`

```bash
cp .env .env.local   # or just edit .env directly
```

Open `.env` and set:

```
BOT_TOKEN=YOUR_TELEGRAM_BOT_TOKEN
MAX_SERVERS=5
SERVER_RAM_MIN=1G
SERVER_RAM_MAX=2G
```

Get a token from [@BotFather](https://t.me/BotFather).

### 3. Install Python dependencies

```bash
pip install -r requirements.txt
```

### 4. Run the bot

```bash
python bot.py
```

---

## Docker

### Build

```bash
docker build -t mc-bot .
```

> The image installs OpenJDK 17 automatically — no separate Java setup needed.

### Run

```bash
docker run -d \
  --name mc-bot \
  --env-file .env \
  -v $(pwd)/servers:/app/servers \
  -v $(pwd)/servers.json:/app/servers.json \
  -v $(pwd)/users.db:/app/users.db \
  mc-bot
```

Mounting the volumes keeps server files and bot data persistent across container restarts.

---

## Project structure

```
mc-bot/
├── bot.py             # Telegram bot — menus, handlers, user registration
├── server_manager.py  # Server lifecycle (create, start, stop, delete, logs)
├── config.py          # Loads .env variables
├── utils.py           # DB helpers, IP detection, process checks
├── servers.json       # Runtime server registry
├── users.db           # SQLite user database (auto-created)
├── .env               # Environment variables (not committed)
├── Dockerfile
├── requirements.txt
└── servers/           # Minecraft server directories
```

---

## Bot commands

| Command | Description |
|---------|-------------|
| `/start` | Open main menu and register user |

All other actions are performed via inline keyboard buttons.

---

## Notes

- Servers are launched as child processes of the bot (or Docker container).
- If the bot restarts, it detects dead PIDs and marks servers as *stopped*.
- Spigot build takes ~5 minutes the first time (BuildTools compiles from source).
- Ports are assigned starting from `25565` and incrementing per server.
