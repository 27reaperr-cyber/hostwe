import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN: str = os.getenv("BOT_TOKEN", "")
MAX_SERVERS: int = int(os.getenv("MAX_SERVERS", "5"))
SERVER_RAM_MIN: str = os.getenv("SERVER_RAM_MIN", "1G")
SERVER_RAM_MAX: str = os.getenv("SERVER_RAM_MAX", "2G")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN is not set in .env")
