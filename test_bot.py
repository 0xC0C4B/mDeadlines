from __future__ import annotations

import os
import tempfile
import unittest
from datetime import datetime, timezone, timedelta
from pathlib import Path

import config
import database
import deadlines
from models import Deadline, Urgency
import sync_manager

SAMPLE_ICS = """BEGIN:VCALENDAR
VERSION:2.0
PRODID:-//Moodle//NONSGML v1.0//EN
BEGIN:VEVENT
UID:1001@moodle.test
SUMMARY:Rok za CS101 Lab 3 Assignment is due
DESCRIPTION:Please submit your laboratory report and python script to Moodle.
CATEGORIES:Computer Science 101
DTSTART:20261012T180000Z
DTEND:20261012T200000Z
URL:https://moodle.test/mod/assign/view.php?id=55
END:VEVENT
BEGIN:VEVENT
UID:1002@moodle.test
SUMMARY:Oddaja Naloga 02 closes
DESCRIPTION:Review homework.
CATEGORIES:MATH 201
DTSTART:20261014T100000Z
DTEND:20261014T110000Z
END:VEVENT
END:VCALENDAR
"""

class TestModels(unittest.TestCase):
    def test_clean_title(self):
        now = datetime.now(timezone.utc)
        d1 = Deadline("u1", "Rok za RV01: computer is due", "CS", now)
        d2 = Deadline("u2", "Oddaja Naloga 01 closes", "Math", now)
        d3 = Deadline("u3", "Quiz 4", "Physics", now)

        self.assertEqual(d1.clean_title, "RV01: computer")
        self.assertEqual(d2.clean_title, "Naloga 01")
        self.assertEqual(d3.clean_title, "Quiz 4")

    def test_urgency_and_countdown(self):
        now = datetime.now(timezone.utc)
        d_urgent = Deadline("u1", "Lab", "CS", now + timedelta(hours=10))
        d_week = Deadline("u2", "Homework", "CS", now + timedelta(days=3))
        d_future = Deadline("u3", "Project", "CS", now + timedelta(days=20))

        self.assertEqual(d_urgent.urgency, Urgency.URGENT_24H)
        self.assertEqual(d_urgent.urgency_emoji, "🔴")

        self.assertEqual(d_week.urgency, Urgency.THIS_WEEK)
        self.assertEqual(d_week.urgency_emoji, "🟡")

        self.assertEqual(d_future.urgency, Urgency.UPCOMING)
        self.assertEqual(d_future.urgency_emoji, "🟢")

    def test_format_html(self):
        now = datetime.now(timezone.utc)
        d = Deadline("u1", "Assignment 1 is due", "CS101", now + timedelta(hours=5), url="https://moodle.test/a1")
        d.id = 42
        html_text = d.format_html()
        self.assertIn("Assignment 1", html_text)
        self.assertIn("#42", html_text)
        self.assertIn("CS101", html_text)
        self.assertIn("https://moodle.test/a1", html_text)


class TestDeadlinesParser(unittest.TestCase):
    def test_parse_ical_content(self):
        items = deadlines.parse_ical_content(SAMPLE_ICS)
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].clean_title, "CS101 Lab 3 Assignment")
        self.assertEqual(items[1].clean_title, "Naloga 02")


class TestIgnoredCoursesMatching(unittest.TestCase):
    def test_course_pattern_matching(self):
        course = "um-PROGRAMIRANJE-I(UN)"
        self.assertTrue(database.matches_course_pattern("PROGRAMIRANJE I", course))
        self.assertTrue(database.matches_course_pattern("programiranje-i", course))
        self.assertTrue(database.matches_course_pattern("PROGRAMIRANJE", course))
        self.assertFalse(database.matches_course_pattern("RA", course))

        course_ra = "um-RA(UN)"
        self.assertTrue(database.matches_course_pattern("RA", course_ra))
        self.assertTrue(database.matches_course_pattern("um-ra", course_ra))
        self.assertFalse(database.matches_course_pattern("MUR", course_ra))

        course_long = "um-RAZVOJPROGRAMSKEOPREME9291"
        self.assertTrue(database.matches_course_pattern("RAZVOJ", course_long))
        # Short abbreviation "RA" shouldn't false-positive inside "RAZVOJ"
        self.assertFalse(database.matches_course_pattern("RA", course_long))


class TestDatabaseAndCompletion(unittest.TestCase):
    def setUp(self):
        self.temp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.temp_db.close()
        config.DB_PATH = Path(self.temp_db.name)
        database.init_db()

    def tearDown(self):
        if os.path.exists(self.temp_db.name):
            os.remove(self.temp_db.name)

    def test_user_flow(self):
        database.register_user(111, "student_user", "Student")
        u = database.get_user(111)
        self.assertEqual(u["username"], "student_user")
        self.assertEqual(u["is_subscribed"], 1)

        # Thresholds
        database.set_user_thresholds(111, "72,24,2")
        th = database.get_user_thresholds(111)
        self.assertEqual(th, [72, 24, 2])

    def test_deadline_cache_and_completion(self):
        now = datetime.now(timezone.utc)
        items = [
            Deadline("d1", "Test Assignment 1", "CS101", now + timedelta(hours=10)),
            Deadline("d2", "Test Assignment 2", "MATH201", now + timedelta(days=4)),
        ]
        count = database.save_or_update_deadlines(items)
        self.assertEqual(count, 2)

        cached = database.get_cached_deadlines(chat_id=111, filter_type="all")
        self.assertEqual(len(cached), 2)

        # Filter today & soon
        today_items = database.get_cached_deadlines(chat_id=111, filter_type="today")
        self.assertEqual(len(today_items), 1)
        self.assertEqual(today_items[0].uid, "d1")

        soon_items = database.get_cached_deadlines(chat_id=111, filter_type="soon")
        self.assertEqual(len(soon_items), 2)

        # Mark done
        target_id = cached[0].id
        done_res = database.mark_deadline_completed(chat_id=111, identifier=target_id, completed=True)
        self.assertIsNotNone(done_res)

        # Now active should exclude d1
        active = database.get_cached_deadlines(chat_id=111, filter_type="all", include_completed=False)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].uid, "d2")

        # Reactivate
        database.mark_deadline_completed(chat_id=111, identifier=target_id, completed=False)
        active2 = database.get_cached_deadlines(chat_id=111, filter_type="all", include_completed=False)
        self.assertEqual(len(active2), 2)

    def test_ignore_course_flow(self):
        now = datetime.now(timezone.utc)
        items = [
            Deadline("d1", "Lab 1", "um-PROGRAMIRANJE-I(UN)", now + timedelta(hours=12)),
            Deadline("d2", "Quiz 1", "um-RA(UN)", now + timedelta(days=2)),
        ]
        database.save_or_update_deadlines(items)

        # Ignore PROGRAMIRANJE I
        is_new, affected, matched = database.add_ignored_course(111, "PROGRAMIRANJE I")
        self.assertTrue(is_new)
        self.assertEqual(affected, 1)
        self.assertIn("um-PROGRAMIRANJE-I(UN)", matched)

        # Query active deadlines with include_ignored=False
        active = database.get_cached_deadlines(chat_id=111, include_ignored=False)
        self.assertEqual(len(active), 1)
        self.assertEqual(active[0].course, "um-RA(UN)")

        # Query all with include_ignored=True
        all_items = database.get_cached_deadlines(chat_id=111, include_ignored=True)
        self.assertEqual(len(all_items), 2)

        # Unignore
        removed, restored = database.remove_ignored_course(111, "PROGRAMIRANJE I")
        self.assertTrue(removed)
        self.assertEqual(restored, 1)

        active_after = database.get_cached_deadlines(chat_id=111, include_ignored=False)
        self.assertEqual(len(active_after), 2)

    def test_alert_tracking(self):
        chat_id = 111
        uid = "task_999"
        self.assertFalse(database.has_alert_been_sent(chat_id, uid, 24))
        database.mark_alert_as_sent(chat_id, uid, 24)
        self.assertTrue(database.has_alert_been_sent(chat_id, uid, 24))
        self.assertFalse(database.has_alert_been_sent(chat_id, uid, 3))


if __name__ == "__main__":
    unittest.main()
