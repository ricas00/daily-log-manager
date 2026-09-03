#!/usr/bin/env python3
"""Collect a day's activity from all sources and emit JSON suggestions.

Stdlib + gh CLI only. Reads credentials from the Hermes profile .env
(never from args/CLI flags) and the Google service-account JSON file.

Usage:  python3 collect.py --day 2026-09-02
Output: JSON on stdout:
  {"day", "generated_at", "suggestions": [{id, time, title, category, notes, source}],
   "errors": {source: message}}

Day boundary: 06:00 Europe/Lisbon — day D covers [D 06:00, D+1 06:00).
"""

import argparse
import base64
import datetime as dt
import hashlib
import json
import os
import re
import sqlite3
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from zoneinfo import ZoneInfo

TZ = ZoneInfo("Europe/Lisbon")
ENV_FILE = "/home/hermes/.hermes/profiles/personal-manager/.env"
HERMES_STATE_DB = "/home/hermes/.hermes/profiles/personal-manager/state.db"
NOTION_DS_ID = "72a1d9b324844748aeb3b2054501e176"
GCALENDAR_ID = "ricardopinto2k@gmail.com"
PRODUCTIVITY_URL = "https://productivity.lmsg.pt/api/v1/log/day"
UA = {"User-Agent": "curl/8.5.0"}


def load_env() -> dict:
    env = {}
    with open(ENV_FILE) as f:
        for line in f:
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                env[k.strip()] = v.strip()
    return env


def http_get_json(url: str, headers: dict, timeout: int = 30):
    req = urllib.request.Request(url, headers={**UA, **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def http_post_json(url: str, body: dict, headers: dict, timeout: int = 30):
    req = urllib.request.Request(
        url, data=json.dumps(body).encode(),
        headers={**UA, "Content-Type": "application/json", **headers})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def b64url(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def suggest(suggestions: list, source: str, title: str, category: str,
            time: str = "12:00", notes: str = "") -> None:
    if not title:
        return
    norm = re.sub(r"\W+", "", title.lower())
    suggestions.append({
        "id": hashlib.sha1(f"{source}:{norm}".encode()).hexdigest()[:12],
        "time": time,
        "title": title[:300],
        "category": category,
        "notes": notes[:500],
        "source": source,
    })


# --- sources ----------------------------------------------------------------

def collect_notion(day: str, env: dict):
    out = []
    h = {"Authorization": f"Bearer {env['NOTION_API_KEY']}", "Notion-Version": "2025-09-03"}
    url = f"https://api.notion.com/v1/data_sources/{NOTION_DS_ID}/query"
    d0 = dt.date.fromisoformat(day)
    body = {"filter": {"property": "Date", "date": {"on_or_after": (d0 - dt.timedelta(days=1)).isoformat(),
                                                     "on_or_before": day}},
            "page_size": 100}
    q = http_post_json(url, body, h)
    for r in q.get("results", []):
        p = r.get("properties", {})
        name = "".join(t.get("plain_text", "") for t in p.get("Name", {}).get("title", []))
        d = p.get("Date", {}).get("date") or {}
        s, e = d.get("start"), d.get("end") or d.get("start")
        if not name or not s:
            continue
        # include tasks whose date range covers day D (single-date or multi-day span)
        if not (dt.date.fromisoformat(s) <= d0 <= dt.date.fromisoformat(e)):
            continue
        status = (p.get("Status", {}).get("status") or {}).get("name", "")
        tags = [t.get("name", "") for t in p.get("Tags", {}).get("multi_select", [])]
        notes = f"status: {status}" + (f"; tags: {', '.join(tags)}" if tags else "")
        if s != e:
            notes += f"; spans {s}→{e}"
        personal_kw = ("personal", "home", "family", "saude", "saúde", "férias", "ferias")
        cat = "personal" if any(k in t.lower() for t in tags for k in personal_kw) else "work"
        suggest(out, "notion", name, cat, notes=notes)
    return out


def google_token(env: dict) -> str:
    cred = json.load(open(env["GOOGLE_CREDENTIALS_FILE"]))
    open("/tmp/collect_key.pem", "w").write(cred["private_key"])
    os.chmod("/tmp/collect_key.pem", 0o600)
    try:
        now = int(time.time())
        header = b64url(json.dumps({"alg": "RS256", "typ": "JWT"}).encode())
        payload = b64url(json.dumps({
            "iss": cred["client_email"], "scope": "https://www.googleapis.com/auth/calendar.readonly",
            "aud": "https://oauth2.googleapis.com/token", "iat": now, "exp": now + 3599,
        }).encode())
        sig = subprocess.run(
            ["openssl", "dgst", "-sha256", "-sign", "/tmp/collect_key.pem"],
            input=f"{header}.{payload}".encode(),
            capture_output=True, check=True).stdout
        jwt = f"{header}.{payload}.{b64url(sig)}"
        data = urllib.parse.urlencode({
            "grant_type": "urn:ietf:params:oauth:grant-type:jwt-bearer", "assertion": jwt}).encode()
        req = urllib.request.Request("https://oauth2.googleapis.com/token", data=data,
                                     headers={**UA, "Content-Type": "application/x-www-form-urlencoded"})
        tok = json.loads(urllib.request.urlopen(req, timeout=30).read())
        return tok["access_token"]
    finally:
        os.unlink("/tmp/collect_key.pem")


def collect_calendar(day: str, env: dict):
    out = []
    tok = google_token(env)
    start = dt.datetime.fromisoformat(f"{day}T06:00:00").replace(tzinfo=TZ).isoformat()
    end = (dt.datetime.fromisoformat(f"{day}T06:00:00") + dt.timedelta(days=1)).replace(tzinfo=TZ).isoformat()
    url = ("https://www.googleapis.com/calendar/v3/calendars/"
           f"{urllib.parse.quote(GCALENDAR_ID)}/events?"
           + urllib.parse.urlencode({"timeMin": start, "timeMax": end, "singleEvents": "true",
                                     "orderBy": "startTime", "maxResults": "100"}))
    d = http_get_json(url, {"Authorization": f"Bearer {tok}"})
    for e in d.get("items", []):
        title = (e.get("summary") or "").strip()
        if not title:
            continue
        st = e.get("start", {})
        t = st.get("dateTime") or st.get("date") or ""
        hhmm = t[11:16] if len(t) >= 16 and t[10] == "T" else "12:00"
        loc = (e.get("location") or "").strip()
        suggest(out, "calendar", title, "personal", time=hhmm, notes=loc)
    return out


def collect_productivity(day: str, env: dict):
    out = []
    d = http_get_json(f"{PRODUCTIVITY_URL}?date={day}&cutoff=06:00",
                      {"X-Agent-Token": env["PRODUCTIVITY_AGENT_TOKEN"]})
    areas = ", ".join(f"{a['area']} {a['minutes']}m" for a in d.get("by_area", []))
    target = "target met" if d.get("target_met") else "target missed"
    suggest(out, "productivity",
            f"Productivity: {d.get('total_minutes', 0)} min tracked ({target}, top: {d.get('top_area', '?')})",
            "work", notes=areas)
    for s in d.get("sessions", []):
        note = (s.get("note") or "").strip()
        if note:
            suggest(out, "productivity", f"{s.get('area', '')}: {note}".strip(": "),
                    "work", notes=f"{s.get('minutes', 0)} min session")
    return out


def collect_github(day: str, env: dict):
    out = []
    gh = shutil.which("gh") or os.path.expanduser("~/.local/bin/gh")
    if not os.path.exists(gh):
        raise RuntimeError("gh CLI not found")
    d1 = (dt.datetime.fromisoformat(day) - dt.timedelta(days=1)).date().isoformat()
    d2 = (dt.datetime.fromisoformat(day) + dt.timedelta(days=1)).date().isoformat()
    r = subprocess.run(
        [gh, "api", f"search/issues?q=author:ricas00+type:issue+created:{d1}..{d2}&per_page=100"],
        capture_output=True, text=True)
    if r.returncode != 0:
        raise RuntimeError(f"gh api failed: {r.stderr.strip()[:200]}")
    start = dt.datetime.fromisoformat(f"{day}T06:00:00").replace(tzinfo=TZ)
    end = start + dt.timedelta(days=1)
    for it in json.loads(r.stdout).get("items", []):
        created = it.get("created_at", "")
        if not created:
            continue
        try:
            ts = dt.datetime.fromisoformat(created.replace("Z", "+00:00")).astimezone(TZ)
        except ValueError:
            continue
        if not (start <= ts < end):
            continue
        repo = (it.get("repository_url") or "").rsplit("/", 1)[-1]
        suggest(out, "github", f"Issue: {it.get('title', '')} ({repo})", "dev",
                time=ts.strftime("%H:%M"), notes=f"#{it.get('number', '')} opened")
    return out


def collect_hermes(day: str, env: dict):
    out = []
    start = dt.datetime.fromisoformat(f"{day}T06:00:00").replace(tzinfo=TZ).timestamp()
    end = start + 86400
    con = sqlite3.connect(f"file:{HERMES_STATE_DB}?mode=ro", uri=True)
    rows = con.execute(
        "SELECT session_id, timestamp, content FROM messages "
        "WHERE role='user' AND timestamp>=? AND timestamp<? ORDER BY timestamp",
        (start, end)).fetchall()
    con.close()
    seen, distinct = set(), []
    for _, ts, content in rows:
        if content in seen:
            continue
        seen.add(content)
        distinct.append((ts, content))
    sessions = {sid for sid, _, _ in rows}
    if not distinct:
        return out
    first = distinct[0][1].split("\n")[0][:80]
    suggest(out, "hermes",
            f"Hermes: {len(distinct)} messages across {len(sessions)} session(s)",
            "admin", notes=f"e.g. “{first}”")
    return out


def run(day: str) -> dict:
    """Collect all sources for a day. Returns {day, generated_at, suggestions, errors}."""
    env = load_env()
    suggestions: list = []
    errors: dict = {}
    for name, fn in (("notion", collect_notion), ("calendar", collect_calendar),
                     ("productivity", collect_productivity), ("github", collect_github),
                     ("hermes", collect_hermes)):
        try:
            suggestions.extend(fn(day, env))
        except Exception as e:  # noqa: BLE001 — one dead source must not kill the rest
            errors[name] = f"{type(e).__name__}: {str(e)[:200]}"
    return {"day": day, "generated_at": dt.datetime.now(TZ).isoformat(),
            "suggestions": suggestions, "errors": errors}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--day", required=True, help="YYYY-MM-DD (Europe/Lisbon day starting 06:00)")
    args = ap.parse_args()
    if not re.match(r"^\d{4}-\d{2}-\d{2}$", args.day):
        print(json.dumps({"error": "bad day"}))
        sys.exit(1)
    print(json.dumps(run(args.day), ensure_ascii=False))


if __name__ == "__main__":
    main()
