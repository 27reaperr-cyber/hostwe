FROM python:3.11-slim

# ── System deps + Java 17 ─────────────────────────────────────────────────────
RUN apt-get update && apt-get install -y --no-install-recommends \
        openjdk-17-jre-headless \
        wget \
        curl \
    && apt-get clean \
    && rm -rf /var/lib/apt/lists/*

# Confirm Java is available
RUN java -version

# ── App setup ─────────────────────────────────────────────────────────────────
WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Ensure data dirs exist
RUN mkdir -p servers

# ── Run ───────────────────────────────────────────────────────────────────────
CMD ["python", "bot.py"]
