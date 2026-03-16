"""
server_manager.py
-----------------
Handles creation, lifecycle and deletion of Minecraft servers.
Only Paper is supported. The jar is pre-cached in /opt/paper-cache/paper.jar
at Docker build time — no downloads happen at runtime.
Servers are tracked in servers.json.
"""

from __future__ import annotations

import json
import os
import shutil
import signal
import subprocess
from pathlib import Path
from typing import Any

import config
from utils import get_vps_ip, is_process_alive, logger

SERVERS_DIR = Path("servers")
SERVERS_JSON = Path("servers.json")
PAPER_CACHE = Path("/opt/paper-cache")
SERVERS_DIR.mkdir(exist_ok=True)

# ── JSON persistence ──────────────────────────────────────────────────────────

def _load() -> dict[str, Any]:
    if not SERVERS_JSON.exists():
        return {}
    with open(SERVERS_JSON) as f:
        return json.load(f)


def _save(data: dict[str, Any]) -> None:
    with open(SERVERS_JSON, "w") as f:
        json.dump(data, f, indent=2)


# ── Status helpers ────────────────────────────────────────────────────────────

def refresh_statuses() -> None:
    """Set status=stopped for any server whose process is no longer alive."""
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


# ── Create ────────────────────────────────────────────────────────────────────

def create_server(name: str, progress_callback=None) -> dict[str, Any]:
    """
    Create and start a new Paper server.
    Uses the pre-cached jar from /opt/paper-cache/paper.jar (built into image).
    progress_callback(msg: str) is called with status updates if provided.
    """
    data = _load()

    if name in data:
        raise ValueError(f"Server '{name}' already exists")
    if len(data) >= config.MAX_SERVERS:
        raise ValueError(f"Maximum number of servers ({config.MAX_SERVERS}) reached")

    def _progress(msg: str) -> None:
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    # ── Copy jar from cache ───────────────────────────────────────────────────
    cached_jar = PAPER_CACHE / "paper.jar"
    if not cached_jar.exists():
        raise FileNotFoundError(
            "Paper jar not found in /opt/paper-cache/paper.jar. "
            "Rebuild the Docker image."
        )

    server_dir = SERVERS_DIR / name
    server_dir.mkdir(parents=True, exist_ok=True)
    _progress("Copying Paper jar…")
    shutil.copy(cached_jar, server_dir / "server.jar")

    # Copy pre-warmed Paper cache (contains mojang jar) if present
    cached_paper_cache = PAPER_CACHE / "cache"
    if cached_paper_cache.exists():
        _progress("Restoring cached Mojang jar…")
        shutil.copytree(cached_paper_cache, server_dir / "cache", dirs_exist_ok=True)

    # ── Config files ──────────────────────────────────────────────────────────
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

    # ── Start process ─────────────────────────────────────────────────────────
    _progress("Starting server…")
    log_file = open(server_dir / "server.log", "a")
    proc = subprocess.Popen(
        [
            "java",
            f"-Xms{config.SERVER_RAM_MIN}",
            f"-Xmx{config.SERVER_RAM_MAX}",
            "-jar",
            "server.jar",
            "nogui",
        ],
        cwd=str(server_dir),
        stdout=log_file,
        stderr=log_file,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    entry: dict[str, Any] = {
        "name": name,
        "path": str(server_dir),
        "pid": proc.pid,
        "status": "running",
        "port": port,
        "type": "paper",
    }
    data[name] = entry
    _save(data)

    _progress(f"Server '{name}' started on {get_vps_ip()}:{port}")
    return entry


# ── Start / Stop / Restart ────────────────────────────────────────────────────

def start_server(name: str) -> dict[str, Any]:
    data = _load()
    srv = data.get(name)
    if not srv:
        raise KeyError(f"Server '{name}' not found")
    if is_process_alive(srv.get("pid")):
        raise RuntimeError(f"Server '{name}' is already running")

    server_dir = Path(srv["path"])
    jar_path = server_dir / "server.jar"
    if not jar_path.exists():
        raise FileNotFoundError(f"server.jar not found in {server_dir}")

    log_file = open(server_dir / "server.log", "a")
    proc = subprocess.Popen(
        [
            "java",
            f"-Xms{config.SERVER_RAM_MIN}",
            f"-Xmx{config.SERVER_RAM_MAX}",
            "-jar",
            "server.jar",
            "nogui",
        ],
        cwd=str(server_dir),
        stdout=log_file,
        stderr=log_file,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )

    srv["pid"] = proc.pid
    srv["status"] = "running"
    _save(data)
    return srv


def stop_server(name: str) -> dict[str, Any]:
    data = _load()
    srv = data.get(name)
    if not srv:
        raise KeyError(f"Server '{name}' not found")

    pid = srv.get("pid")
    if pid and is_process_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGTERM)
        except ProcessLookupError:
            pass

    srv["pid"] = None
    srv["status"] = "stopped"
    _save(data)
    return srv


def restart_server(name: str) -> dict[str, Any]:
    stop_server(name)
    return start_server(name)


# ── Logs ──────────────────────────────────────────────────────────────────────

def get_logs(name: str, lines: int = 20) -> str:
    data = _load()
    srv = data.get(name)
    if not srv:
        raise KeyError(f"Server '{name}' not found")

    server_dir = Path(srv["path"])

    # Paper writes to logs/latest.log, not stdout
    for candidate in [server_dir / "logs" / "latest.log", server_dir / "server.log"]:
        if candidate.exists() and candidate.stat().st_size > 0:
            with open(candidate) as f:
                all_lines = f.readlines()
            return "".join(all_lines[-lines:])

    return "(no logs yet — server may still be starting)"


# ── Delete ────────────────────────────────────────────────────────────────────

def delete_server(name: str) -> None:
    data = _load()
    srv = data.get(name)
    if not srv:
        raise KeyError(f"Server '{name}' not found")

    pid = srv.get("pid")
    if pid and is_process_alive(pid):
        try:
            os.killpg(os.getpgid(pid), signal.SIGKILL)
        except ProcessLookupError:
            pass

    server_dir = Path(srv["path"])
    if server_dir.exists():
        shutil.rmtree(server_dir)

    del data[name]
    _save(data)
    logger.info("Deleted server '%s'", name)
