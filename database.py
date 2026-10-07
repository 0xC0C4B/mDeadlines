from __future__ import annotations

import os
import re
import sqlite3
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import List, Optional, Tuple

import config
from models import Deadline, Urgency

MOODLE_CLI_DB = Path.home() / ".local" / "share" / "moodle-deadlines" / "deadlines.db"


def normalize_text(s: str) -> str:
    """Normalize text by replacing punctuation, delimiters, and brackets with spaces."""
    return re.sub(r"[-_()\/.,:;]+", " ", s).lower().strip()


def matches_course_pattern(pattern: str, course: str) -> bool:
    """Check if course name matches an ignored course pattern."""
    p_clean = pattern.strip().lower()
    c_clean = course.strip().lower()
    if not p_clean or not c_clean:
        return False

    # Exact match
    if p_clean == c_clean:
        return True

    p_norm = normalize_text(pattern)
    c_norm = normalize_text(course)

    # For short abbreviations (<= 3 chars, e.g. "RA", "MUR"), require token boundary
    if len(p_clean) <= 3:
        p_tokens = p_norm.split()
        c_tokens = c_norm.split()
        return all(tok in c_tokens for tok in p_tokens)

    # Substring in normalized string (e.g. "programiranje i" in "um programiranje i un")
    # or direct substring (e.g. "PROGRAMIRANJE" in "um-PROGRAMIRANJE-I(UN)")
    return p_norm in c_norm or p_clean in c_clean


from contextlib import contextmanager


@contextmanager
def get_connection():
    conn = sqlite3.connect(config.DB_PATH)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def init_db() -> None:
    """Initialize SQLite tables for users, local deadlines cache, ignored courses, and alerts."""
    with get_connection() as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER PRIMARY KEY,
                username TEXT,
                first_name TEXT,
                moodle_url TEXT,
                is_subscribed INTEGER DEFAULT 1,
                thresholds TEXT DEFAULT '48,24,3,1',
                created_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS deadlines (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                uid TEXT NOT NULL UNIQUE,
                title TEXT NOT NULL,
                course TEXT NOT NULL,
                due_date TEXT NOT NULL,
                description TEXT,
                url TEXT,
                is_completed INTEGER DEFAULT 0,
                completed_at TEXT,
                last_synced_at TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS user_completions (
                chat_id INTEGER NOT NULL,
                deadline_uid TEXT NOT NULL,
                completed_at TEXT NOT NULL,
                PRIMARY KEY (chat_id, deadline_uid)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ignored_courses (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                pattern TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, pattern)
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS sent_alerts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER NOT NULL,
                deadline_uid TEXT NOT NULL,
                threshold_hours INTEGER NOT NULL,
                sent_at TEXT NOT NULL,
                UNIQUE(chat_id, deadline_uid, threshold_hours)
            )
        """)
        conn.commit()


def register_user(chat_id: int, username: Optional[str] = None, first_name: Optional[str] = None) -> None:
    """Register or update basic info for user."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT INTO users (chat_id, username, first_name, is_subscribed, created_at)
            VALUES (?, ?, ?, 1, ?)
            ON CONFLICT(chat_id) DO UPDATE SET
                username = excluded.username,
                first_name = excluded.first_name
        """, (chat_id, username, first_name, now_iso))
        conn.commit()


def get_user(chat_id: int) -> Optional[dict]:
    """Retrieve user record."""
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM users WHERE chat_id = ?", (chat_id,))
        row = cur.fetchone()
        return dict(row) if row else None


def set_user_subscription(chat_id: int, is_subscribed: bool) -> None:
    """Toggle automated alerts subscription."""
    with get_connection() as conn:
        conn.execute("""
            UPDATE users SET is_subscribed = ? WHERE chat_id = ?
        """, (1 if is_subscribed else 0, chat_id))
        conn.commit()


def set_user_moodle_url(chat_id: int, moodle_url: Optional[str]) -> None:
    """Update Moodle calendar URL for a user."""
    with get_connection() as conn:
        conn.execute("""
            UPDATE users SET moodle_url = ? WHERE chat_id = ?
        """, (moodle_url, chat_id))
        conn.commit()


def set_user_thresholds(chat_id: int, thresholds_str: str) -> None:
    """Update custom alert thresholds (e.g. '48,24,3,1')."""
    with get_connection() as conn:
        conn.execute("""
            UPDATE users SET thresholds = ? WHERE chat_id = ?
        """, (thresholds_str, chat_id))
        conn.commit()


def get_user_thresholds(chat_id: int) -> List[int]:
    """Get alert threshold hours for user."""
    user = get_user(chat_id)
    raw = user.get("thresholds") if user else None
    if not raw:
        return config.ALERT_HOURS
    try:
        hours = sorted([int(h.strip()) for h in raw.split(",") if h.strip().isdigit()], reverse=True)
        return hours if hours else config.ALERT_HOURS
    except Exception:
        return config.ALERT_HOURS


def get_subscribed_users() -> List[dict]:
    """Retrieve all users who have active notifications."""
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM users WHERE is_subscribed = 1")
        return [dict(row) for row in cur.fetchall()]


# ---------------- Ignored Courses Management ---------------- #

def add_ignored_course(chat_id: Optional[int], pattern: str) -> Tuple[bool, int, List[str]]:
    """Add a course pattern to ignored list. Returns (is_new, count_of_affected_deadlines, matching_courses)."""
    clean_pat = pattern.strip()
    if not clean_pat:
        return False, 0, []

    now_iso = datetime.now(timezone.utc).isoformat()
    is_new = False
    with get_connection() as conn:
        try:
            conn.execute("""
                INSERT INTO ignored_courses (chat_id, pattern, created_at)
                VALUES (?, ?, ?)
            """, (chat_id, clean_pat, now_iso))
            conn.commit()
            is_new = True
        except sqlite3.IntegrityError:
            is_new = False

    # Find matching courses and affected deadlines
    all_deadlines = get_cached_deadlines(chat_id=chat_id, include_ignored=True)
    matching_courses = set()
    affected_count = 0

    for d in all_deadlines:
        if matches_course_pattern(clean_pat, d.course):
            matching_courses.add(d.course)
            affected_count += 1

    return is_new, affected_count, sorted(list(matching_courses))


def remove_ignored_course(chat_id: Optional[int], pattern: str) -> Tuple[bool, int]:
    """Remove a course pattern from ignored list. Returns (was_removed, count_of_restored_deadlines)."""
    clean_pat = pattern.strip()
    if not clean_pat:
        return False, 0

    was_removed = False
    with get_connection() as conn:
        if chat_id is not None:
            cur = conn.execute("""
                DELETE FROM ignored_courses
                WHERE chat_id = ? AND LOWER(pattern) = LOWER(?)
            """, (chat_id, clean_pat))
        else:
            cur = conn.execute("""
                DELETE FROM ignored_courses
                WHERE LOWER(pattern) = LOWER(?)
            """, (clean_pat,))
        conn.commit()
        was_removed = cur.rowcount > 0

    # Count restored deadlines
    all_deadlines = get_cached_deadlines(chat_id=chat_id, include_ignored=True)
    restored_count = sum(1 for d in all_deadlines if matches_course_pattern(clean_pat, d.course))

    return was_removed, restored_count


def get_ignored_courses(chat_id: Optional[int] = None) -> List[str]:
    """Return all configured ignored course patterns for this chat or globally."""
    patterns = set()
    with get_connection() as conn:
        if chat_id is not None:
            cur = conn.execute("""
                SELECT pattern FROM ignored_courses
                WHERE chat_id = ? OR chat_id IS NULL
                ORDER BY pattern ASC
            """, (chat_id,))
        else:
            cur = conn.execute("SELECT pattern FROM ignored_courses ORDER BY pattern ASC")
        for row in cur.fetchall():
            patterns.add(row["pattern"])

    # Also load from moodle-deadlines CLI database if present
    if MOODLE_CLI_DB.exists():
        try:
            cli_conn = sqlite3.connect(MOODLE_CLI_DB)
            cur = cli_conn.execute("SELECT pattern FROM ignored_courses")
            for row in cur.fetchall():
                patterns.add(row[0])
            cli_conn.close()
        except Exception:
            pass

    return sorted(list(patterns))


def get_all_courses(chat_id: Optional[int] = None) -> List[dict]:
    """Return all unique courses with deadline counts and whether they are ignored."""
    with get_connection() as conn:
        cur = conn.execute("""
            SELECT course, COUNT(*) AS count
            FROM deadlines
            GROUP BY course
            ORDER BY course ASC
        """)
        rows = cur.fetchall()

    ignored_patterns = get_ignored_courses(chat_id)
    courses = []
    for r in rows:
        c_name = r["course"]
        is_ignored = any(matches_course_pattern(pat, c_name) for pat in ignored_patterns)
        courses.append({
            "name": c_name,
            "count": r["count"],
            "is_ignored": is_ignored
        })
    return courses


# ---------------- Deadlines Storage & Queries ---------------- #

def save_or_update_deadlines(items: List[Deadline]) -> int:
    """Insert or update parsed deadlines into local cache."""
    now_iso = datetime.now(timezone.utc).isoformat()
    count = 0
    with get_connection() as conn:
        for item in items:
            due_iso = item.due_date.isoformat()
            conn.execute("""
                INSERT INTO deadlines (uid, title, course, due_date, description, url, last_synced_at)
                VALUES (?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(uid) DO UPDATE SET
                    title = excluded.title,
                    course = excluded.course,
                    due_date = excluded.due_date,
                    description = excluded.description,
                    url = excluded.url,
                    last_synced_at = excluded.last_synced_at
            """, (item.uid, item.title, item.course, due_iso, item.description, item.url, now_iso))
            count += 1
        conn.commit()
    return count


def load_deadlines_from_moodle_cli_db() -> List[Deadline]:
    """If moodle-deadlines CLI database exists, sync its entries."""
    if not MOODLE_CLI_DB.exists():
        return []

    results: List[Deadline] = []
    try:
        conn = sqlite3.connect(MOODLE_CLI_DB)
        conn.row_factory = sqlite3.Row
        cur = conn.execute("""
            SELECT id, uid, title, course, due_date, description, url, is_completed
            FROM deadlines
        """)
        for row in cur.fetchall():
            due_dt = datetime.fromisoformat(row["due_date"])
            results.append(Deadline(
                id=row["id"],
                uid=row["uid"],
                title=row["title"],
                course=row["course"],
                due_date=due_dt,
                description=row["description"] or "",
                url=row["url"],
                is_completed=bool(row["is_completed"])
            ))
        conn.close()
    except Exception:
        pass
    return results


def mark_deadline_completed(chat_id: int, identifier: str | int, completed: bool = True) -> Optional[Deadline]:
    """Mark deadline completed by ID or UID."""
    now_iso = datetime.now(timezone.utc).isoformat()
    found_deadline: Optional[Deadline] = None

    with get_connection() as conn:
        if isinstance(identifier, int) or (isinstance(identifier, str) and identifier.isdigit()):
            cur = conn.execute("SELECT * FROM deadlines WHERE id = ?", (int(identifier),))
        else:
            cur = conn.execute("SELECT * FROM deadlines WHERE uid = ?", (str(identifier),))
        row = cur.fetchone()

        if row:
            uid = row["uid"]
            if completed:
                conn.execute("""
                    INSERT OR REPLACE INTO user_completions (chat_id, deadline_uid, completed_at)
                    VALUES (?, ?, ?)
                """, (chat_id, uid, now_iso))
                conn.execute("""
                    UPDATE deadlines SET is_completed = 1, completed_at = ? WHERE uid = ?
                """, (now_iso, uid))
            else:
                conn.execute("""
                    DELETE FROM user_completions WHERE chat_id = ? AND deadline_uid = ?
                """, (chat_id, uid))
                conn.execute("""
                    UPDATE deadlines SET is_completed = 0, completed_at = NULL WHERE uid = ?
                """, (uid,))
            conn.commit()

            due_dt = datetime.fromisoformat(row["due_date"])
            found_deadline = Deadline(
                id=row["id"],
                uid=row["uid"],
                title=row["title"],
                course=row["course"],
                due_date=due_dt,
                description=row["description"] or "",
                url=row["url"],
                is_completed=completed
            )
    return found_deadline


def get_cached_deadlines(
    chat_id: Optional[int] = None,
    filter_type: str = "all",
    course_filter: Optional[str] = None,
    include_completed: bool = False,
    include_ignored: bool = False
) -> List[Deadline]:
    """Retrieve deadlines from database with filters, completion state, and ignored courses filtering."""
    with get_connection() as conn:
        cur = conn.execute("SELECT * FROM deadlines ORDER BY due_date ASC")
        rows = cur.fetchall()

    user_completed_uids = set()
    if chat_id:
        with get_connection() as conn:
            cur = conn.execute("SELECT deadline_uid FROM user_completions WHERE chat_id = ?", (chat_id,))
            user_completed_uids = {r["deadline_uid"] for r in cur.fetchall()}

    ignored_patterns = [] if include_ignored else get_ignored_courses(chat_id)

    items: List[Deadline] = []

    for r in rows:
        course_name = r["course"]

        # Filter out ignored courses
        if not include_ignored and ignored_patterns:
            if any(matches_course_pattern(pat, course_name) for pat in ignored_patterns):
                continue

        due_dt = datetime.fromisoformat(r["due_date"])
        is_done = bool(r["is_completed"]) or (r["uid"] in user_completed_uids)
        if not include_completed and is_done:
            continue

        item = Deadline(
            id=r["id"],
            uid=r["uid"],
            title=r["title"],
            course=course_name,
            due_date=due_dt,
            description=r["description"] or "",
            url=r["url"],
            is_completed=is_done
        )

        # Course filter (e.g. from /course <name>)
        if course_filter and not matches_course_pattern(course_filter, item.course):
            continue

        if filter_type in ("soon", "upcoming"):
            if not (0 <= item.hours_remaining <= 168):
                continue
        elif filter_type == "today":
            if not (0 <= item.hours_remaining <= 24):
                continue
        elif filter_type == "week":
            if not (0 <= item.hours_remaining <= 168):
                continue
        elif filter_type == "urgent":
            if not (0 <= item.hours_remaining <= 72):
                continue

        items.append(item)

    return items


def has_alert_been_sent(chat_id: int, deadline_uid: str, threshold_hours: int) -> bool:
    """Check if an alert for a specific deadline and threshold has already been sent."""
    with get_connection() as conn:
        cur = conn.execute("""
            SELECT 1 FROM sent_alerts
            WHERE chat_id = ? AND deadline_uid = ? AND threshold_hours = ?
        """, (chat_id, deadline_uid, threshold_hours))
        return cur.fetchone() is not None


def mark_alert_as_sent(chat_id: int, deadline_uid: str, threshold_hours: int) -> None:
    """Record that an alert has been delivered to prevent duplicate alerts."""
    now_iso = datetime.now(timezone.utc).isoformat()
    with get_connection() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO sent_alerts (chat_id, deadline_uid, threshold_hours, sent_at)
            VALUES (?, ?, ?, ?)
        """, (chat_id, deadline_uid, threshold_hours, now_iso))
        conn.commit()
