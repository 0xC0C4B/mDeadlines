# Telegram Moodle Deadlines & Reminders Bot 🎓🔔

An asynchronous Telegram reminder bot built with Python and `python-telegram-bot` (v22+). It synchronizes with your Moodle course calendar feed and delivers proactive alert notifications before assignment deadlines with interactive Telegram controls.

---

## ✨ Features

- 📅 **Interactive Deadlines Dashboard**:
  - View upcoming assignments, quizzes, and project milestones with `/deadlines` or `/start`.
  - Filter by urgency (`/today`, `/week`, `/urgent`) or course (`/course <name>`).
  - Interactive pagination with Next/Previous navigation buttons.
- ⏰ **Proactive Background Alerts**:
  - Automatically dispatches reminders before deadlines (e.g. at 48h, 24h, 3h, and 1h).
  - Background `JobQueue` monitors upcoming deadlines every few minutes.
  - Zero duplicate spam: SQLite database tracks sent notifications across all alert thresholds.
- 🚫 **Ignore Specific Subjects / Courses**:
  - Filter out unwanted or completed subjects (e.g. `/ignore PROGRAMIRANJE I` or `/ignore RA`).
  - Ignored subjects are automatically hidden from deadline lists and silenced from proactive reminder alerts.
  - Interactive `/courses` menu with one-click **Ignore** and **Unignore** buttons.
- 🎯 **Task Completion Tracking**:
  - Mark assignments as done directly from Telegram via `/done <id>` or by clicking the **✓ #ID** button.
  - Completed tasks are silenced from reminders and crossed off the list.
- 🔗 **Smart Feed Sync & Cloudflare/WAF Bypass**:
  - Automatically connects to Moodle feeds using `curl_cffi` browser impersonation to handle university firewalls and WAFs.
  - Seamlessly integrates with the local `moodle-deadlines` CLI database if present.
  - Allows each Telegram user to connect their personal Moodle calendar via `/setmoodle <url>`.
- 🧪 **Terminal Diagnostic Mode**:
  - Test and view your active deadlines directly from the terminal without Telegram using `.venv/bin/python bot.py --check` or `--sync`.

---

## 🚀 Getting Started

### 1. Set Up Telegram Bot Token

1. Open Telegram and search for [@BotFather](https://t.me/BotFather).
2. Send `/newbot`.
3. Choose a display name (e.g., `My University Deadlines Bot`) and username (e.g., `my_uni_deadlines_bot`).
4. Copy the HTTP API token provided by BotFather.
5. Add it to [`.env`](file:///home/user/VSC/telegram-bot/.env):
   ```env
   TELEGRAM_BOT_TOKEN=123456789:ABCdefGHIjklmNOPqrSTUvwxYZ
   ```

### 2. Verify Your Moodle Calendar Feed

The bot is already pre-configured to detect your Moodle calendar feed from [`.env`](file:///home/user/VSC/telegram-bot/.env):

```env
DEFAULT_MOODLE_ICAL_URL=https://ucilnice.arnes.si/calendar/export_execute.php?userid=...
```

You can test connectivity and inspect your upcoming deadlines directly in the terminal:

```bash
.venv/bin/python bot.py --sync
```

### 3. Launch the Bot

```bash
.venv/bin/python bot.py
```

Open Telegram, find your bot, and send `/start`.

---

## 🤖 Available Bot Commands

| Command | Description |
|---|---|
| `/start` | Open the interactive dashboard with status overview and quick buttons |
| `/deadlines` or `/list` | List all upcoming deadlines with pagination and action buttons |
| `/soon` | Show deadlines due soon (within 7 days, including urgent & today) |
| `/course <name>` | Filter deadlines by course name or code (e.g., `/course RA`) |
| `/courses` | Interactive menu of all courses with one-click ignore toggles |
| `/ignore <subject>` | Ignore a subject (e.g. `/ignore PROGRAMIRANJE I` or `/ignore RA`) |
| `/unignore <subject>` | Restore an ignored subject |
| `/ignored` | List all currently ignored subjects with remove buttons |
| `/done <id>` | Mark a task as completed (e.g., `/done 1`) |
| `/undone <id>` | Re-activate a completed task |
| `/sync` | Force an immediate sync with Moodle |
| `/setmoodle <url>` | Link your personal Moodle iCal URL |
| `/clearmoodle` | Reset custom URL (reverts to system default) |
| `/subscribe` | Enable proactive deadline reminder notifications |
| `/unsubscribe` | Mute reminder notifications |
| `/thresholds <h1,h2...>` | Customize alert warning hours (e.g., `/thresholds 72,24,3,1`) |
| `/settings` | Display your notification configuration |
| `/help` | Detailed command list and setup guide |

---

## 🧪 Running Unit Tests

Run the unit test suite to verify models, iCal parsing, and database transactions:

```bash
.venv/bin/python -m unittest test_bot.py
```

---

## 📁 File Structure

- [`bot.py`](file:///home/user/VSC/telegram-bot/bot.py): Telegram application, handlers, inline keyboards, pagination, and `JobQueue` background alert checker.
- [`sync_manager.py`](file:///home/user/VSC/telegram-bot/sync_manager.py): Coordinates fetching between remote Moodle URLs, local CLI database, and caches.
- [`models.py`](file:///home/user/VSC/telegram-bot/models.py): `Deadline` and `Urgency` data structures with formatting helpers and multilingual title cleanup.
- [`deadlines.py`](file:///home/user/VSC/telegram-bot/deadlines.py): Parser for iCalendar (`.ics`) feeds with `curl_cffi` WAF bypass.
- [`database.py`](file:///home/user/VSC/telegram-bot/database.py): SQLite persistence for users, cached deadlines, task completion states, and alert logs.
- [`config.py`](file:///home/user/VSC/telegram-bot/config.py): Environment configuration and feed detection.
- [`test_bot.py`](file:///home/user/VSC/telegram-bot/test_bot.py): Automated unit tests.
- [`Dockerfile`](file:///home/user/VSC/telegram-bot/Dockerfile) & [`docker-compose.yml`](file:///home/user/VSC/telegram-bot/docker-compose.yml): Cloud container deployment.
- [`moodle-bot.service`](file:///home/user/VSC/telegram-bot/moodle-bot.service): Background Linux systemd service.

---

## ☁️ 24/7 Hosting & Deployment

> [!IMPORTANT]
> **About TeleBotHost (telebothost.com):**
> TeleBotHost does **not** support standard Python applications or `pip` libraries. It only runs its proprietary JavaScript dialect called **TBL (Tele Bot Language)**. Because our bot requires Python, asynchronous JobQueues, and `curl_cffi` for university firewall/Cloudflare bypass, it cannot run directly on TeleBotHost without being rewritten in TBL.
> 
> Instead, you can host this Python bot 24/7 for free using any of the standard platforms below:

### Option 1: Render / Railway (Free Cloud Hosting)
1. Push this repository to your GitHub account.
2. Sign in to [Render](https://render.com) or [Railway](https://railway.app).
3. Create a **New Web Service / Background Worker** connected to your repo.
4. Set:
   - **Build Command**: `pip install -r requirements.txt`
   - **Start Command**: `python bot.py`
5. In Environment Variables, add:
   - `TELEGRAM_BOT_TOKEN=...`
   - `DEFAULT_MOODLE_ICAL_URL=...`

### Option 2: Docker
```bash
docker compose up -d --build
```

### Option 3: Systemd Service (Linux Server / Raspberry Pi)
```bash
sudo cp moodle-bot.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now moodle-bot
```
