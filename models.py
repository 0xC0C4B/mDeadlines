from __future__ import annotations

import html
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Optional


class Urgency(str, Enum):
    """Urgency level of a deadline."""
    COMPLETED = "completed"
    OVERDUE = "overdue"
    URGENT_24H = "urgent_24h"
    THIS_WEEK = "this_week"
    UPCOMING = "upcoming"

    @property
    def label(self) -> str:
        match self:
            case Urgency.COMPLETED:
                return "COMPLETED"
            case Urgency.OVERDUE:
                return "OVERDUE"
            case Urgency.URGENT_24H:
                return "< 24H"
            case Urgency.THIS_WEEK:
                return "THIS WEEK"
            case Urgency.UPCOMING:
                return "UPCOMING"

    @property
    def emoji(self) -> str:
        match self:
            case Urgency.COMPLETED:
                return "✅"
            case Urgency.OVERDUE:
                return "🚨"
            case Urgency.URGENT_24H:
                return "🔴"
            case Urgency.THIS_WEEK:
                return "🟡"
            case Urgency.UPCOMING:
                return "🟢"


@dataclass
class Deadline:
    """Represents a course deadline / assignment from Moodle."""
    uid: str
    title: str
    course: str
    due_date: datetime
    description: str = ""
    url: Optional[str] = None
    id: Optional[int] = None
    is_completed: bool = False
    event_type: str = "Assignment"

    @property
    def hours_remaining(self) -> float:
        now = datetime.now(timezone.utc)
        due_utc = self.due_date if self.due_date.tzinfo else self.due_date.replace(tzinfo=timezone.utc)
        return (due_utc - now).total_seconds() / 3600.0

    @property
    def is_past(self) -> bool:
        return self.hours_remaining < 0

    @property
    def urgency(self) -> Urgency:
        if self.is_completed:
            return Urgency.COMPLETED
        hours = self.hours_remaining
        if hours < 0:
            return Urgency.OVERDUE
        if hours <= 24:
            return Urgency.URGENT_24H
        if hours <= 168:  # 7 days
            return Urgency.THIS_WEEK
        return Urgency.UPCOMING

    @property
    def urgency_emoji(self) -> str:
        return self.urgency.emoji

    def format_countdown(self) -> str:
        if self.is_completed:
            return "Completed"
        hours = self.hours_remaining
        if hours < 0:
            abs_hours = abs(hours)
            days = int(abs_hours // 24)
            rem_h = int(abs_hours % 24)
            if days > 0:
                return f"Overdue by {days}d {rem_h}h"
            return f"Overdue by {rem_h}h"
        
        days = int(hours // 24)
        rem_hours = int(hours % 24)
        mins = int((hours * 60) % 60)
        parts = []
        if days > 0:
            parts.append(f"{days}d")
        if rem_hours > 0 or days > 0:
            parts.append(f"{rem_hours}h")
        parts.append(f"{mins}m")
        return " ".join(parts) + " left"

    @property
    def clean_title(self) -> str:
        """Strip redundant Moodle prefixes (like 'Rok za ') or suffixes (like ' is due')."""
        cleaned = self.title.strip()
        cleaned = re.sub(r"^(Rok za\s+|Oddaja\s+)", "", cleaned, flags=re.IGNORECASE)
        cleaned = re.sub(r"\s+(is due|closes|opens|is created)$", "", cleaned, flags=re.IGNORECASE)
        return cleaned.strip()

    def format_html(self, id_prefix: bool = True) -> str:
        """Format as a rich Telegram HTML message block."""
        emoji = self.urgency_emoji
        due_str = self.due_date.strftime("%b %d, %Y at %H:%M UTC")
        countdown = self.format_countdown()
        safe_title = html.escape(self.clean_title)
        safe_course = html.escape(self.course)

        id_tag = f"<code>#{self.id}</code> " if (id_prefix and self.id is not None) else ""
        done_strikethrough = "<s>" if self.is_completed else ""
        done_strikethrough_end = "</s>" if self.is_completed else ""

        text = f"{emoji} {id_tag}<b>{done_strikethrough}{safe_title}{done_strikethrough_end}</b>\n"
        if self.course:
            text += f"📚 <i>{safe_course}</i>\n"
        text += f"⏰ <b>Due:</b> {due_str} (<b>{countdown}</b>)\n"
        if self.url:
            text += f"🔗 <a href=\"{self.url}\">Open in Moodle</a>\n"
        return text

    def format_summary_line(self) -> str:
        """Single line summary for compact views."""
        emoji = self.urgency_emoji
        safe_title = html.escape(self.clean_title)
        id_str = f"#{self.id} " if self.id else ""
        countdown = self.format_countdown()
        return f"{emoji} {id_str}<b>{safe_title}</b> — <i>{countdown}</i>"
