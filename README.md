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

| Method | Path | Description |
|---|---|---|
| GET | `/api/health` | Liveness (no auth) |
| GET | `/api/entries?day=YYYY-MM-DD` | Entries for a day (auth) |
| POST | `/api/entries` | Add entry (auth). Body: `{"title": "...", "time": "HH:MM" or ISO, "category": "...", "notes": "...", "source": "manual"}` |
| DELETE | `/api/entries?day=YYYY-MM-DD&id=<id>` | Delete entry (auth) |

Categories: `work`, `personal`, `health`, `learning`, `admin`, `general`.

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
  index.html    # mobile-first single-page UI
data/           # gitignored: .token (passcode) + YYYY-MM-DD.json per day
deploy/
  daily-log-manager.service   # systemd user unit template
prompts/
  productivity-agent-api-prompt.md  # spec for the Productivity app's agent API
AGENTS.md       # full pipeline spec (sources, Notion IDs, conventions)
```
