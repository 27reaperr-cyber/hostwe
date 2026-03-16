FROM python:3.11-slim

# ── System deps + Java 21 ─────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-21-jre-headless \
        wget \
        curl \
        jq \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# ── Pre-download latest Paper jar + warm up Mojang cache ──────────────────────
# Runs at BUILD time — new servers copy from cache, no downloads at runtime.
RUN set -e && \
    PAPER_API="https://api.papermc.io/v2/projects/paper" && \
    LATEST_VER=$(curl -fsSL "$PAPER_API" | jq -r '.versions[-1]') && \
    LATEST_BUILD=$(curl -fsSL "$PAPER_API/versions/$LATEST_VER/builds" | jq -r '.builds[-1].build') && \
    JAR_NAME="paper-${LATEST_VER}-${LATEST_BUILD}.jar" && \
    mkdir -p /opt/paper-cache && \
    curl -fsSL "$PAPER_API/versions/$LATEST_VER/builds/$LATEST_BUILD/downloads/$JAR_NAME" \
         -o /opt/paper-cache/paper.jar && \
    echo "Downloaded Paper $LATEST_VER build $LATEST_BUILD" && \
    # Warm up: let Paper pre-download the Mojang vanilla jar into its cache
    mkdir -p /opt/paper-warmup && \
    cp /opt/paper-cache/paper.jar /opt/paper-warmup/server.jar && \
    echo "eula=true" > /opt/paper-warmup/eula.txt && \
    cd /opt/paper-warmup && \
    timeout 180 java -Xms512M -Xmx512M -jar server.jar nogui || true && \
    # Move Paper's internal cache (contains mojang jar) to a known location
    cp -r /opt/paper-warmup/cache /opt/paper-cache/cache 2>/dev/null || true && \
    echo "Paper warm-up done"

# ── App setup ─────────────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p servers

# ── Run ───────────────────────────────────────────────────────────────────────
CMD ["python", "bot.py"]
