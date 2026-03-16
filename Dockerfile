FROM python:3.11-slim

# ── System deps + Java 21 ─────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jre-headless \
        curl \
        jq \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Pre-download Paper jar + Mojang jar (no server startup) ───────────────────
# 1. Fetch latest Paper build info
# 2. Download paper.jar
# 3. Find which MC version it targets, download mojang server jar directly
# Paper expects its cache at: <server_dir>/cache/mojang_<version>.jar
RUN set -e && \
    PAPER_API="https://api.papermc.io/v2/projects/paper" && \
    LATEST_VER=$(curl -fsSL "$PAPER_API" | jq -r '.versions[-1]') && \
    LATEST_BUILD=$(curl -fsSL "$PAPER_API/versions/$LATEST_VER/builds" | jq -r '.builds[-1].build') && \
    JAR_NAME="paper-${LATEST_VER}-${LATEST_BUILD}.jar" && \
    mkdir -p /opt/paper-cache/cache && \
    echo "Downloading Paper ${LATEST_VER} build ${LATEST_BUILD}..." && \
    curl -fsSL "$PAPER_API/versions/$LATEST_VER/builds/$LATEST_BUILD/downloads/$JAR_NAME" \
         -o /opt/paper-cache/paper.jar && \
    echo "Downloading Mojang server jar for ${LATEST_VER}..." && \
    MANIFEST_URL="https://launchermeta.mojang.com/mc/game/version_manifest_v2.json" && \
    VERSION_URL=$(curl -fsSL "$MANIFEST_URL" | jq -r --arg v "$LATEST_VER" '.versions[] | select(.id == $v) | .url') && \
    MOJANG_URL=$(curl -fsSL "$VERSION_URL" | jq -r '.downloads.server.url') && \
    curl -fsSL "$MOJANG_URL" -o "/opt/paper-cache/cache/mojang_${LATEST_VER}.jar" && \
    echo "Done. Cache contents:" && \
    ls -lh /opt/paper-cache/ && \
    ls -lh /opt/paper-cache/cache/

# ── App setup ─────────────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p servers

# ── Run ───────────────────────────────────────────────────────────────────────
CMD ["python", "bot.py"]
