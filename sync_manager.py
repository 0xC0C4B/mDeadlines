from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import List, Optional

import config
import database
from deadlines import fetch_deadlines
from models import Deadline

logger = logging.getLogger("telegram_deadline_bot.sync")


@dataclass
class SyncResult:
    success: bool
    items_count: int
    source: str
    error: Optional[str] = None


def sync_deadlines_for_user(chat_id: Optional[int] = None) -> SyncResult:
    """Synchronize deadlines from available sources into the local database."""
    # Ensure any demo dummy tasks are always purged
    database.purge_demo_deadlines()

    url = None
    if chat_id:
        user = database.get_user(chat_id)
        if user and user.get("moodle_url"):
            url = user["moodle_url"]

    if not url:
        url = config.DEFAULT_MOODLE_ICAL_URL

    # Try fetching from Moodle URL
    if url:
        try:
            items = fetch_deadlines(url)
            if items:
                count = database.save_or_update_deadlines(items, replace_all=True)
                return SyncResult(success=True, items_count=count, source="moodle_url")
        except Exception as e:
            logger.warning(f"Failed to fetch from {url}: {e}")

    # Fallback to local moodle-deadlines CLI database if present
    cli_items = database.load_deadlines_from_moodle_cli_db()
    if cli_items:
        count = database.save_or_update_deadlines(cli_items, replace_all=True)
        return SyncResult(success=True, items_count=count, source="moodle_cli_db")

    # If database already has cached entries, count them
    cached = database.get_cached_deadlines(chat_id=chat_id)
    if cached:
        return SyncResult(success=True, items_count=len(cached), source="local_cache")

    # If nothing is configured or available, do not insert dummy items
    return SyncResult(success=True, items_count=0, source="empty")
