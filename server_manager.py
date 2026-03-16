"""
server_manager.py
-----------------
Paper-only server manager. Jar pre-cached at /opt/paper-cache/ in Docker image.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

import config
from utils import get_vps_ip, is_process_alive, logger

SERVERS_DIR  = Path("servers")
SERVERS_JSON = Path("servers.json")
PAPER_CACHE  = Path("/opt/paper-cache")

SERVERS_DIR.mkdir(exist_ok=True)

# ── ANSI strip ────────────────────────────────────────────────────────────────
_ANSI = re.compile(r"\x1B(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])")

def _strip_ansi(text: str) -> str:
    return _ANSI.sub("", text)


# ── JSON persistence ──────────────────────────────────────────────────────────

def _load() -> dict[str, Any]:
    if not SERVERS_JSON.exists():
        return {}
    with open(SERVERS_JSON) as f:
        try:
            return json.load(f)
        except json.JSONDecodeError:
            return {}

def _save(data: dict[str, Any]) -> None:
    with open(SERVERS_JSON, "w") as f:
        json.dump(data, f, indent=2)


# ── Status helpers ────────────────────────────────────────────────────────────

def refresh_statuses() -> None:
    data = _load()
    changed = False
    for srv in data.values():
        if srv["status"] == "running" and not is_process_alive(srv.get("pid")):
            srv["status"] = "stopped"
            srv["pid"] = None
            changed = True
    if changed:
        _save(data)

def list_servers() -> list[dict[str, Any]]:
    refresh_statuses()
    return list(_load().values())

def get_server(name: str) -> dict[str, Any] | None:
    refresh_statuses()
    return _load().get(name)


# ── Port allocation ───────────────────────────────────────────────────────────

def _next_port() -> int:
    used = {srv["port"] for srv in _load().values()}
    port = 25565
    while port in used:
        port += 1
    return port


# ── Launch helper ─────────────────────────────────────────────────────────────

def _launch(server_dir: Path) -> subprocess.Popen:
    log_file = open(server_dir / "server.log", "a")
    return subprocess.Popen(
        [
            "java",
            f"-Xms{config.SERVER_RAM_MIN}",
            f"-Xmx{config.SERVER_RAM_MAX}",
            "-jar", "server.jar",
            "nogui",
        ],
        cwd=str(server_dir),
        stdout=log_file,
        stderr=log_file,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )


# ── Create ────────────────────────────────────────────────────────────────────

def create_server(name: str, progress_callback=None) -> dict[str, Any]:
    data = _load()

    if name in data:
        raise ValueError(f"Server '{name}' already exists")
    if len(data) >= config.MAX_SERVERS:
        raise ValueError(f"Maximum {config.MAX_SERVERS} servers reached")

    def _p(msg: str) -> None:
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    # Verify cache exists
    cached_jar = PAPER_CACHE / "paper.jar"
    if not cached_jar.exists():
        raise FileNotFoundError(
            "paper.jar not found at /opt/paper-cache/paper.jar — rebuild the Docker image."
        )

    server_dir = SERVERS_DIR / name
    server_dir.mkdir(parents=True, exist_ok=True)

    # Copy jar
    _p("Copying Paper jar…")
    shutil.copy(cached_jar, server_dir / "server.jar")

    # Copy pre-warmed mojang cache if present
    cached_mc = PAPER_CACHE / "cache"
    if cached_mc.exists():
        _p("Restoring Mojang jar cache…")
        shutil.copytree(str(cached_mc), str(server_dir / "cache"), dirs_exist_ok=True)

    # Write config
    port = _next_port()
    (server_dir / "eula.txt").write_text("eula=true\n")
    (server_dir / "server.properties").write_text(
        f"server-port={port}\n"
        "gamemode=survival\n"
        "difficulty=normal\n"
        "max-players=20\n"
        "online-mode=true\n"
        "motd=A Minecraft Server\n"
    )

    # Start
    _p("Starting server process…")
    proc = _launch(server_dir)

    entry: dict[str, Any] = {
        "name":   name,
        "path":   str(server_dir),
        "pid":    proc.pid,
        "status": "running",
        "port":   port,
        "type":   "paper",
    }
    data[name] = entry
    _save(data)

    _p(f"Done — {get_vps_ip()}:{port}")
    return entry


# ── Start / Stop / Restart ────────────────────────────────────────────────────

def start_server(name: str) -> dict[str, Any]:
    data = _load()
    srv  = data.get(name)
    if not srv:
        raise KeyError(f"Server '{name}' not found")
    if is_process_alive(srv.get("pid")):
        raise RuntimeError(f"Server '{name}' is already running")

    server_dir = Path(srv["path"])
    if not (server_dir / "server.jar").exists():
        raise FileNotFoundError(f"server.jar missing in {server_dir}")

    proc = _launch(server_dir)
    srv["pid"]    = proc.pid
    srv["status"] = "running"
    _save(data)
    return srv


def stop_server(name: str) -> dict[str, Any]:
    data = _load()
    srv  = data.get(name)
    if not srv:
        raise KeyError(f"Server '{name}' not found")

    pid = srv.get("pid")
    if pid and is_process_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except (ProcessLookupError, OSError):
            pass

    srv["pid"]    = None
    srv["status"] = "stopped"
    _save(data)
    return srv


def restart_server(name: str) -> dict[str, Any]:
    stop_server(name)
    return start_server(name)


# ── Logs ──────────────────────────────────────────────────────────────────────

def get_logs(name: str, lines: int = 20) -> str:
    data = _load()
    srv  = data.get(name)
    if not srv:
        raise KeyError(f"Server '{name}' not found")

    server_dir = Path(srv["path"])

    # Paper writes to logs/latest.log — prefer that, fallback to server.log (our stdout redirect)
    candidates = [
        server_dir / "logs" / "latest.log",
        server_dir / "server.log",
    ]

    for path in candidates:
        if path.exists() and path.stat().st_size > 0:
            with open(path, errors="replace") as f:
                all_lines = f.readlines()
            tail = "".join(all_lines[-lines:])
            return _strip_ansi(tail).strip() or "(log exists but is empty)"

    return "(no logs yet — server may still be initialising)"


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_server(name: str) -> None:
    data = _load()
    srv  = data.get(name)
    if not srv:
        raise KeyError(f"Server '{name}' not found")

    pid = srv.get("pid")
    if pid and is_process_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except (ProcessLookupError, OSError):
            pass

    server_dir = Path(srv["path"])
    if server_dir.exists():
        shutil.rmtree(server_dir)

    del data[name]
    _save(data)
    logger.info("Deleted server '%s'", name)
