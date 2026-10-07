from __future__ import annotations

import hashlib
import os
import re
from datetime import datetime, timezone, timedelta
from typing import List, Optional
from urllib.parse import urlparse
import requests

try:
    from curl_cffi import requests as cffi_requests
    HAS_CURL_CFFI = True
except ImportError:
    cffi_requests = None
    HAS_CURL_CFFI = False

from models import Deadline, Urgency

USER_AGENT = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36"
)


def parse_ical_datetime(dt_str: str) -> datetime:
    """Parse iCal timestamp strings like 20261015T220000Z or 20261015T220000."""
    dt_clean = dt_str.strip().rstrip("Z")
    if "T" in dt_clean:
        if len(dt_clean) == 15:  # YYYYMMDDTHHMMSS
            dt = datetime.strptime(dt_clean, "%Y%m%dT%H%M%S")
        elif len(dt_clean) == 13:  # YYYYMMDDTHHMM
            dt = datetime.strptime(dt_clean, "%Y%m%dT%H%M")
        else:
            dt = datetime.fromisoformat(dt_clean)
    else:
        # All-day event: YYYYMMDD
        dt = datetime.strptime(dt_clean, "%Y%m%d")
        dt = dt.replace(hour=23, minute=59, second=59)

    # Ensure UTC timezone awareness
    if dt_str.endswith("Z") or not dt.tzinfo:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def parse_ical_content(content: str) -> List[Deadline]:
    """Parse raw iCalendar (.ics) string into list of Deadline items."""
    # Unfold lines (lines starting with space or tab are continuations)
    unfolded = re.sub(r"\r?\n[ \t]", "", content)
    lines = unfolded.splitlines()

    events: List[Deadline] = []
    in_event = False
    current_props: dict[str, str] = {}

    for line in lines:
        line = line.strip()
        if not line:
            continue
        if line == "BEGIN:VEVENT":
            in_event = True
            current_props = {}
            continue
        elif line == "END:VEVENT":
            in_event = False
            summary = current_props.get("SUMMARY", "Untitled Task")
            dt_raw = current_props.get("DTEND") or current_props.get("DTSTART")
            if not dt_raw:
                continue

            try:
                due_date = parse_ical_datetime(dt_raw)
            except Exception:
                continue

            course = current_props.get("CATEGORIES", "")
            description = current_props.get("DESCRIPTION", "")
            url = current_props.get("URL", "")

            # If no URL in URL property, check description for links
            if not url and description:
                url_match = re.search(r"https?://[^\s\"<>]+", description)
                if url_match:
                    url = url_match.group(0).rstrip(".,;")

            # Generate unique ID if UID missing
            uid = current_props.get("UID")
            if not uid:
                uid_hash = hashlib.sha256(f"{summary}_{dt_raw}".encode()).hexdigest()[:16]
                uid = f"deadline_{uid_hash}"

            events.append(Deadline(
                uid=uid,
                title=summary,
                course=course,
                due_date=due_date,
                description=description,
                url=url
            ))
            continue

        if in_event and ":" in line:
            prop_key_full, prop_val = line.split(":", 1)
            prop_key = prop_key_full.split(";")[0].upper()
            prop_val = (
                prop_val.replace("\\n", "\n")
                .replace("\\N", "\n")
                .replace("\\,", ",")
                .replace("\\;", ";")
                .replace("\\\\", "\\")
            )
            current_props[prop_key] = prop_val

    # Sort ascending by due date
    events.sort(key=lambda d: d.due_date)
    return events


def fetch_deadlines(url: str, timeout: int = 15) -> List[Deadline]:
    """Fetch and parse deadlines from a Moodle calendar export URL or local file."""
    if not url:
        return []

    url = url.strip()
    if url.startswith("webcal://"):
        url = "https://" + url[9:]

    # Support local file path
    if os.path.exists(url) or url.startswith("file://") or (url.endswith(".ics") and not url.startswith("http")):
        clean_path = url.replace("file://", "")
        with open(clean_path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()
        return parse_ical_content(content)

    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/calendar, text/plain, text/html, */*",
        "Accept-Language": "en-US,en;q=0.9,sl;q=0.8",
    }

    # 1. Try curl_cffi for browser impersonation (bypasses Cloudflare / university WAFs)
    if HAS_CURL_CFFI and cffi_requests is not None:
        try:
            resp = cffi_requests.get(
                url,
                headers=headers,
                impersonate="chrome",
                timeout=timeout
            )
            if resp.status_code == 200:
                return parse_ical_content(resp.text)
        except Exception:
            pass

    # 2. Fallback to standard requests
    resp = requests.get(url, headers=headers, timeout=timeout)
    resp.raise_for_status()
    return parse_ical_content(resp.text)


def get_demo_deadlines() -> List[Deadline]:
    """Generate sample deadlines for immediate testing and demonstration."""
    now = datetime.now(timezone.utc)
    return [
        Deadline(
            id=1,
            uid="demo-assignment-1",
            title="Assignment 1: Algorithms Analysis",
            course="CS204: Data Structures & Algorithms",
            due_date=now + timedelta(hours=14),
            description="Submit PDF report and source code repository link.",
            url="https://moodle.example.edu/mod/assign/view.php?id=101"
        ),
        Deadline(
            id=2,
            uid="demo-quiz-2",
            title="Weekly Quiz 4: Concurrency & Threads",
            course="CS301: Operating Systems",
            due_date=now + timedelta(days=2, hours=6),
            description="20 multiple-choice questions, 45 minutes limit.",
            url="https://moodle.example.edu/mod/quiz/view.php?id=202"
        ),
        Deadline(
            id=3,
            uid="demo-project-3",
            title="Final Project Milestone 1 - Proposal & Architecture",
            course="CS450: Software Engineering",
            due_date=now + timedelta(days=5, hours=18),
            description="Submit architecture design document and user stories.",
            url="https://moodle.example.edu/mod/assign/view.php?id=303"
        )
    ]
