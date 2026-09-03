#!/usr/bin/env python3
"""Daily Log Manager server.

Stdlib-only local server: serves the mobile UI and persists daily entries as
JSON files under data/ (one file per day: YYYY-MM-DD.json). Binds 0.0.0.0 so
it is reachable from the LAN / reverse proxy; every API route (except
/api/health) requires the shared passcode (Authorization: Bearer <token>).

Token resolution: DAILY_LOG_TOKEN env var, else data/.token file
(generated on first start, chmod 600). The passcode is entered once in the
UI and stored in the browser's localStorage.
"""

import hmac
import json
import os
import re
import secrets
import threading
from datetime import date, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = ROOT / "data"
UI_DIR = Path(__file__).resolve().parent
PORT = 8787
MAX_BODY = 256 * 1024

_lock = threading.Lock()
_day_re = re.compile(r"^\d{4}-\d{2}-\d{2}$")
_CATEGORY_RE = re.compile(r"^[\w -]{1,40}$")


def _resolve_token() -> str:
    env = os.environ.get("DAILY_LOG_TOKEN", "").strip()
    if env:
        return env
    f = DATA_DIR / ".token"
    if f.exists():
        tok = f.read_text(encoding="utf-8").strip()
        if tok:
            return tok
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tok = secrets.token_urlsafe(18)
    f.write_text(tok, encoding="utf-8")
    os.chmod(f, 0o600)
    return tok


TOKEN = _resolve_token()


def _load_day(day: str) -> list:
    f = DATA_DIR / f"{day}.json"
    if not f.exists():
        return []
    try:
        entries = json.loads(f.read_text(encoding="utf-8"))
        return entries if isinstance(entries, list) else []
    except (json.JSONDecodeError, OSError):
        return []


def _save_day(day: str, entries: list) -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    tmp = DATA_DIR / f"{day}.json.tmp"
    tmp.write_text(json.dumps(entries, indent=2, ensure_ascii=False), encoding="utf-8")
    tmp.replace(DATA_DIR / f"{day}.json")


def _validate_entry(raw: dict) -> tuple[dict, str]:
    """Return (clean_entry, error)."""
    if not isinstance(raw, dict):
        return {}, "body must be a JSON object"
    title = str(raw.get("title", "")).strip()
    if not title or len(title) > 300:
        return {}, "title required (1-300 chars)"
    category = str(raw.get("category", "general")).strip() or "general"
    if not _CATEGORY_RE.match(category):
        return {}, "invalid category"
    notes = str(raw.get("notes", "")).strip()[:2000]
    source = raw.get("source", "manual")
    if source not in ("manual", "suggested"):
        return {}, "source must be 'manual' or 'suggested'"
    ts_raw = str(raw.get("time", "")).strip()
    try:
        ts = datetime.fromisoformat(ts_raw) if ts_raw else datetime.now()
    except ValueError:
        return {}, "time must be ISO 8601"
    entry = {
        "id": secrets.token_hex(6),
        "time": ts.isoformat(timespec="seconds"),
        "title": title,
        "category": category.lower(),
        "notes": notes,
        "source": source,
    }
    return entry, ""


class Handler(BaseHTTPRequestHandler):
    server_version = "DailyLogManager/1.0"

    def log_message(self, fmt, *args):  # keep stdout quiet
        pass

    # --- helpers -----------------------------------------------------------
    def _send_json(self, code: int, obj) -> None:
        body = json.dumps(obj, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_file(self, path: Path) -> None:
        if not path.exists() or not path.is_file():
            self._send_json(404, {"error": "not found"})
            return
        data = path.read_bytes()
        ctype = "text/html; charset=utf-8" if path.suffix == ".html" else "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return False
        return hmac.compare_digest(auth[7:].strip(), TOKEN)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return None, "body too large"
        try:
            return json.loads(self.rfile.read(length).decode("utf-8")), ""
        except (json.JSONDecodeError, UnicodeDecodeError):
            return None, "invalid JSON"

    def _qs(self) -> dict:
        return {k: v[0] for k, v in parse_qs(urlsplit(self.path).query).items()}

    # --- routes ------------------------------------------------------------
    def do_GET(self):
        path = urlsplit(self.path).path
        if path in ("/", "/index.html"):
            self._send_file(UI_DIR / "index.html")
        elif path == "/api/health":
            self._send_json(200, {"ok": True, "date": date.today().isoformat()})
        elif path == "/api/entries":
            if not self._authorized():
                self._send_json(401, {"error": "unauthorized"})
                return
            q = self._qs()
            day = q.get("day", date.today().isoformat())
            if not _day_re.match(day):
                self._send_json(400, {"error": "day must be YYYY-MM-DD"})
                return
            with _lock:
                entries = _load_day(day)
            entries.sort(key=lambda e: e.get("time", ""))
            self._send_json(200, {"day": day, "entries": entries})
        else:
            self._send_json(404, {"error": "not found"})

    def do_POST(self):
        path = self.path.split("?", 1)[0]
        if path != "/api/entries":
            self._send_json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        raw, err = self._read_json_body()
        if err:
            self._send_json(400, {"error": err})
            return
        entry, err = _validate_entry(raw)
        if err:
            self._send_json(400, {"error": err})
            return
        day = entry["time"][:10]
        with _lock:
            entries = _load_day(day)
            entries.append(entry)
            _save_day(day, entries)
        self._send_json(201, {"entry": entry})

    def do_DELETE(self):
        path = urlsplit(self.path).path
        if path != "/api/entries":
            self._send_json(404, {"error": "not found"})
            return
        if not self._authorized():
            self._send_json(401, {"error": "unauthorized"})
            return
        q = self._qs()
        day = q.get("day", "").strip()
        entry_id = q.get("id", "").strip()
        if not _day_re.match(day) or not entry_id:
            self._send_json(400, {"error": "day and id required"})
            return
        with _lock:
            entries = _load_day(day)
            remaining = [e for e in entries if e.get("id") != entry_id]
            if len(remaining) == len(entries):
                self._send_json(404, {"error": "entry not found"})
                return
            _save_day(day, remaining)
        self._send_json(200, {"ok": True, "id": entry_id})


def main():
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("0.0.0.0", PORT), Handler)
    print(f"Daily Log Manager: bound 0.0.0.0:{PORT} (token file: {DATA_DIR / '.token'})")
    server.serve_forever()


if __name__ == "__main__":
    main()
