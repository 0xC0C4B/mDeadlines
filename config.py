from __future__ import annotations

import os
import sqlite3
from pathlib import Path
from typing import List

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent

# Telegram Token
BOT_TOKEN: str = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()

# Check for Moodle CLI database feed as automatic fallback
MOODLE_CLI_DB = Path.home() / ".local" / "share" / "moodle-deadlines" / "deadlines.db"


def detect_moodle_feed_url() -> str:
    """Detect configured Moodle feed URL from environment or local moodle-deadlines database."""
    env_url = os.getenv("DEFAULT_MOODLE_ICAL_URL", "").strip()
    if env_url:
        return env_url
    if MOODLE_CLI_DB.exists():
        try:
            conn = sqlite3.connect(MOODLE_CLI_DB)
            cur = conn.execute("SELECT url FROM feeds WHERE status = 'active' ORDER BY id ASC LIMIT 1")
            row = cur.fetchone()
            conn.close()
            if row and row[0]:
                return row[0].strip()
        except Exception:
            pass
    return ""


DEFAULT_MOODLE_ICAL_URL: str = detect_moodle_feed_url()

# Background check interval in seconds (default: 30 minutes)
CHECK_INTERVAL_SECONDS: int = int(os.getenv("CHECK_INTERVAL_SECONDS", "1800"))

# Alert thresholds (hours before deadline)
_alert_hours_raw = os.getenv("ALERT_HOURS_BEFORE", "48,24,3,1")
ALERT_HOURS: List[int] = sorted(
    [int(h.strip()) for h in _alert_hours_raw.split(",") if h.strip().isdigit()],
    reverse=True
)
if not ALERT_HOURS:
    ALERT_HOURS = [48, 24, 3, 1]

# Timezone
TIMEZONE_NAME: str = os.getenv("TIMEZONE", "Europe/Ljubljana").strip()

# Database path
DB_PATH: Path = BASE_DIR / "bot_data.db"
