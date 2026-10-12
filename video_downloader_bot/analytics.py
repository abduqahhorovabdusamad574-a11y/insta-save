"""
Bot statistikasi: foydalanuvchilar va hodisalar (SQLite, stdlib).

Foydalanuvchi botga kirsa (/start, oddiy xabar, tugma bosishi) — `track()`
uni shu yerga yozadi. Dashboard shu bazani o'qib, ko'rsatkamlarni chiqaradi.

Jadvallar:
  users  — foydalanuvchi profili + hisoblagichlar (qachon kirgani,
           necha xabar, necha yuklash va h.k.)
  events — vaqt bo'yicha hodisalar (grafiklar shundan quriladi)

Baza WAL rejimida — bot (yozuvchi) va dashboard (o'quvchi) bir vaqtda ishlaydi,
hatto alohida jarayonlarda bo'lsa ham (`ANALYTICS_DB` bir xil bo'lsa).
"""

from __future__ import annotations

import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
DB_PATH = os.getenv("ANALYTICS_DB", str(BASE_DIR / "analytics.db"))

# `users` jadvalidagi hisoblagich ustunlari — xavfsizlik uchun faqat shu nomlar
COUNTERS = {
    "message": "messages",
    "callback": "callbacks",
    "start": "starts",
    "video": "videos",
    "music": "musics",
    "desc": "descs",
}

_lock = threading.Lock()
_conn: sqlite3.Connection | None = None


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=15, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def get_conn() -> sqlite3.Connection:
    global _conn
    if _conn is None:
        os.makedirs(os.path.dirname(os.path.abspath(DB_PATH)), exist_ok=True)
        _conn = _connect()
        _init_schema(_conn)
    return _conn


def _init_schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS users (
            user_id       INTEGER PRIMARY KEY,
            username      TEXT,
            first_name    TEXT,
            last_name     TEXT,
            language_code TEXT,
            is_premium    INTEGER DEFAULT 0,
            first_seen    INTEGER NOT NULL,
            last_seen     INTEGER NOT NULL,
            messages      INTEGER DEFAULT 0,
            callbacks     INTEGER DEFAULT 0,
            starts        INTEGER DEFAULT 0,
            videos        INTEGER DEFAULT 0,
            musics        INTEGER DEFAULT 0,
            descs         INTEGER DEFAULT 0
        );
        CREATE INDEX IF NOT EXISTS idx_users_first_seen ON users(first_seen);
        CREATE INDEX IF NOT EXISTS idx_users_last_seen  ON users(last_seen);

        CREATE TABLE IF NOT EXISTS events (
            id      INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL,
            type    TEXT    NOT NULL,
            ok      INTEGER NOT NULL DEFAULT 1,
            ts      INTEGER NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_events_ts   ON events(ts);
        CREATE INDEX IF NOT EXISTS idx_events_user ON events(user_id, ts);
        CREATE INDEX IF NOT EXISTS idx_events_type ON events(type, ts);
        """
    )
    conn.commit()


# ---- Yozuv ----------------------------------------------------------------
def track(user, action: str, ok: int = 1) -> None:
    """
    Bitta hodisani yozadi: foydalanuvchini yangilaydi (oxirgi faollik),
    hisoblagichni oshiradi va `events` jadvaliga yozuv qo'shadi.

    Hech qachon xabar tashlamaydi — statistika uchun bot uzilmasligi kerak.
    `action`: message | callback | start | video | music | desc
    `ok`:     1 = muvaffaqiyatli, 0 = xatolik (yuklab bo'lmadi va h.k.)
    """
    field = COUNTERS.get(action)
    if field is None:
        return
    try:
        uid = int(user.id)
        now = int(time.time())
        username = user.username
        first_name = user.first_name
        last_name = user.last_name
        lang = getattr(user, "language_code", None)
        premium = 1 if getattr(user, "is_premium", False) else 0

        with _lock, get_conn() as conn:
            conn.execute(
                """
                INSERT INTO users (user_id, username, first_name, last_name,
                                   language_code, is_premium, first_seen, last_seen)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(user_id) DO UPDATE SET
                    username      = COALESCE(excluded.username, users.username),
                    first_name    = COALESCE(excluded.first_name, users.first_name),
                    last_name     = COALESCE(excluded.last_name, users.last_name),
                    language_code = COALESCE(excluded.language_code, users.language_code),
                    is_premium    = MAX(users.is_premium, excluded.is_premium),
                    last_seen     = MAX(users.last_seen, excluded.last_seen)
                """,
                (uid, username, first_name, last_name, lang, premium, now, now),
            )
            conn.execute(
                f"UPDATE users SET {field} = {field} + 1 WHERE user_id = ?", (uid,)
            )
            conn.execute(
                "INSERT INTO events (user_id, type, ok, ts) VALUES (?, ?, ?, ?)",
                (uid, action, 1 if ok else 0, now),
            )
    except Exception:  # noqa: BLE001 — statistika bot ishini to'xtatmasin
        logging.exception("Analytics yozuvda xatolik (%s)", action)


# ---- Vaqt yordamchilari ---------------------------------------------------
def _day_start(ts: float) -> int:
    dt = datetime.fromtimestamp(ts)
    return int(datetime(dt.year, dt.month, dt.day).timestamp())


def _fmt_day(ts: float) -> str:
    return datetime.fromtimestamp(ts).strftime("%Y-%m-%d")


# ---- So'rovlar ------------------------------------------------------------
def summary() -> dict:
    """Dashbord uchun asosiy ko'rsatkichlar (KPI kartalar)."""
    now = time.time()
    today = _day_start(now)
    yesterday = today - 86400
    week = today - 7 * 86400
    month = today - 30 * 86400

    with _lock, get_conn() as conn:
        one = lambda sql, *p: conn.execute(sql, p).fetchone()[0]  # noqa: E731

        total_users = one("SELECT COUNT(*) FROM users")
        new_today = one("SELECT COUNT(*) FROM users WHERE first_seen >= ?", today)
        new_yesterday = one(
            "SELECT COUNT(*) FROM users WHERE first_seen >= ? AND first_seen < ?",
            yesterday, today,
        )
        new_7d = one("SELECT COUNT(*) FROM users WHERE first_seen >= ?", week)
        new_30d = one("SELECT COUNT(*) FROM users WHERE first_seen >= ?", month)
        active_today = one("SELECT COUNT(*) FROM users WHERE last_seen >= ?", today)
        active_7d = one("SELECT COUNT(*) FROM users WHERE last_seen >= ?", week)
        online = one("SELECT COUNT(*) FROM users WHERE last_seen >= ?", now - 300)

        messages_total = one("SELECT COALESCE(SUM(messages), 0) FROM users")
        messages_today = one(
            "SELECT COUNT(*) FROM events WHERE type = 'message' AND ts >= ?", today
        )
        downloads_total = one(
            "SELECT COUNT(*) FROM events WHERE type IN ('video', 'music') AND ok = 1"
        )
        downloads_today = one(
            "SELECT COUNT(*) FROM events "
            "WHERE type IN ('video', 'music') AND ok = 1 AND ts >= ?",
            today,
        )
        videos_total = one("SELECT COALESCE(SUM(videos), 0) FROM users")
        musics_total = one("SELECT COALESCE(SUM(musics), 0) FROM users")
        descs_total = one("SELECT COALESCE(SUM(descs), 0) FROM users")
        starts_total = one("SELECT COALESCE(SUM(starts), 0) FROM users")
        starts_today = one(
            "SELECT COUNT(*) FROM events WHERE type = 'start' AND ts >= ?", today
        )

        ok = one(
            "SELECT COUNT(*) FROM events "
            "WHERE type IN ('video', 'music', 'desc') AND ok = 1"
        )
        bad = one(
            "SELECT COUNT(*) FROM events "
            "WHERE type IN ('video', 'music', 'desc') AND ok = 0"
        )
        attempts = ok + bad

    growth = None
    if new_yesterday:
        growth = round((new_today - new_yesterday) / new_yesterday * 100, 1)

    return {
        "total_users": total_users,
        "new_today": new_today,
        "new_yesterday": new_yesterday,
        "new_7d": new_7d,
        "new_30d": new_30d,
        "active_today": active_today,
        "active_7d": active_7d,
        "online": online,
        "messages_total": messages_total,
        "messages_today": messages_today,
        "downloads_total": downloads_total,
        "downloads_today": downloads_today,
        "videos_total": videos_total,
        "musics_total": musics_total,
        "descs_total": descs_total,
        "starts_total": starts_total,
        "starts_today": starts_today,
        "success_rate": round(ok / attempts * 100, 1) if attempts else None,
        "growth_pct": growth,
        "generated_at": int(now),
    }


def timeseries(days: int = 14) -> list[dict]:
    """Kunlik qator: yangi foydalanuvchilar, faol foydalanuvchilar, hodisalar."""
    days = max(1, min(int(days), 365))
    today = datetime.fromtimestamp(time.time())
    today = datetime(today.year, today.month, today.day)
    start = int((today - timedelta(days=days - 1)).timestamp())

    with _lock, get_conn() as conn:
        new_rows = conn.execute(
            "SELECT date(first_seen, 'unixepoch', 'localtime') AS d, COUNT(*) AS c "
            "FROM users WHERE first_seen >= ? GROUP BY d",
            (start,),
        ).fetchall()
        active_rows = conn.execute(
            "SELECT date(ts, 'unixepoch', 'localtime') AS d, "
            "COUNT(DISTINCT user_id) AS c "
            "FROM events WHERE ts >= ? GROUP BY d",
            (start,),
        ).fetchall()
        event_rows = conn.execute(
            "SELECT date(ts, 'unixepoch', 'localtime') AS d, COUNT(*) AS c, "
            "SUM(CASE WHEN type IN ('video', 'music') AND ok = 1 "
            "         THEN 1 ELSE 0 END) AS dl "
            "FROM events WHERE ts >= ? GROUP BY d",
            (start,),
        ).fetchall()

    new_map = {r["d"]: r["c"] for r in new_rows}
    active_map = {r["d"]: r["c"] for r in active_rows}
    event_map = {r["d"]: (r["c"], r["dl"] or 0) for r in event_rows}

    out = []
    for i in range(days):
        day = today - timedelta(days=days - 1 - i)
        key = day.strftime("%Y-%m-%d")
        events, downloads = event_map.get(key, (0, 0))
        out.append({
            "date": key,
            "label": day.strftime("%d.%m"),
            "new_users": new_map.get(key, 0),
            "active_users": active_map.get(key, 0),
            "events": events,
            "downloads": downloads,
        })
    return out


def hourly(hours: int = 24) -> list[dict]:
    """Soatlik qator (soat boshidan boshlab, `hours` ta soat)."""
    hours = max(1, min(int(hours), 168))
    now = time.time()
    start = int(now // 3600) * 3600 - (hours - 1) * 3600

    with _lock, get_conn() as conn:
        rows = conn.execute(
            "SELECT ts, user_id FROM events WHERE ts >= ?", (start,)
        ).fetchall()

    buckets: dict[int, dict] = {}
    for h in range(hours):
        ts = start + h * 3600
        dt = datetime.fromtimestamp(ts)
        buckets[ts] = {"label": dt.strftime("%H:00"), "events": 0, "users": set()}
    for r in rows:
        key = int(r["ts"] // 3600) * 3600
        b = buckets.get(key)
        if b is None:
            continue
        b["events"] += 1
        b["users"].add(r["user_id"])

    return [
        {"label": b["label"], "events": b["events"], "users": len(b["users"])}
        for b in buckets.values()
    ]


def actions(days: int = 7) -> list[dict]:
    """Amallar taqsimoti (video / musiqa / tavsif / start) — oxirgi N kun."""
    days = max(1, min(int(days), 365))
    start = int(time.time()) - days * 86400
    with _lock, get_conn() as conn:
        rows = conn.execute(
            "SELECT type, COUNT(*) AS c, SUM(ok) AS ok "
            "FROM events WHERE type IN ('video', 'music', 'desc', 'start') "
            "AND ts >= ? GROUP BY type",
            (start,),
        ).fetchall()
    return [
        {"type": r["type"], "count": r["c"], "ok": r["ok"] or 0}
        for r in sorted(rows, key=lambda r: -r["c"])
    ]


def recent_events(limit: int = 20) -> list[dict]:
    """Jonli hodisalar lentasi (dashboard'ning o'ng paneli)."""
    limit = max(1, min(int(limit), 100))
    with _lock, get_conn() as conn:
        rows = conn.execute(
            "SELECT e.ts, e.type, e.ok, e.user_id, u.username, u.first_name "
            "FROM events e LEFT JOIN users u ON u.user_id = e.user_id "
            "ORDER BY e.id DESC LIMIT ?",
            (limit,),
        ).fetchall()
    return [dict(r) for r in rows]


def list_users(
    search: str = "",
    page: int = 1,
    per_page: int = 15,
    sort: str = "last_seen",
) -> dict:
    """Foydalanuvchilar ro'yxati: qidiruv, saralash, sahifalash."""
    page = max(1, int(page))
    per_page = max(1, min(int(per_page), 100))
    sort_cols = {
        "last_seen": "last_seen DESC",
        "first_seen": "first_seen DESC",
        "messages": "messages DESC",
        "downloads": "(videos + musics) DESC",
    }
    order = sort_cols.get(sort, sort_cols["last_seen"])

    where, params = "", []
    q = (search or "").strip()
    if q:
        like = f"%{q}%"
        where = (
            "WHERE username LIKE ? OR first_name LIKE ? OR last_name LIKE ? "
            "OR CAST(user_id AS TEXT) LIKE ?"
        )
        params = [like, like, like, like]

    with _lock, get_conn() as conn:
        total = conn.execute(f"SELECT COUNT(*) FROM users {where}", params).fetchone()[0]
        rows = conn.execute(
            f"""
            SELECT user_id, username, first_name, last_name, language_code,
                   is_premium, first_seen, last_seen, messages, callbacks,
                   starts, videos, musics, descs,
                   (videos + musics) AS downloads
            FROM users {where}
            ORDER BY {order}
            LIMIT ? OFFSET ?
            """,
            [*params, per_page, (page - 1) * per_page],
        ).fetchall()

    return {
        "items": [dict(r) for r in rows],
        "total": total,
        "page": page,
        "per_page": per_page,
        "pages": max(1, -(-total // per_page)),
    }


def export_users_csv() -> str:
    """Foydalanuvchilar ro'yxatini CSV ko'rinishida qaytaradi."""
    data = list_users(page=1, per_page=100000)
    head = (
        "user_id,username,first_name,last_name,first_seen,last_seen,"
        "messages,starts,videos,musics,descs,downloads"
    )
    lines = [head]
    for u in data["items"]:
        lines.append(
            ",".join(str(v) if v is not None else ""
                     for v in [
                         u["user_id"], u["username"], u["first_name"], u["last_name"],
                         datetime.fromtimestamp(u["first_seen"]).isoformat(sep=" "),
                         datetime.fromtimestamp(u["last_seen"]).isoformat(sep=" "),
                         u["messages"], u["starts"], u["videos"], u["musics"],
                         u["descs"], u["downloads"],
                     ])
        )
    return "\n".join(lines)


def db_info() -> dict:
    """Baza haqida ma'lumot (dashboard pastki qatorida ko'rsatiladi)."""
    size = 0
    try:
        size = os.path.getsize(DB_PATH)
    except OSError:
        pass
    return {"path": os.path.abspath(DB_PATH), "size": size}
