# Daily Log Manager

Mobile-first web UI for logging daily events, with event suggestions from collected sources.

## Structure
- `app/` — the web app (served locally, no build step)
  - `index.html` — single-page UI (mobile-first)
  - `server.py` — stdlib-only Python server; serves the UI, entries, tags, suggestions
  - `collect.py` — collects a day from all 5 sources (stdlib + `gh` CLI only); run by the
    server's refresh worker, or standalone: `python3 app/collect.py --day YYYY-MM-DD`
- `data/` — persisted JSON entries (one file per day: `YYYY-MM-DD.json`), plus `.token`
  (passcode) and `tags.json` (user tag list) — all gitignored
- `prompts/` — build prompts handed to other LLMs (e.g. the Productivity agent API)

## Conventions
- Python: stdlib only (host python3 has no pip; do not add dependencies).
- UI: single page, thumb-friendly, works on a phone screen width; no external CDNs (offline-safe).
- Data model (per entry): `{id, time (ISO local), title, category, notes, source}`.
  - `source` = `manual` (typed in the UI) or `suggested` (added from a collected source).
- Server: dedicated port **8787**, bound on the Hermes VM for LAN reachability
  (`http://192.168.13.60:8787`); the user reverse-proxies it to a domain (NPM upstream).
  Plain-HTTP upstream behind TLS termination is fine; no websockets.
- Auth (built-in since 2026-09-03): every request needs `Authorization: Bearer <token>`.
  Token auto-generated on first start at `data/.token` (or override with `DAILY_LOG_TOKEN`
  env var). The UI shows a passcode gate on load and stores the token only in browser
  localStorage. This is a second layer UNDER any proxy-level basic auth.
- Tags are user-editable in the UI (Tags button): stored in `data/tags.json`, served by
  `GET/POST /api/tags` (same bearer auth), default `general, work, dev, meeting, health,
  personal`. Entries keep whatever label they were saved with.
- "↻ Analysis" button (`POST /api/refresh?day=...`) runs `collect.py` in a background thread
  and stores the result as pending suggestions for that day (`GET /api/suggestions?day=...`
  → `{status: idle|running|done, data}`). UI renders suggestions above the log with
  "add to log" / "dismiss"; approving writes a `source: "suggested"` entry. Manual entries
  are never touched by a refresh. Collect reads credentials from the profile .env and
  `state.db` on this VM — it only works on the Hermes host.
- Persistent: systemd user service `daily-log-manager.service` (`deploy/`), enabled +
  lingering (survives logout/reboot), `Restart=on-failure`.
- Notion sync is handled by the Personal Manager agent, not by this app. The app owns local capture only.

## Run
```bash
python3 app/server.py            # http://192.168.13.60:8787
```

## Day boundary (important — applies to everything in this project)
- The user's "day" starts at **06:00 Europe/Lisbon** (setting `DAY_CUTOFF`, default `06:00`),
  NOT midnight. The user sleeps 03:00–04:00 and never wakes before 06:00.
- Activity before 06:00 belongs to the **previous** day — for collected sources AND manual notes.
- Log day D covers local time D 06:00 → D+1 06:00.

## Daily log pipeline (Personal Manager agent)
Each evening at **20:00 Europe/Lisbon** (cron), reconstruct what the user did in the day that
started at the 06:00 cutoff and write a simple, deduplicated bullet list into the Notion
"Daily Text Logs" page. The web UI lets the user approve/reject suggested bullets and add manual
notes for today or any recent day (manual notes attributed using the same cutoff).

### Sources
1. **Notion "Main Tasks and Events"** — database page id `512925f89f1e4c38a183177d1db1efe1`;
   data source id `72a1d9b324844748aeb3b2054501e176`. API `Notion-Version: 2025-09-03`:
   query via `POST /v1/data_sources/{data_source_id}/query`. **VERIFIED read 2026-09-03.**
   Tasks carry the due date, or span multiple days.
   Completed = status in {`Done`, `Halted Recurring (Temp)`, `Halted Recurring (Perm)`, `SubDivided`}
   (SubDivided = replaced by 2+ sub-tasks). Status `Recurring Task` = user manually drags
   it to its next occurrence (e.g. "Amazon FBA Start of Week Tasks" = Sun+Mon).
   Tasks also carry difficulty and priority → use for warnings ("high-priority task today", "hard task").
   Access: dedicated Notion integration (token in profile `.env` as NOTION_API_KEY;
   both the database page and the Daily Text Logs page are shared with it).
2. **Google Calendar** — only the "Ric | Personal" calendar. Calendar ID =
   `ricardopinto2k@gmail.com` (the owner's email). Service account:
   `hermes-daily-log@hermes-personal-logs-agent.iam.gserviceaccount.com`, key JSON at
   `/home/hermes/credentials/productivity-gcal.json` (chmod 600), scope `calendar.readonly`.
   **VERIFIED 2026-09-03**: token exchange works and events read back.
   Pitfall: `GET /users/me/calendarList` can lag for minutes/hours after a share —
   do NOT trust it; probe events directly with `GET /calendars/{calendar_id}/events`.
3. **Hermes session history** — all profiles if accessible. Summarize at a high level
   ("updated hermes agent a bit"), not deep detail, unless something notable happened.
4. **GitHub** — count only issues *opened* by ricas00 (creation date = the day's work;
   do NOT count when ric-loop later picks the issue up). General overview only.
5. **Productivity tracker** — https://productivity.lmsg.pt, repo `ricas00/Productivity-Tracker-V2.0`.
   Collect via the dedicated read-only agent API under `/api/v1/log/` (header `X-Agent-Token`,
   token in profile `.env` as PRODUCTIVITY_AGENT_TOKEN). **VERIFIED working 2026-09-03**:
   `/api/v1/log/day?date=YYYY-MM-DD` → {date, weekday, total_minutes, target_minutes,
   target_met, session_count, top_area, by_area[], sessions[]}; `/api/v1/log/areas` → area list.
   (Old endpoints like /api/health 404 — the backend moved.)
   Use `cutoff=06:00` for day attribution where supported.
   Build prompt: `prompts/productivity-agent-api-prompt.md`.

### Write target
Notion page "Daily Text Logs" (page id `2fde73e140d34e7fa5c3540e93d5b1da`):
year child pages ("2026") → month child pages ("August 2026") → one toggle list item per day,
containing that day's text. Write **simple bullet points** — no rambling, no duplication
across sources.

### Rules
- Timezone for all day boundaries: **Europe/Lisbon**; day starts at `DAY_CUTOFF` (06:00).
- Be honest about sourced vs inferred; never invent activity the sources don't show.
- Verify Notion writes by reading the page back before reporting success.
- Keep project-specific details here; general facts go to profile memory.
