from __future__ import annotations

import argparse
import html
import logging
import sys
from datetime import datetime
from typing import List, Optional

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CallbackQueryHandler,
    CommandHandler,
    ContextTypes,
)

import config
import database
from models import Deadline, Urgency
from sync_manager import sync_deadlines_for_user

logging.basicConfig(
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
    level=logging.INFO
)
logger = logging.getLogger("telegram_deadline_bot")

PAGE_SIZE = 5


def build_dashboard_keyboard(is_subscribed: bool = True) -> InlineKeyboardMarkup:
    """Main interactive menu buttons."""
    sub_label = "🔕 Mute Alerts" if is_subscribed else "🔔 Enable Alerts"
    keyboard = [
        [
            InlineKeyboardButton("📋 All Deadlines", callback_data="view:all:0"),
            InlineKeyboardButton("⚡ Due Soon (< 7d)", callback_data="view:soon:0"),
        ],
        [
            InlineKeyboardButton("📚 Subjects / Ignored", callback_data="action:courses"),
            InlineKeyboardButton("🔄 Sync Moodle", callback_data="action:sync"),
        ],
        [
            InlineKeyboardButton(sub_label, callback_data="action:toggle_sub"),
            InlineKeyboardButton("⚙️ Settings", callback_data="action:settings"),
        ],
        [
            InlineKeyboardButton("❓ Help & Guide", callback_data="action:help"),
        ]
    ]
    return InlineKeyboardMarkup(keyboard)


def build_deadlines_pagination_keyboard(
    filter_type: str,
    offset: int,
    total_count: int,
    deadlines_on_page: List[Deadline]
) -> InlineKeyboardMarkup:
    """Build navigation and quick action buttons for deadline lists."""
    rows = []

    # Done action buttons for each displayed deadline
    done_buttons = []
    for d in deadlines_on_page:
        if not d.is_completed and d.id is not None:
            done_buttons.append(
                InlineKeyboardButton(f"✓ #{d.id}", callback_data=f"done:{d.id}:{filter_type}:{offset}")
            )
    if done_buttons:
        for i in range(0, len(done_buttons), 3):
            rows.append(done_buttons[i:i+3])

    # Navigation buttons
    nav_buttons = []
    if offset > 0:
        prev_offset = max(0, offset - PAGE_SIZE)
        nav_buttons.append(InlineKeyboardButton("⬅️ Previous", callback_data=f"view:{filter_type}:{prev_offset}"))
    
    if offset + PAGE_SIZE < total_count:
        next_offset = offset + PAGE_SIZE
        nav_buttons.append(InlineKeyboardButton("Next ➡️", callback_data=f"view:{filter_type}:{next_offset}"))

    if nav_buttons:
        rows.append(nav_buttons)

    # Filter toggle button
    if filter_type == "all":
        rows.append([InlineKeyboardButton("⚡ Filter: Due Soon (< 7d)", callback_data="view:soon:0")])
    elif filter_type == "soon":
        rows.append([InlineKeyboardButton("📋 Show All Deadlines", callback_data="view:all:0")])

    # Return to menu button
    rows.append([
        InlineKeyboardButton("🔄 Refresh", callback_data=f"view:{filter_type}:{offset}"),
        InlineKeyboardButton("🏠 Menu", callback_data="action:menu")
    ])

    return InlineKeyboardMarkup(rows)


def render_deadlines_view(
    deadlines: List[Deadline],
    filter_type: str = "all",
    offset: int = 0
) -> tuple[str, InlineKeyboardMarkup]:
    """Generate formatted text and pagination markup for deadlines list."""
    total = len(deadlines)
    filter_titles = {
        "all": "All Upcoming Deadlines",
        "soon": "Deadlines Due Soon (≤ 7 Days)",
        "urgent": "Urgent Deadlines (< 72h)",
        "week": "Deadlines Due This Week",
        "today": "Deadlines Due Within 24 Hours",
    }
    title = filter_titles.get(filter_type, "Upcoming Deadlines")

    if not deadlines:
        msg = (
            f"🎉 <b>{title}</b>\n\n"
            "No matching deadlines found! You're completely up to date.\n\n"
            "<i>Use /sync to refresh from Moodle, /courses to check subjects, or /setmoodle to link a calendar.</i>"
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="action:menu")]])
        return msg, markup

    slice_items = deadlines[offset:offset + PAGE_SIZE]
    page_num = (offset // PAGE_SIZE) + 1
    total_pages = ((total - 1) // PAGE_SIZE) + 1

    header = f"📅 <b>{title}</b> (Page {page_num}/{total_pages})\n"
    header += "──────────────────────────\n\n"

    cards = []
    for d in slice_items:
        cards.append(d.format_html(id_prefix=True))

    footer = "\n──────────────────────────\n"
    footer += "💡 <i>Click <b>✓ #ID</b> below or type <code>/done &lt;id&gt;</code> to complete a task.</i>"

    content = header + "\n".join(cards) + footer
    markup = build_deadlines_pagination_keyboard(filter_type, offset, total, slice_items)
    return content, markup


def render_courses_view(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Generate interactive course management list with ignore toggles."""
    courses = database.get_all_courses(chat_id)
    ignored_patterns = database.get_ignored_courses(chat_id)

    if not courses:
        text = (
            "📚 <b>Course & Subject Manager</b>\n\n"
            "No courses found in your calendar yet.\n"
            "Run /sync to fetch courses from Moodle."
        )
        markup = InlineKeyboardMarkup([[InlineKeyboardButton("🏠 Menu", callback_data="action:menu")]])
        return text, markup

    text = (
        "📚 <b>Course & Subject Manager</b>\n\n"
        "Click any subject button below to <b>Ignore</b> or <b>Unignore</b> it.\n"
        "Ignored subjects will NOT trigger alerts and are hidden from your active task lists.\n\n"
    )

    keyboard = []
    for c in courses:
        name = c["name"]
        count = c["count"]
        is_ignored = c["is_ignored"]

        status_emoji = "🚫" if is_ignored else "🟢"
        strike = "<s>" if is_ignored else ""
        strike_end = "</s>" if is_ignored else ""
        status_word = " (Ignored)" if is_ignored else ""

        safe_name = html.escape(name)
        text += f"{status_emoji} {strike}<b>{safe_name}</b>{strike_end} — <code>{count}</code> task(s){status_word}\n"

        btn_action = "✅ Unignore" if is_ignored else "🚫 Ignore"
        # Shorten button text if very long
        short_title = name if len(name) <= 20 else name[:18] + ".."
        keyboard.append([
            InlineKeyboardButton(
                f"{btn_action}: {short_title}",
                callback_data=f"toggle_course:{name}"
            )
        ])

    keyboard.append([
        InlineKeyboardButton(f"🚫 Ignored List ({len(ignored_patterns)})", callback_data="action:ignored"),
        InlineKeyboardButton("🏠 Menu", callback_data="action:menu")
    ])

    return text, InlineKeyboardMarkup(keyboard)


def render_ignored_view(chat_id: int) -> tuple[str, InlineKeyboardMarkup]:
    """Generate list of currently ignored subject patterns with remove buttons."""
    ignored = database.get_ignored_courses(chat_id)

    if not ignored:
        text = (
            "🚫 <b>Ignored Subjects List</b>\n\n"
            "You have no ignored subjects. All course assignments will receive reminders.\n\n"
            "To ignore a subject, use <code>/ignore &lt;name&gt;</code> (e.g. <code>/ignore PROGRAMIRANJE I</code>) "
            "or click <b>📚 Manage Courses</b> below."
        )
        markup = InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 Manage Courses", callback_data="action:courses")],
            [InlineKeyboardButton("🏠 Menu", callback_data="action:menu")]
        ])
        return text, markup

    text = (
        f"🚫 <b>Ignored Subjects ({len(ignored)} active)</b>\n\n"
        "Assignments from these subjects are hidden from notifications and views:\n\n"
    )

    keyboard = []
    for pat in ignored:
        text += f"• <code>{html.escape(pat)}</code>\n"
        keyboard.append([
            InlineKeyboardButton(f"❌ Unignore '{pat[:20]}'", callback_data=f"unignore_pat:{pat}")
        ])

    keyboard.append([
        InlineKeyboardButton("📚 Manage Courses", callback_data="action:courses"),
        InlineKeyboardButton("🏠 Menu", callback_data="action:menu")
    ])

    return text, InlineKeyboardMarkup(keyboard)


# ============================================================================
# Command Handlers
# ============================================================================

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /start command with rich interactive welcome dashboard."""
    if not update.effective_user or not update.effective_chat:
        return

    chat_id = update.effective_chat.id
    user = update.effective_user
    database.register_user(chat_id, user.username, user.first_name)
    user_record = database.get_user(chat_id)
    is_sub = bool(user_record.get("is_subscribed", 1)) if user_record else True

    # Initial sync in background if cache is empty
    cached = database.get_cached_deadlines(chat_id=chat_id, include_ignored=False)
    if not cached:
        sync_deadlines_for_user(chat_id)
        cached = database.get_cached_deadlines(chat_id=chat_id, include_ignored=False)

    soon_count = sum(1 for d in cached if 0 <= d.hours_remaining <= 168)
    urgent_count = sum(1 for d in cached if 0 <= d.hours_remaining <= 72)
    user_name = html.escape(user.first_name or "Student")
    ignored_count = len(database.get_ignored_courses(chat_id))

    soon_info = f"{soon_count} ({urgent_count} urgent)" if urgent_count > 0 else str(soon_count)

    welcome_text = (
        f"👋 <b>Welcome back, {user_name}!</b>\n\n"
        "🎓 <b>Moodle Deadlines & Reminders Bot</b>\n\n"
        f"📊 <b>Status Overview:</b>\n"
        f"• <b>Active Tasks:</b> <code>{len(cached)}</code>\n"
        f"• <b>Due Soon (≤ 7d):</b> <code>{soon_info}</code>\n"
        f"• <b>Ignored Subjects:</b> <code>{ignored_count}</code>\n"
        f"• <b>Reminders:</b> {'🔔 Enabled' if is_sub else '🔕 Muted'}\n\n"
        "Choose an action below to manage your assignments:"
    )

    markup = build_dashboard_keyboard(is_sub)
    if update.message:
        await update.message.reply_html(welcome_text, reply_markup=markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(welcome_text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def cmd_help(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /help command."""
    help_text = (
        "📖 <b>Moodle Deadline Bot Guide & Commands</b>\n\n"
        "<b>Navigation & Queries:</b>\n"
        "• /deadlines or /list - View all upcoming deadlines\n"
        "• /soon - Deadlines due in the next 7 days (including urgent & today)\n"
        "• /course &lt;name&gt; - Filter deadlines by course code/name\n\n"
        "<b>Subject & Course Management:</b>\n"
        "• /courses - Interactive menu of all courses with ignore toggles\n"
        "• /ignore &lt;subject&gt; - Ignore subject (e.g. <code>/ignore PROGRAMIRANJE I</code>)\n"
        "• /unignore &lt;subject&gt; - Stop ignoring a subject\n"
        "• /ignored - View list of currently ignored subjects\n\n"
        "<b>Task Management:</b>\n"
        "• /done &lt;id&gt; - Mark a task completed (silences alerts)\n"
        "• /undone &lt;id&gt; - Unmark a completed task\n"
        "• /sync - Fetch fresh deadlines from Moodle immediately\n\n"
        "<b>Alert & Feed Settings:</b>\n"
        "• /subscribe - Turn ON proactive alerts\n"
        "• /unsubscribe - Turn OFF proactive alerts\n"
        "• /thresholds &lt;hours&gt; - Customize warning hours (e.g. <code>/thresholds 48,24,3,1</code>)\n"
        "• /setmoodle &lt;url&gt; - Connect your personal Moodle iCal URL\n"
        "• /clearmoodle - Remove personal URL (use default)\n"
        "• /settings - View your full configuration\n\n"
        "<b>Exporting your Moodle Calendar:</b>\n"
        "1. Open Moodle in your browser.\n"
        "2. Click <b>Calendar</b> &gt; <b>Export calendar</b>.\n"
        "3. Choose <i>Events related to courses</i> and <i>Recent and next 60 days</i>.\n"
        "4. Click <b>Get calendar URL</b> and send it with <code>/setmoodle &lt;url&gt;</code>."
    )
    if update.message:
        await update.message.reply_html(help_text)
    elif update.callback_query:
        await update.callback_query.message.reply_html(help_text)


async def cmd_deadlines(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /deadlines and /list."""
    chat_id = update.effective_chat.id
    deadlines = database.get_cached_deadlines(chat_id=chat_id, filter_type="all", include_ignored=False)
    if not deadlines:
        sync_deadlines_for_user(chat_id)
        deadlines = database.get_cached_deadlines(chat_id=chat_id, filter_type="all", include_ignored=False)

    text, markup = render_deadlines_view(deadlines, filter_type="all", offset=0)
    await update.message.reply_html(text, reply_markup=markup, disable_web_page_preview=True)


async def cmd_soon(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /soon - deadlines due in the next 7 days (covers today, 24h, 72h, this week)."""
    chat_id = update.effective_chat.id
    deadlines = database.get_cached_deadlines(chat_id=chat_id, filter_type="soon", include_ignored=False)
    text, markup = render_deadlines_view(deadlines, filter_type="soon", offset=0)
    await update.message.reply_html(text, reply_markup=markup, disable_web_page_preview=True)


async def cmd_today(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /today."""
    await cmd_soon(update, context)


async def cmd_week(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /week."""
    await cmd_soon(update, context)


async def cmd_urgent(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /urgent."""
    await cmd_soon(update, context)


async def cmd_course(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /course <filter>."""
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_html(
            "⚠️ <b>Please specify a course name or code.</b>\n"
            "Example: <code>/course Algorithms</code> or <code>/course CS</code>"
        )
        return

    filter_str = " ".join(context.args)
    deadlines = database.get_cached_deadlines(chat_id=chat_id, course_filter=filter_str, include_ignored=False)

    if not deadlines:
        await update.message.reply_html(
            f"🔍 No upcoming active deadlines found matching: <b>{html.escape(filter_str)}</b>\n"
            "<i>(If this subject was ignored, check /ignored or /courses)</i>"
        )
        return

    text, markup = render_deadlines_view(deadlines, filter_type="all", offset=0)
    await update.message.reply_html(text, reply_markup=markup, disable_web_page_preview=True)


async def cmd_ignore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ignore <subject>."""
    chat_id = update.effective_chat.id
    if not context.args:
        text, markup = render_courses_view(chat_id)
        await update.message.reply_html(
            "⚠️ <b>Please specify a course to ignore, or select from the list below:</b>\n"
            "Example: <code>/ignore PROGRAMIRANJE I</code> or <code>/ignore RA</code>\n\n" + text,
            reply_markup=markup
        )
        return

    pattern = " ".join(context.args).strip()
    is_new, affected_count, matching_courses = database.add_ignored_course(chat_id, pattern)

    matched_text = f"<i>{', '.join(html.escape(c) for c in matching_courses)}</i>" if matching_courses else "<i>None currently in calendar</i>"

    await update.message.reply_html(
        f"🚫 <b>Subject Ignored:</b> <code>{html.escape(pattern)}</code>\n\n"
        f"• <b>Matching Course(s):</b> {matched_text}\n"
        f"• <b>Filtered Deadlines:</b> <code>{affected_count}</code>\n\n"
        "You will no longer receive proactive alerts or see deadlines for this subject.\n\n"
        f"💡 <i>To re-enable, use <code>/unignore {html.escape(pattern)}</code> or /ignored.</i>",
        reply_markup=InlineKeyboardMarkup([
            [InlineKeyboardButton("📚 Manage Courses", callback_data="action:courses")],
            [InlineKeyboardButton("📋 Check Deadlines", callback_data="view:all:0")]
        ])
    )


async def cmd_unignore(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /unignore <subject>."""
    chat_id = update.effective_chat.id
    if not context.args:
        text, markup = render_ignored_view(chat_id)
        await update.message.reply_html(text, reply_markup=markup)
        return

    pattern = " ".join(context.args).strip()
    removed, count = database.remove_ignored_course(chat_id, pattern)

    if removed:
        await update.message.reply_html(
            f"✅ <b>Subject Unignored:</b> <code>{html.escape(pattern)}</code>\n\n"
            f"Restored <b>{count}</b> deadline(s) to your notifications and active views.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("📋 View Deadlines", callback_data="view:all:0")],
                [InlineKeyboardButton("📚 Manage Courses", callback_data="action:courses")]
            ])
        )
    else:
        await update.message.reply_html(
            f"⚠️ <code>{html.escape(pattern)}</code> was not found in your ignored subjects list.\n"
            "Use /ignored to see all configured ignore patterns."
        )


async def cmd_courses(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /courses - interactive course overview."""
    chat_id = update.effective_chat.id
    text, markup = render_courses_view(chat_id)
    if update.message:
        await update.message.reply_html(text, reply_markup=markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def cmd_ignored(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /ignored - list of ignored courses."""
    chat_id = update.effective_chat.id
    text, markup = render_ignored_view(chat_id)
    if update.message:
        await update.message.reply_html(text, reply_markup=markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)


async def cmd_done(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /done <id>."""
    chat_id = update.effective_chat.id
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_html(
            "⚠️ <b>Please provide the deadline ID to complete.</b>\n"
            "Example: <code>/done 1</code>\n"
            "(You can find the ID <code>#ID</code> next to each task in /deadlines)"
        )
        return

    deadline_id = int(context.args[0])
    deadline = database.mark_deadline_completed(chat_id, deadline_id, completed=True)

    if deadline:
        await update.message.reply_html(
            f"🎉 <b>Marked as completed!</b>\n"
            f"<s>{html.escape(deadline.clean_title)}</s>\n\n"
            "You won't receive further reminder alerts for this assignment."
        )
    else:
        await update.message.reply_html(
            f"❌ Could not find assignment with ID <code>#{deadline_id}</code>."
        )


async def cmd_undone(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /undone <id>."""
    chat_id = update.effective_chat.id
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_html("⚠️ Example: <code>/undone 1</code>")
        return

    deadline_id = int(context.args[0])
    deadline = database.mark_deadline_completed(chat_id, deadline_id, completed=False)

    if deadline:
        await update.message.reply_html(
            f"🔄 <b>Reactivated task!</b>\n"
            f"<b>{html.escape(deadline.clean_title)}</b>\n\n"
            "Reminder alerts are now re-enabled for this assignment."
        )
    else:
        await update.message.reply_html(f"❌ Could not find assignment with ID <code>#{deadline_id}</code>.")


async def cmd_sync(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /sync."""
    chat_id = update.effective_chat.id
    loading = await update.message.reply_text("🔄 Syncing latest deadlines from Moodle...")
    result = sync_deadlines_for_user(chat_id)
    await loading.delete()

    source_names = {
        "moodle_url": "Remote Moodle Calendar Feed",
        "moodle_cli_db": "Moodle CLI Database",
        "local_cache": "Local Cache",
        "demo": "Demo Feed"
    }
    source_title = source_names.get(result.source, result.source)

    if result.success:
        await update.message.reply_html(
            f"✅ <b>Sync Completed!</b>\n\n"
            f"• <b>Items Synced:</b> <code>{result.items_count}</code>\n"
            f"• <b>Source:</b> <i>{source_title}</i>\n\n"
            "Type /deadlines to view your updated assignments."
        )
    else:
        await update.message.reply_html(
            f"❌ <b>Sync Failed.</b>\n"
            f"<i>Error:</i> <code>{html.escape(result.error or 'Unknown')}</code>"
        )


async def cmd_setmoodle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /setmoodle <url>."""
    chat_id = update.effective_chat.id
    if not context.args:
        await update.message.reply_html(
            "⚠️ <b>Please provide your Moodle calendar URL.</b>\n\n"
            "Usage:\n"
            "<code>/setmoodle https://moodle.your-uni.edu/calendar/export_execute.php?...</code>\n\n"
            "See /help for step-by-step export instructions."
        )
        return

    url = context.args[0].strip()
    database.set_user_moodle_url(chat_id, url)
    database.purge_demo_deadlines()
    loading = await update.message.reply_text("⏳ Verifying and syncing your Moodle feed...")
    result = sync_deadlines_for_user(chat_id)
    await loading.delete()

    if result.success and result.source == "moodle_url":
        await update.message.reply_html(
            f"✅ <b>Moodle Calendar Connected!</b>\n\n"
            f"Successfully synced <b>{result.items_count}</b> upcoming deadline(s).\n"
            "All demo placeholders have been removed. Proactive reminders are enabled.",
            reply_markup=build_dashboard_keyboard(True)
        )
    else:
        await update.message.reply_html(
            "⚠️ <b>URL saved, but feed verification encountered an issue.</b>\n"
            "Please verify that the export URL is accessible and includes your token."
        )


async def cmd_clearmoodle(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /clearmoodle."""
    chat_id = update.effective_chat.id
    database.set_user_moodle_url(chat_id, None)
    database.purge_demo_deadlines()
    database.clear_all_deadlines()

    if config.DEFAULT_MOODLE_ICAL_URL:
        result = sync_deadlines_for_user(chat_id)
        msg = (
            f"🗑️ <b>Custom calendar feed removed.</b>\n\n"
            f"Re-synced with system default calendar (<code>{result.items_count}</code> tasks).\n"
            "All dummy demo assignments have been purged."
        )
    else:
        msg = (
            "🗑️ <b>Custom calendar feed removed.</b>\n\n"
            "All custom and demo assignments have been completely cleared from storage.\n"
            "Use <code>/setmoodle &lt;url&gt;</code> to link your Moodle calendar."
        )
    await update.message.reply_html(msg, reply_markup=build_dashboard_keyboard(True))


async def cmd_cleardemo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /cleardemo."""
    count = database.purge_demo_deadlines()
    await update.message.reply_html(
        f"🧹 <b>Demo assignments purged!</b>\n"
        f"Removed <b>{count}</b> placeholder demo assignment(s) from your storage."
    )


async def cmd_subscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /subscribe."""
    chat_id = update.effective_chat.id
    database.set_user_subscription(chat_id, True)
    thresholds = database.get_user_thresholds(chat_id)
    thresh_str = ", ".join(f"{h}h" for h in thresholds)
    await update.message.reply_html(
        "🔔 <b>Proactive reminders are now ENABLED.</b>\n"
        f"You will receive alerts at: <b>{thresh_str}</b> before due dates.",
        reply_markup=build_dashboard_keyboard(True)
    )


async def cmd_unsubscribe(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /unsubscribe."""
    chat_id = update.effective_chat.id
    database.set_user_subscription(chat_id, False)
    await update.message.reply_html(
        "🔕 <b>Proactive reminders are now MUTED.</b>\n"
        "You can still check deadlines anytime using /deadlines.",
        reply_markup=build_dashboard_keyboard(False)
    )


async def cmd_thresholds(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /thresholds <hours>."""
    chat_id = update.effective_chat.id
    if not context.args:
        current = database.get_user_thresholds(chat_id)
        curr_str = ", ".join(str(h) for h in current)
        await update.message.reply_html(
            f"⚙️ <b>Current Alert Thresholds:</b> <code>{curr_str}</code> hours before deadline.\n\n"
            "To change, specify hours separated by commas:\n"
            "Example: <code>/thresholds 72, 24, 6, 1</code>"
        )
        return

    raw = "".join(context.args)
    valid_hours = [int(h.strip()) for h in raw.split(",") if h.strip().isdigit()]
    if not valid_hours:
        await update.message.reply_html("❌ Please provide valid integer hours separated by commas.")
        return

    valid_hours = sorted(valid_hours, reverse=True)
    save_str = ",".join(str(h) for h in valid_hours)
    database.set_user_thresholds(chat_id, save_str)

    await update.message.reply_html(
        f"✅ <b>Alert thresholds updated!</b>\n"
        f"You will now receive alerts at: <b>{', '.join(f'{h}h' for h in valid_hours)}</b> before due dates."
    )


async def cmd_settings(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle /settings."""
    chat_id = update.effective_chat.id
    user = database.get_user(chat_id)
    is_sub = bool(user.get("is_subscribed", 1)) if user else True
    thresholds = database.get_user_thresholds(chat_id)
    thresh_str = ", ".join(f"{h}h" for h in thresholds)
    ignored_count = len(database.get_ignored_courses(chat_id))

    has_custom = bool(user.get("moodle_url")) if user else False
    cal_source = "🔗 Custom Moodle Feed" if has_custom else (
        "🌐 System Feed" if config.DEFAULT_MOODLE_ICAL_URL else "🧪 Demo Feed"
    )

    settings_text = (
        "⚙️ <b>Your Notification Settings:</b>\n\n"
        f"• <b>Alerts Status:</b> {'✅ Active' if is_sub else '❌ Muted'}\n"
        f"• <b>Calendar Source:</b> {cal_source}\n"
        f"• <b>Alert Triggers:</b> {thresh_str} before deadline\n"
        f"• <b>Ignored Subjects:</b> <code>{ignored_count}</code> configured (/ignored)\n"
        f"• <b>Sync Interval:</b> Every {config.CHECK_INTERVAL_SECONDS // 60} minutes\n"
        f"• <b>Timezone:</b> <code>{config.TIMEZONE_NAME}</code>\n\n"
        "Use the buttons below to adjust preferences:"
    )

    markup = build_dashboard_keyboard(is_sub)
    if update.message:
        await update.message.reply_html(settings_text, reply_markup=markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text(settings_text, reply_markup=markup, parse_mode=ParseMode.HTML)


# ============================================================================
# Callback Query Handlers (Inline Buttons)
# ============================================================================

async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Handle interactive inline keyboard clicks."""
    query = update.callback_query
    if not query or not query.data:
        return
    await query.answer()

    chat_id = update.effective_chat.id
    data = query.data

    if data.startswith("view:"):
        _, filter_type, offset_str = data.split(":")
        offset = int(offset_str)
        deadlines = database.get_cached_deadlines(chat_id=chat_id, filter_type=filter_type, include_ignored=False)
        text, markup = render_deadlines_view(deadlines, filter_type=filter_type, offset=offset)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    elif data.startswith("done:"):
        _, dead_id_str, filter_type, offset_str = data.split(":")
        deadline_id = int(dead_id_str)
        offset = int(offset_str)
        database.mark_deadline_completed(chat_id, deadline_id, completed=True)

        deadlines = database.get_cached_deadlines(chat_id=chat_id, filter_type=filter_type, include_ignored=False)
        text, markup = render_deadlines_view(deadlines, filter_type=filter_type, offset=offset)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML, disable_web_page_preview=True)

    elif data.startswith("alert_done:"):
        _, dead_id_str = data.split(":")
        database.mark_deadline_completed(chat_id, int(dead_id_str), completed=True)
        await query.message.edit_reply_markup(reply_markup=None)
        await query.message.reply_html(f"✅ Marked task <code>#{dead_id_str}</code> as done. Alerts silenced!")

    elif data.startswith("toggle_course:"):
        course_name = data[len("toggle_course:"):]
        # Check if already ignored
        ignored = database.get_ignored_courses(chat_id)
        if any(database.matches_course_pattern(p, course_name) for p in ignored):
            database.remove_ignored_course(chat_id, course_name)
        else:
            database.add_ignored_course(chat_id, course_name)

        text, markup = render_courses_view(chat_id)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    elif data.startswith("unignore_pat:"):
        pattern = data[len("unignore_pat:"):]
        database.remove_ignored_course(chat_id, pattern)
        text, markup = render_ignored_view(chat_id)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    elif data == "action:courses":
        text, markup = render_courses_view(chat_id)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    elif data == "action:ignored":
        text, markup = render_ignored_view(chat_id)
        await query.message.edit_text(text, reply_markup=markup, parse_mode=ParseMode.HTML)

    elif data == "action:sync":
        await query.message.reply_text("🔄 Syncing deadlines...")
        sync_deadlines_for_user(chat_id)
        deadlines = database.get_cached_deadlines(chat_id=chat_id, filter_type="all", include_ignored=False)
        text, markup = render_deadlines_view(deadlines, filter_type="all", offset=0)
        await query.message.reply_html(text, reply_markup=markup, disable_web_page_preview=True)

    elif data == "action:toggle_sub":
        user = database.get_user(chat_id)
        current = bool(user.get("is_subscribed", 1)) if user else True
        new_sub = not current
        database.set_user_subscription(chat_id, new_sub)
        status_word = "enabled 🔔" if new_sub else "muted 🔕"
        await query.message.reply_html(f"Automated reminders have been <b>{status_word}</b>.")
        await cmd_start(update, context)

    elif data == "action:settings":
        await cmd_settings(update, context)

    elif data == "action:help":
        await cmd_help(update, context)

    elif data == "action:menu":
        await cmd_start(update, context)


# ============================================================================
# Scheduled Background Jobs
# ============================================================================

async def background_sync_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job to sync Moodle feeds and update the database."""
    logger.info("Executing scheduled Moodle sync job...")
    try:
        sync_deadlines_for_user(None)
    except Exception as e:
        logger.error(f"Scheduled sync encountered an error: {e}")


async def background_alert_checker_job(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Periodic job to evaluate approaching deadlines and dispatch alerts."""
    logger.info("Executing scheduled deadline alert checker...")
    users = database.get_subscribed_users()
    if not users:
        return

    for user in users:
        chat_id = user["chat_id"]
        thresholds = database.get_user_thresholds(chat_id)
        # Exclude ignored courses so no alerts are ever dispatched for them
        deadlines = database.get_cached_deadlines(chat_id=chat_id, filter_type="all", include_ignored=False)

        for d in deadlines:
            hours = d.hours_remaining
            if hours <= 0:
                continue

            for th in thresholds:
                if hours <= th:
                    if not database.has_alert_been_sent(chat_id, d.uid, th):
                        alert_text = (
                            f"🚨 <b>DEADLINE ALERT: Due in &lt; {th} Hours!</b>\n\n"
                            f"{d.format_html(id_prefix=True)}\n"
                            "⚠️ <i>Please make sure to finalize and submit your assignment in time!</i>"
                        )
                        btn_list = []
                        if d.url:
                            btn_list.append(InlineKeyboardButton("🔗 Open in Moodle", url=d.url))
                        if d.id is not None:
                            btn_list.append(InlineKeyboardButton("✅ Mark as Done", callback_data=f"alert_done:{d.id}"))

                        reply_markup = InlineKeyboardMarkup([btn_list]) if btn_list else None

                        try:
                            await context.bot.send_message(
                                chat_id=chat_id,
                                text=alert_text,
                                parse_mode=ParseMode.HTML,
                                reply_markup=reply_markup,
                                disable_web_page_preview=True
                            )
                            database.mark_alert_as_sent(chat_id, d.uid, th)
                            logger.info(f"Dispatched {th}h alert to {chat_id} for '{d.title}'")
                        except Exception as send_err:
                            logger.error(f"Failed to deliver alert to {chat_id}: {send_err}")
                        break


# ============================================================================
# Main Entry Point & CLI Diagnostics
# ============================================================================

def run_cli_diagnostics(sync_first: bool = False) -> None:
    """Command-line utility to test connectivity and output deadlines."""
    print("=" * 65)
    print("🎓 Moodle Deadlines & Reminder Bot — Diagnostic Mode")
    print("=" * 65)
    database.init_db()

    if sync_first:
        print("\n🔄 Running Moodle feed synchronization...")
        result = sync_deadlines_for_user()
        print(f"Sync result: {result.source} (Count: {result.items_count})")

    ignored = database.get_ignored_courses()
    if ignored:
        print(f"Ignored subject patterns ({len(ignored)}): {', '.join(ignored)}")

    courses = database.get_all_courses()
    print(f"\nAll courses ({len(courses)}):")
    for c in courses:
        tag = "[IGNORED]" if c["is_ignored"] else "[ACTIVE]"
        print(f"  • {tag} {c['name']} ({c['count']} deadlines)")

    deadlines = database.get_cached_deadlines(include_ignored=False)
    print(f"\nActive upcoming deadline(s) ({len(deadlines)}):\n")

    for d in deadlines:
        print(f"[{d.urgency_emoji}] #{d.id or '?'} {d.clean_title}")
        print(f"    Course: {d.course}")
        print(f"    Due:    {d.due_date.strftime('%Y-%m-%d %H:%M UTC')} ({d.format_countdown()})")
        if d.url:
            print(f"    Link:   {d.url}")
        print()

    print("=" * 65)


def main() -> None:
    parser = argparse.ArgumentParser(description="Moodle Deadlines & Reminders Telegram Bot")
    parser.add_argument("--check", action="store_true", help="Print active deadlines to terminal and exit")
    parser.add_argument("--sync", action="store_true", help="Sync Moodle feed and print deadlines to terminal")
    args = parser.parse_args()

    database.init_db()

    if args.check or args.sync:
        run_cli_diagnostics(sync_first=args.sync)
        return

    # Check for Bot Token
    if not config.BOT_TOKEN or config.BOT_TOKEN == "your_bot_token_here":
        print("\n" + "=" * 65)
        print("⚠️  TELEGRAM_BOT_TOKEN is not configured yet!")
        print("=" * 65)
        print("To launch your bot:")
        print("1. Open Telegram and search for @BotFather (https://t.me/BotFather).")
        print("2. Send /newbot and choose a name and username.")
        print("3. Copy the HTTP API token provided by BotFather.")
        print("4. Add it to .env in this directory:")
        print("     TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklmNOPqrSTUvwxYZ")
        print("5. Start the bot with:")
        print("     .venv/bin/python bot.py")
        print("\n💡 You can also test deadline syncing in terminal right now with:")
        print("     .venv/bin/python bot.py --sync")
        print("=" * 65 + "\n")
        sys.exit(1)

    # Build Application
    app = ApplicationBuilder().token(config.BOT_TOKEN).build()

    # Commands
    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("help", cmd_help))
    app.add_handler(CommandHandler(["deadlines", "list"], cmd_deadlines))
    app.add_handler(CommandHandler(["soon", "today", "week", "urgent"], cmd_soon))
    app.add_handler(CommandHandler("course", cmd_course))
    app.add_handler(CommandHandler("courses", cmd_courses))
    app.add_handler(CommandHandler("ignore", cmd_ignore))
    app.add_handler(CommandHandler("unignore", cmd_unignore))
    app.add_handler(CommandHandler("ignored", cmd_ignored))
    app.add_handler(CommandHandler("done", cmd_done))
    app.add_handler(CommandHandler("undone", cmd_undone))
    app.add_handler(CommandHandler("sync", cmd_sync))
    app.add_handler(CommandHandler("setmoodle", cmd_setmoodle))
    app.add_handler(CommandHandler("clearmoodle", cmd_clearmoodle))
    app.add_handler(CommandHandler("cleardemo", cmd_cleardemo))
    app.add_handler(CommandHandler("subscribe", cmd_subscribe))
    app.add_handler(CommandHandler("unsubscribe", cmd_unsubscribe))
    app.add_handler(CommandHandler("thresholds", cmd_thresholds))
    app.add_handler(CommandHandler("settings", cmd_settings))

    # Buttons
    app.add_handler(CallbackQueryHandler(handle_callback_query))

    # Background Jobs
    if app.job_queue:
        app.job_queue.run_repeating(
            background_sync_job,
            interval=config.CHECK_INTERVAL_SECONDS,
            first=10
        )
        app.job_queue.run_repeating(
            background_alert_checker_job,
            interval=300,
            first=20
        )
        logger.info("Configured scheduled jobs for Moodle sync and alert delivery.")
    else:
        logger.warning("JobQueue unavailable. Scheduled reminders will not trigger.")

    print("\n🚀 Telegram Bot is running! Press Ctrl+C to terminate.")
    app.run_polling()


if __name__ == "__main__":
    main()
