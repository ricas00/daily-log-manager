# Daily Log Manager

Mobile-first web app + small local server for manually logging daily events
into your day, and (later) for approving/rejecting suggested entries that the
Hermes agent collects from your sources (Notion tasks, Google Calendar,
Hermes sessions, GitHub issues, Productivity tracker) and writes to your
Notion "Daily Text Logs" page.

## Quick start

No dependencies — Python 3.9+ stdlib only.

```bash
python3 app/server.py
```

Then open **http://127.0.0.1:8787** (or your machine's LAN IP, it binds
`0.0.0.0`). On first visit the UI asks for a passcode.

### Passcode / auth

- On first start the server generates a token and stores it in
  `data/.token` (chmod 600). Read it yourself:
  `cat data/.token`
- Override with env var: `DAILY_LOG_TOKEN=... python3 app/server.py`
- Every `/api/*` route (except `/api/health`) requires the header
  `Authorization: Bearer <token>`. The UI stores it in localStorage after
  you enter it once.

## Data

Entries are plain JSON files, one per day, under `data/`:
`data/2026-09-03.json`. This directory is **gitignored** — it holds your
passcode and personal entries and never leaves this machine.

## API

All routes require `Authorization: Bearer <passcode>` except `/api/health`.

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness |
| GET | `/api/entries?day=YYYY-MM-DD` | Entries for a day |
| POST | `/api/entries` | Add entry. Body: `{"title": "...", "time": "HH:MM" or ISO, "category": "...", "notes": "...", "source": "manual"}` |
| DELETE | `/api/entries?day=YYYY-MM-DD&id=<id>` | Delete entry |
| GET | `/api/tags` | List available tags |
| POST | `/api/tags` | Replace the tag list: `{"tags": ["dev", "work", ...]}` (1–30, lowercase `[a-z0-9-]`) |
| POST | `/api/refresh?day=YYYY-MM-DD` | Re-run the analysis for that day (background; 409 if already running) |
| GET | `/api/suggestions?day=YYYY-MM-DD` | `{status: idle\|running\|done, data: {day, generated_at, suggestions[], errors}}` |

Tags are user-editable in the UI (Tags button) and persist to `data/tags.json`
(default `general, work, dev, meeting, health, personal`). Entries keep whatever
label they were saved with.

### Analysis (`↻ Analysis` button / `collect.py`)

`collect.py` re-checks **all five sources** for the selected day (Notion tasks,
Google Calendar, Hermes sessions, GitHub issues, Productivity tracker) with the
06:00 Europe/Lisbon day boundary, and proposes bullets above the log — you tap
"add to log" or "dismiss". It never writes to any source, and never touches
entries you already logged. It runs only on the Hermes host: it reads
credentials from the profile `.env` and `state.db` on this machine. Standalone:
`python3 app/collect.py --day 2026-09-03`.

## Run as a systemd user service (Linux)

```bash
# 1. copy the unit, adjust the path if your checkout is elsewhere
cp deploy/daily-log-manager.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now daily-log-manager.service

# 2. make it survive reboots/logout
loginctl enable-linger $USER
```

Manage: `systemctl --user status|restart|stop daily-log-manager.service`

## Reverse proxy

Plain HTTP SPA, no websockets — any reverse proxy works. Point the upstream
at the VM, e.g. `daily.lmsg.pt` → `http://<vm-lan-ip>:8787`. No special
headers needed.

## Structure

```
app/
  server.py     # stdlib HTTP server: UI + JSON API + bearer auth
  index.html    # mobile-first single-page UI (passcode gate, tags, suggestions)
  collect.py    # 5-source day collector (stdlib + gh CLI)
data/           # gitignored: .token (passcode), tags.json, YYYY-MM-DD.json per day
deploy/
  daily-log-manager.service   # systemd user unit template
prompts/
  productivity-agent-api-prompt.md  # spec for the Productivity app's agent API
AGENTS.md       # full pipeline spec (sources, Notion IDs, conventions)
```
