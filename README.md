# Schengen Visa Slot Tracker

Polls VFS Global and TLScontact every 90 seconds for London-based Schengen visa appointment slots across 13 countries.  Sends a Telegram message the moment a new slot appears within the next 3 months.  De-duplicates alerts: the same slot will not trigger another message for 6 hours.

---

## 1. Setup

### 1.1 Create a Telegram Bot

1. Message `@BotFather` on Telegram.
2. Send `/newbot` and follow the prompts.  Copy the **bot token** (format: `123456:ABCdef...`).
3. Send any message to your new bot.
4. Get your **chat ID**:
   ```
   https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
   ```
   Look for `"chat": {"id": <number>}` in the response.  That number is your chat ID.

### 1.2 Register Portal Accounts

- **VFS Global**: create an account at https://visa.vfsglobal.com/gbr/en/ita (any country page works).
- **TLScontact**: create an account at https://fr.tlscontact.com/gb/LON/ (any country page works).

Both portals share one account per portal, not one per country.

### 1.3 Configure environment

```bash
cp .env.example .env
```

Edit `.env`:

| Variable | Description |
|---|---|
| `VFS_EMAIL` | Your VFS Global account email |
| `VFS_PASSWORD` | Your VFS Global password |
| `TLS_EMAIL` | Your TLScontact email |
| `TLS_PASSWORD` | Your TLScontact password |
| `TELEGRAM_BOT_TOKEN` | Token from BotFather |
| `TELEGRAM_CHAT_ID` | Your personal or group chat ID |
| `POLL_INTERVAL_SECONDS` | How often to poll each country (default 90) |
| `SLOT_WINDOW_DAYS` | Only alert for slots within this many days (default 90) |
| `ALERT_COOLDOWN_HOURS` | Minimum hours between repeated alerts for the same slot (default 6) |
| `LOG_LEVEL` | `DEBUG`, `INFO`, `WARNING` (default `INFO`) |
| `HEADLESS` | `true` for Docker/server, `false` for local debugging (default `true`) |

---

## 2. Running locally

```bash
# Install dependencies
pip install -r requirements.txt

# Install Playwright's Chromium browser
playwright install chromium

# Verify Telegram bot config (sends a test message)
python -m src.main --test-alert

# Start tracker
python -m src.main
```

Logs are written to stdout.  On a TTY they are pretty-printed; inside Docker they are JSON.

SQLite database is written to `data/tracker.db` (created automatically).

---

## 3. Running with Docker

```bash
# Build image
docker compose build

# Start in background
docker compose up -d

# Tail logs
docker compose logs -f

# Stop
docker compose down
```

The `data/` directory is mounted as a volume so the SQLite database persists across container restarts.

---

## 4. Discovering live API endpoints (--discover)

VFS Global and TLScontact are SPAs whose internal API paths can change without notice.  The `--discover` flag launches a visible Chromium window, performs the portal login, and then intercepts every XHR/fetch request for 30 seconds.  Use this to identify the real slot-check endpoint when the tracker reports no data.

```bash
# Discover VFS endpoints (opens real browser window)
python -m src.main --discover vfs

# Discover TLScontact endpoints
python -m src.main --discover tls

# Extend interception window to 60 seconds
python -m src.main --discover vfs --discover-duration 60
```

The command prints every intercepted request URL and Authorization header, then dumps the full list as JSON.  Look for URLs containing words like `slot`, `appointment`, or `availability`.  Once you have confirmed the real path, update `VFS_SLOT_API` in `src/scrapers/vfs.py` or `_TLS_API_PATHS` in `src/scrapers/tls.py`.

Discovery does not run in Docker (`HEADLESS=true` suppresses the GUI).  Run it locally with `HEADLESS=false`.

---

## 5. Troubleshooting

**No slots found / tracker runs but never alerts**

The most likely cause is that the portal API paths have changed.  Run `--discover` (see section 4) to find the live endpoints, then update the path constants in `src/scrapers/vfs.py` and `src/scrapers/tls.py`.

**Playwright login fails (email/password field not found)**

Login form selectors can change when portals update their UI.  Open `src/session/vfs_session.py` or `src/session/tls_session.py`, enable `headless=False` locally, and update the `page.fill(...)` selectors to match the current form.

**401 Unauthorized on every request**

The Bearer token captured at login is being rejected.  Common causes:
- VFS requires an additional CAPTCHA or MFA step — complete it manually once in `headless=False` mode so cookies are populated, then re-run.
- Session TTL is shorter than `TOKEN_TTL_MINUTES` (50 min) — reduce the constant in `vfs_session.py`.

**Telegram sends no message**

Run `--test-alert` first.  If that also fails, check:
- `TELEGRAM_BOT_TOKEN` has no surrounding spaces.
- The bot has been started (you have messaged it at least once).
- `TELEGRAM_CHAT_ID` is a plain integer, not a username.

**Container exits immediately**

Missing required environment variables will cause pydantic-settings to raise a `ValidationError` at startup.  Check `docker compose logs` for the specific missing variable.

**Rate limiting / IP ban**

Each country poll has a 2–4 second random jitter.  With 13 countries staggered 4 seconds apart, a full cycle takes roughly 52 seconds.  If you are rate-limited, increase `POLL_INTERVAL_SECONDS` to 180+ and consider running the tracker from a residential IP.

---

## 6. GitHub Actions + GitHub Pages setup

1. Push this repo to GitHub (public or private).
2. Go to repo Settings → Secrets → Actions. Add these secrets:
   - `VFS_EMAIL`, `VFS_PASSWORD`
   - `TLS_EMAIL`, `TLS_PASSWORD`
   - `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`
3. Go to repo Settings → Pages → Source: Deploy from branch → Branch: `main` → Folder: `/docs` → Save.
4. The workflow runs every 5 minutes automatically. First run may take 3–5 minutes to install Playwright.
5. Your dashboard will be live at: `https://YOUR_USERNAME.github.io/YOUR_REPO_NAME/`
6. Update `DATA_URL` in `docs/index.html` if slots don't load (see comment in the file).

Note on SQLite dedup: The `tracker.db` is cached between GitHub Actions runs using `actions/cache`.
The cache key rotates each run but restores the previous run's DB, so dedup works correctly.
If you want to reset all alerts, manually delete the cache in GitHub Actions → Caches.

---

## Project Structure

```
schengen-tracker/
├── src/
│   ├── main.py              # Entry point, CLI flags
│   ├── scheduler.py         # APScheduler job setup
│   ├── config.py            # Settings (pydantic-settings) + YAML loader
│   ├── models.py            # Pydantic models: Slot, Country, AlertRecord
│   ├── store.py             # aiosqlite: persist slots, de-dup alerts
│   ├── detector.py          # New-slot detection logic
│   ├── logging_config.py    # structlog configuration
│   ├── alerts/
│   │   └── telegram.py      # Telegram Bot API integration
│   ├── session/
│   │   ├── vfs_session.py   # Playwright VFS login + Bearer token capture
│   │   └── tls_session.py   # Playwright TLScontact login + cookie capture
│   └── scrapers/
│       ├── base.py          # Shared httpx GET with jitter
│       ├── vfs.py           # VFS slot API calls + response parsing
│       └── tls.py           # TLScontact slot API calls + response parsing
├── config/
│   └── countries.yaml       # All 13 countries, portal config
├── data/                    # SQLite DB (gitignored, volume-mounted in Docker)
├── .env.example
├── Dockerfile
├── docker-compose.yml
└── requirements.txt
```
