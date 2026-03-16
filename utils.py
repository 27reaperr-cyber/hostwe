import os
import socket
import sqlite3
import logging
from datetime import datetime
from pathlib import Path

DB_PATH = Path("users.db")

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(message)s",
    level=logging.INFO,
)
logger = logging.getLogger(__name__)


# ── Database ──────────────────────────────────────────────────────────────────

def init_db() -> None:
    with sqlite3.connect(DB_PATH) as con:
        con.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                user_id   INTEGER PRIMARY KEY,
                username  TEXT,
                joined_at TEXT NOT NULL,
                role      TEXT NOT NULL DEFAULT 'user'
            )
            """
        )
        con.commit()
    logger.info("Database initialised at %s", DB_PATH)


def register_user(user_id: int, username: str | None) -> None:
    with sqlite3.connect(DB_PATH) as con:
        existing = con.execute(
            "SELECT user_id FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
        if not existing:
            con.execute(
                "INSERT INTO users (user_id, username, joined_at) VALUES (?, ?, ?)",
                (user_id, username or "", datetime.utcnow().isoformat()),
            )
            con.commit()
            logger.info("Registered new user: %s (%s)", username, user_id)


def get_user_role(user_id: int) -> str:
    with sqlite3.connect(DB_PATH) as con:
        row = con.execute(
            "SELECT role FROM users WHERE user_id = ?", (user_id,)
        ).fetchone()
    return row[0] if row else "unknown"


# ── Network ───────────────────────────────────────────────────────────────────

def get_vps_ip() -> str:
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        s.connect(("8.8.8.8", 80))
        ip = s.getsockname()[0]
        s.close()
        return ip
    except Exception:
        return "unknown"


# ── Process helpers ───────────────────────────────────────────────────────────

def is_process_alive(pid: int | None) -> bool:
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


# ── Text helpers ──────────────────────────────────────────────────────────────

def status_label(status: str) -> str:
    return "running" if status == "running" else "stopped"
