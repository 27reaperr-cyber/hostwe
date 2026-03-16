"""
server_manager.py
-----------------
Handles creation, lifecycle and deletion of Minecraft servers.
Servers are tracked in servers.json.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import signal
import subprocess
import urllib.request
from pathlib import Path
from typing import Any

import config
from utils import get_vps_ip, is_process_alive, logger

SERVERS_DIR = Path("servers")
SERVERS_JSON = Path("servers.json")
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


# ── Download helpers ──────────────────────────────────────────────────────────

def _latest_paper_url() -> str:
    api = "https://api.papermc.io/v2/projects/paper"
    with urllib.request.urlopen(api, timeout=15) as r:
        versions: list[str] = json.loads(r.read())["versions"]
    latest = versions[-1]
    builds_api = f"{api}/versions/{latest}/builds"
    with urllib.request.urlopen(builds_api, timeout=15) as r:
        builds = json.loads(r.read())["builds"]
    build_num = builds[-1]["build"]
    jar_name = f"paper-{latest}-{build_num}.jar"
    return (
        f"https://api.papermc.io/v2/projects/paper/versions/{latest}"
        f"/builds/{build_num}/downloads/{jar_name}"
    )


def _latest_vanilla_url() -> str:
    manifest = "https://launchermeta.mojang.com/mc/game/version_manifest_v2.json"
    with urllib.request.urlopen(manifest, timeout=15) as r:
        data = json.loads(r.read())
    latest_id = data["latest"]["release"]
    for v in data["versions"]:
        if v["id"] == latest_id:
            with urllib.request.urlopen(v["url"], timeout=15) as r2:
                meta = json.loads(r2.read())
            return meta["downloads"]["server"]["url"]
    raise RuntimeError("Could not find latest vanilla version")


def _download(url: str, dest: Path) -> None:
    logger.info("Downloading %s -> %s", url, dest)
    urllib.request.urlretrieve(url, dest)


# ── BuildTools (Spigot) ───────────────────────────────────────────────────────

def _build_spigot(server_dir: Path) -> Path:
    bt_url = "https://hub.spigotmc.org/jenkins/job/BuildTools/lastSuccessfulBuild/artifact/target/BuildTools.jar"
    bt_jar = server_dir / "BuildTools.jar"
    _download(bt_url, bt_jar)
    logger.info("Running BuildTools for Spigot (this may take a while)…")
    result = subprocess.run(
        ["java", "-jar", str(bt_jar), "--rev", "latest", "--nogui"],
        cwd=str(server_dir),
        capture_output=True,
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise RuntimeError(f"BuildTools failed:\n{result.stderr[-1000:]}")
    # find produced spigot jar
    for f in server_dir.glob("spigot-*.jar"):
        return f
    raise RuntimeError("BuildTools did not produce a spigot jar")


# ── Create ────────────────────────────────────────────────────────────────────

def create_server(
    name: str,
    server_type: str,
    progress_callback=None,
) -> dict[str, Any]:
    """
    Create and start a new Minecraft server.

    progress_callback(msg: str) is called with status updates if provided.
    Returns the server dict on success.
    """
    data = _load()

    if name in data:
        raise ValueError(f"Server '{name}' already exists")
    if len(data) >= config.MAX_SERVERS:
        raise ValueError(f"Maximum number of servers ({config.MAX_SERVERS}) reached")

    server_dir = SERVERS_DIR / name
    server_dir.mkdir(parents=True, exist_ok=True)

    def _progress(msg: str) -> None:
        logger.info(msg)
        if progress_callback:
            progress_callback(msg)

    port = _next_port()
    jar_path: Path

    # ── Download / build ──────────────────────────────────────────────────────
    _progress(f"Preparing {server_type} server…")

    if server_type == "paper":
        _progress("Fetching latest PaperMC version info…")
        url = _latest_paper_url()
        jar_path = server_dir / "server.jar"
        _progress("Downloading Paper jar…")
        _download(url, jar_path)

    elif server_type == "vanilla":
        _progress("Fetching latest Vanilla version info…")
        url = _latest_vanilla_url()
        jar_path = server_dir / "server.jar"
        _progress("Downloading Vanilla jar…")
        _download(url, jar_path)

    elif server_type == "spigot":
        _progress("Building Spigot via BuildTools (may take ~5 min)…")
        jar_path = _build_spigot(server_dir)
        # normalise name for startup
        canonical = server_dir / "server.jar"
        shutil.copy(jar_path, canonical)
        jar_path = canonical

    else:
        raise ValueError(f"Unknown server type: {server_type}")

    # ── Config files ──────────────────────────────────────────────────────────
    (server_dir / "eula.txt").write_text("eula=true\n")

    props = (
        f"server-port={port}\n"
        "gamemode=survival\n"
        "difficulty=normal\n"
        "max-players=20\n"
        "online-mode=true\n"
        "motd=A Minecraft Server\n"
    )
    (server_dir / "server.properties").write_text(props)

    # ── Start process ─────────────────────────────────────────────────────────
    _progress("Starting server…")
    log_file = open(server_dir / "server.log", "a")
    proc = subprocess.Popen(
        [
            "java",
            f"-Xms{config.SERVER_RAM_MIN}",
            f"-Xmx{config.SERVER_RAM_MAX}",
            "-jar",
            str(jar_path),
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
        "type": server_type,
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
            str(jar_path),
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

    log_path = Path(srv["path"]) / "server.log"
    if not log_path.exists():
        return "(no log file yet)"

    with open(log_path) as f:
        all_lines = f.readlines()
    return "".join(all_lines[-lines:]) or "(log is empty)"


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
