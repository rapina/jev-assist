"""Local decision traces, human review, and the authenticated browser dashboard."""
from contextlib import contextmanager
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
import json
import io
import zipfile
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import threading
import time
from urllib.parse import parse_qs, urlsplit
import uuid

from local_runtime import STATE, open_file, private, authorized, local_secret
from routing_policy import TIERS

DB_PATH = Path(STATE) / "jev-audit.sqlite3"
ORIGIN = "http://127.0.0.1:4319"
_lock = threading.RLock()
_logins = {}
_sessions = {}
storage_error = None
CLIENT_FILES = (
    "server/configure-client.mjs", "server/observer.mjs", "server/local-client.mjs", "server/install-local-service.ps1",
    "router/src/toml-structure.mjs", "router/src/file-security.mjs",
    "vendor/canny/package.json",
    "vendor/canny/LICENSE", "vendor/canny/UPSTREAM.md",
    *(f"vendor/canny/dist/{name}.js" for name in ("events", "hook", "config", "checks", "ledger", "jev", "rules")),
)


def client_bundle():
    root = Path(__file__).resolve().parent.parent
    catalog = json.loads((Path(STATE) / "merged-models.json").read_text(encoding="utf-8"))
    models = [dict(m, visibility="list") for m in catalog["models"] if m.get("slug") in ("jev/auto", *TIERS)]
    if not any(m["slug"] == "jev/auto" for m in models):
        raise ValueError("Jev model catalog unavailable")
    data = io.BytesIO()
    with zipfile.ZipFile(data, "w", zipfile.ZIP_DEFLATED) as archive:
        for name in CLIENT_FILES:
            archive.write(root / name, name)
        archive.writestr("models.json", json.dumps({"models": models}))
    return data.getvalue()
_secret_keys = re.compile(r"^(authorization|cookie|password|secret|api[_-]?key|access_token|refresh_token|id_token)$", re.I)
_secret_text = re.compile(r"(?i)(?:bearer\s+|apikey_|sk-)[a-z0-9._-]{8,}|eyJ[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+\.[a-zA-Z0-9_-]+")


def timestamp():
    return datetime.now(timezone.utc).isoformat(timespec="microseconds")


def clean(value):
    """Keep the bounded Jev projection, never auth headers or canonical replay."""
    if isinstance(value, dict):
        return {str(k): "[REDACTED]" if _secret_keys.fullmatch(str(k)) else clean(v)
                for k, v in value.items()}
    if isinstance(value, list):
        return [clean(v) for v in value]
    if isinstance(value, str):
        return _secret_text.sub("[REDACTED]", value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


@contextmanager
def database():
    # ponytail: one process-wide lock; use a writer queue if audit throughput matters.
    with _lock:
        path = str(DB_PATH)
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)
        # Recheck each time, including when storage was deleted or replaced.
        # PERSIST keeps its rollback journal with these same private permissions.
        for name in (path, path + "-journal"):
            fd = open_file(name, os.O_RDWR | os.O_CREAT)
            try:
                if not private(fd):
                    raise PermissionError("Audit storage must belong to the current user")
            finally:
                os.close(fd)
        connection = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=rw", uri=True, timeout=2)
        connection.row_factory = sqlite3.Row
        try:
            connection.execute("PRAGMA journal_mode=PERSIST")
            connection.execute("PRAGMA synchronous=FULL")
            connection.execute("PRAGMA temp_store=MEMORY")
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("CREATE TABLE IF NOT EXISTS records (id TEXT PRIMARY KEY, started TEXT NOT NULL, updated TEXT NOT NULL, phase TEXT NOT NULL, body TEXT NOT NULL, review TEXT)")
            connection.execute("CREATE TABLE IF NOT EXISTS reviews (seq INTEGER PRIMARY KEY, record_id TEXT NOT NULL REFERENCES records(id) ON DELETE CASCADE, body TEXT NOT NULL)")
            connection.execute("CREATE TABLE IF NOT EXISTS observations (id TEXT PRIMARY KEY, updated INTEGER NOT NULL, received TEXT NOT NULL, body TEXT NOT NULL)")
            with connection:
                yield connection
        finally:
            connection.close()


def record(record_id, phase, **fields):
    """Observability failures never change the model request's result."""
    global storage_error
    record_id = record_id or str(uuid.uuid4())
    try:
        fields = public_record(fields)
        encoded = json.dumps(fields, ensure_ascii=False)
        if len(encoded.encode("utf-8")) > 256 * 1024:
            fields = {"capture_error": "event exceeded 256 KiB"}
        with database() as db:
            existing = db.execute("SELECT body,started FROM records WHERE id=?", (record_id,)).fetchone()
            body = json.loads(existing["body"]) if existing else {"events": []}
            now = timestamp()
            started = existing["started"] if existing else now
            body.update(fields)
            body["events"].append({"phase": phase, "at": now})
            body["events"] = body["events"][-20:]
            db.execute("INSERT INTO records(id,started,updated,phase,body) VALUES(?,?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,phase=excluded.phase,body=excluded.body",
                       (record_id, started, now, phase, json.dumps(body, ensure_ascii=False)))
            if phase in ("completed", "failed", "disconnected"):
                # Reviewed examples survive; unreviewed history is bounded.
                db.execute("DELETE FROM records WHERE review IS NULL AND id IN (SELECT id FROM records WHERE review IS NULL ORDER BY started DESC LIMIT -1 OFFSET 10000)")
        storage_error = None
    except (OSError, sqlite3.Error, ValueError, TypeError) as error:
        storage_error = type(error).__name__
    return record_id


def _document(row):
    if row is None:
        return None
    body = public_record(json.loads(row["body"]))
    selected = body.get("selected") or {}
    decision = body.get("decision") or {}
    fixed = selected.get("source") in ("manual", "client_model")
    return {**body, "id": row["id"], "started": row["started"],
            "updated": row["updated"], "phase": row["phase"], "difficulty": "fixed" if fixed else "unknown",
            "dimensions": None if fixed else decision.get("dimensions")}


def public_record(body):
    # Apply on both writes and reads so historical prompt captures never reach clients.
    return clean({k: v for k, v in body.items() if k in (
        "events", "step", "policy_version", "policy_hash", "decision", "selected",
        "outcome", "error", "capture_error")})


def get_record(record_id):
    with database() as db:
        return _document(db.execute("SELECT * FROM records WHERE id=?", (record_id,)).fetchone())


def list_records(query):
    clauses, parameters = [], []
    mode_sql = "CASE WHEN json_extract(body,'$.selected.source') IN ('manual','client_model') THEN 'fixed' WHEN json_extract(body,'$.decision.model') IS NOT NULL THEN 'auto' ELSE 'other' END"
    mode = query.get("mode", [""])[0]
    if mode in ("auto", "fixed"):
        clauses.append(mode_sql + "=?")
        parameters.append(mode)
    for field, expression in (("model", "COALESCE(json_extract(body,'$.outcome.model'),json_extract(body,'$.selected.model'))"),):
        value = query.get(field, [""])[0]
        if value:
            clauses.append(expression + "=?")
            parameters.append(value)
    limit = min(max(int(query.get("limit", [100])[0]), 1), 500)
    where = " WHERE " + " AND ".join(clauses) if clauses else ""
    with database() as db:
        rows = db.execute("SELECT * FROM records" + where + " ORDER BY started DESC LIMIT ?", (*parameters, limit)).fetchall()
        stats = dict(db.execute("SELECT count(*) AS total,coalesce(sum(phase='completed'),0) AS completed,coalesce(sum(phase IN ('failed','disconnected')),0) AS failed FROM records").fetchone())
        distribution = [dict(r) for r in db.execute("SELECT COALESCE(json_extract(body,'$.outcome.model'),json_extract(body,'$.selected.model')) AS model," + mode_sql + " AS mode,count(*) AS count FROM records WHERE phase='completed' GROUP BY model,mode")]
    summaries = []
    for row in rows:
        item = _document(row)
        outcome, selected = item.get("outcome", {}), item.get("selected", {})
        summaries.append({**{k: item.get(k) for k in ("id", "started", "updated", "phase", "difficulty", "dimensions")},
                          "model": outcome.get("model", selected.get("model")),
                          "effort": outcome.get("effort", selected.get("effort")),
                          "gate": outcome.get("gate", selected.get("gate")),
                          "jev_ms": outcome.get("jev_ms"), "total_ms": outcome.get("total_ms"),
                          "http": outcome.get("status"), "completed": item["phase"] == "completed"})
    return {"records": summaries, "stats": stats, "distribution": distribution, "storage_error": storage_error}


def observe(body):
    if (body.get("mode") != "observe" or not isinstance(body.get("id"), str)
            or not re.fullmatch(r"[a-f0-9]{64}", body["id"])
            or type(body.get("updated")) is not int or body["updated"] < 0
            or type(body.get("eventCount")) is not int or body["eventCount"] < 0
            or type(body.get("verified")) is not bool
            or body.get("verdict") not in ("allow", "block", "deny", "ask", "note", "warn")
            or any(not isinstance(body.get(k), str) or len(body[k]) > 160 for k in ("session", "project", "client"))
            or not isinstance(body.get("files"), list) or len(body["files"]) > 100
            or any(not isinstance(f, str) or len(f) > 1024 for f in body["files"])
            or not isinstance(body.get("events"), list) or len(body["events"]) > 30
            or any(not isinstance(e, dict) for e in body["events"])):
        raise ValueError("Invalid observation")
    value = clean({k: body[k] for k in ("id", "session", "project", "client", "mode", "updated", "eventCount", "files", "verified", "verdict", "events")})
    with database() as db:
        db.execute("INSERT INTO observations VALUES(?,?,?,?) ON CONFLICT(id) DO UPDATE SET updated=excluded.updated,received=excluded.received,body=excluded.body WHERE excluded.updated >= observations.updated",
                   (body["id"], body["updated"], timestamp(), json.dumps(value, ensure_ascii=False)))
        db.execute("DELETE FROM observations WHERE id IN (SELECT id FROM observations ORDER BY updated DESC LIMIT -1 OFFSET 1000)")
    return {"ok": True}


def observations():
    with database() as db:
        rows = db.execute("SELECT body,received FROM observations ORDER BY updated DESC LIMIT 100").fetchall()
    return {"sessions": [{**json.loads(r["body"]), "received": r["received"]} for r in rows]}


def new_login():
    with _lock:
        now = time.monotonic()
        for table in (_logins, _sessions):
            for key, expiry in list(table.items()):
                if expiry <= now:
                    del table[key]
        if len(_logins) >= 32 or len(_sessions) >= 32:
            raise ValueError("Too many dashboard sessions")
        code = secrets.token_urlsafe(32)
        _logins[code] = now + 60
    return {"url": ORIGIN + "/dashboard/login?code=" + code}


def _headers(handler, code, kind, data, extra=()):
    handler.send_response(code)
    for name, value in (("Content-Type", kind), ("Content-Length", str(len(data))),
                        ("Cache-Control", "no-store"), ("Referrer-Policy", "no-referrer"),
                        ("X-Content-Type-Options", "nosniff"),
                        ("Content-Security-Policy", "default-src 'self'; script-src 'self'; style-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"), *extra):
        handler.send_header(name, value)
    handler.end_headers()
    handler.wfile.write(data)


def _json(handler, code, value):
    _headers(handler, code, "application/json; charset=utf-8", json.dumps(value, ensure_ascii=False).encode())


def handle(handler):
    """Dashboard routes only. /dashboard/session uses the existing bearer guard."""
    parts = urlsplit(handler.path)
    path = parts.path.rstrip("/")
    if not path.startswith("/dashboard") or path == "/dashboard/session":
        return False
    if (handler.headers.get("Host") != "127.0.0.1:4319"
            or handler.headers.get("Origin") not in (None, ORIGIN)
            or handler.headers.get("Sec-Fetch-Site") == "cross-site"):
        _json(handler, 403, {"error": "Same-origin dashboard access required"})
        return True
    query = parse_qs(parts.query)
    try:
        if handler.command == "GET" and path == "/dashboard/login":
            with _lock:
                expires = _logins.pop(query.get("code", [""])[0], 0)
                if expires <= time.monotonic():
                    _json(handler, 401, {"error": "Dashboard login expired"})
                    return True
                session = secrets.token_urlsafe(32)
                _sessions[session] = time.monotonic() + 12 * 3600
            _headers(handler, 303, "text/plain", b"", (("Location", "/dashboard"),
                     ("Set-Cookie", f"jev_dashboard={session}; HttpOnly; SameSite=Strict; Path=/dashboard; Max-Age=43200")))
            return True
        if handler.command == "GET" and path == "/dashboard/client.zip":
            _headers(handler, 200, "application/zip", client_bundle())
            return True
        assets = {"/dashboard": ("dashboard.html", "text/html; charset=utf-8"),
                  "/dashboard/install-client.ps1": ("install-client.ps1", "text/plain; charset=utf-8"),
                  "/dashboard/uninstall-client.ps1": ("uninstall-client.ps1", "text/plain; charset=utf-8"),
                  "/dashboard/dashboard.js": ("dashboard.js", "text/javascript; charset=utf-8"),
                  "/dashboard/dashboard.css": ("dashboard.css", "text/css; charset=utf-8")}
        if handler.command == "GET" and path in assets:
            name, kind = assets[path]
            _headers(handler, 200, kind, Path(__file__).with_name(name).read_bytes())
            return True
        cookie = SimpleCookie(handler.headers.get("Cookie", ""))
        session = cookie.get("jev_dashboard")
        with _lock:
            valid = (authorized(handler.headers.get("Authorization"), local_secret())
                     or bool(session and _sessions.get(session.value, 0) > time.monotonic()))
        if not valid:
            _json(handler, 401, {"error": "Run jev-assist dashboard to sign in"})
        elif handler.command == "GET" and path == "/dashboard/api/observations":
            _json(handler, 200, observations())
        elif handler.command == "GET" and path == "/dashboard/api/records":
            _json(handler, 200, list_records(query))
        elif handler.command == "GET" and path == "/dashboard/api/record":
            item = get_record(query.get("id", [""])[0])
            _json(handler, 200 if item else 404, item or {"error": "Record not found"})
        else:
            _json(handler, 404, {"error": "Dashboard endpoint not found"})
    except (ValueError, CookieError):
        _json(handler, 400, {"error": "Invalid dashboard request"})
    except LookupError:
        _json(handler, 404, {"error": "Record not found"})
    except (OSError, sqlite3.Error):
        _json(handler, 503, {"error": "Dashboard storage unavailable"})
    return True
