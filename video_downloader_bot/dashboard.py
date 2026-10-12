"""
Bot statistikasi uchun web-dashboard (aiohttp — qo'shimcha kutubxona kerak emas).

Ikki xil ishga tushirish:
  1) Bot bilan birga (main.py avtomatik boshlaydi) — http://127.0.0.1:8000
  2) Alohida:  python dashboard.py --port 8000

Sozlamalar (.env):
  DASHBOARD_HOST     — 127.0.0.1 (standart, faqat shu kompyuter)
  DASHBOARD_PORT     — 8000
  DASHBOARD_PASSWORD — bo'sh bo'lsa auth yoqilmaydi; to'ldirilsa — parol talab qilinadi
  DASHBOARD_ENABLED  — 0 bo'lsa bot dashboard'ni boshlamaydi
"""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import hmac
import logging
import os
import sys
import time
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

from aiohttp import web

import analytics

STATIC_DIR = BASE_DIR / "static"

HOST = os.getenv("DASHBOARD_HOST") or os.getenv("HOST") or "0.0.0.0"
try:
    PORT = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT") or "8000")
except ValueError:
    PORT = 8000

_bot_info: dict = {"username": None, "id": None}
_started_at = time.time()


def set_bot_info(username: str | None, bot_id: int | None) -> None:
    """Dashboard sarlavhasida ko'rsatiladigan bot ma'lumotlari."""
    _bot_info["username"] = username
    _bot_info["id"] = bot_id


def _password() -> str:
    return (os.getenv("DASHBOARD_PASSWORD") or "").strip()


def _expected_token() -> str:
    """Paroldan hosil qilinadigan token (parol o'zi saqlanmaydi)."""
    return hashlib.sha256(("dashboard:" + _password()).encode("utf-8")).hexdigest()


# ---- Middleware (auth) ----------------------------------------------------
@web.middleware
async def _auth_middleware(request: web.Request, handler):
    path = request.path
    # /health, /api/me ochiq (login ekrani va health check uchun), /api/login parolni tekshiradi,
    # static va index fayllarga ham auth talab qilinmaydi.
    public = path in ("/", "/health", "/api/login", "/api/me") or path.startswith("/static/")
    if not _password() or public:
        return await handler(request)
    token = request.headers.get("X-Auth-Token", "") or request.query.get("token", "")
    if not hmac.compare_digest(token, _expected_token()):
        return web.json_response({"error": "unauthorized"}, status=401)
    return await handler(request)


# ---- API handlerlar -------------------------------------------------------
async def _me(request: web.Request) -> web.Response:
    from downloader import get_cookie_status
    return web.json_response({
        "auth_required": bool(_password()),
        "bot": _bot_info,
        "server_time": int(time.time()),
        "uptime": int(time.time() - _started_at),
        "db": analytics.db_info(),
        "cookies": get_cookie_status(),
    })


async def _login(request: web.Request) -> web.Response:
    try:
        body = await request.json()
    except Exception:  # noqa: BLE001
        body = {}
    password = str(body.get("password") or "")
    if not _password() or hmac.compare_digest(password, _password()):
        return web.json_response({"token": _expected_token()})
    await asyncio.sleep(0.4)  # parolni tez tanlashga urinishga qarshi kichik kechikish
    return web.json_response({"error": "invalid_password"}, status=401)


async def _summary(request: web.Request) -> web.Response:
    return web.json_response(analytics.summary())


async def _timeseries(request: web.Request) -> web.Response:
    days = request.query.get("days", "14")
    try:
        days = int(days)
    except ValueError:
        days = 14
    return web.json_response({"days": days, "points": analytics.timeseries(days)})


async def _hourly(request: web.Request) -> web.Response:
    hours = request.query.get("hours", "24")
    try:
        hours = int(hours)
    except ValueError:
        hours = 24
    return web.json_response({"points": analytics.hourly(hours)})


async def _actions(request: web.Request) -> web.Response:
    days = request.query.get("days", "7")
    try:
        days = int(days)
    except ValueError:
        days = 7
    return web.json_response({"items": analytics.actions(days)})


async def _events(request: web.Request) -> web.Response:
    limit = request.query.get("limit", "20")
    try:
        limit = int(limit)
    except ValueError:
        limit = 20
    return web.json_response({"items": analytics.recent_events(limit)})


async def _users(request: web.Request) -> web.Response:
    q = request.query.get("search", "")
    try:
        page = int(request.query.get("page", "1"))
    except ValueError:
        page = 1
    try:
        per_page = int(request.query.get("per_page", "15"))
    except ValueError:
        per_page = 15
    sort = request.query.get("sort", "last_seen")
    return web.json_response(
        analytics.list_users(search=q, page=page, per_page=per_page, sort=sort)
    )


async def _export(request: web.Request) -> web.Response:
    csv_text = analytics.export_users_csv()
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return web.Response(
        text=csv_text,
        content_type="text/csv",
        headers={
            "Content-Disposition": f'attachment; filename="users-{stamp}.csv"',
        },
    )


async def _health(request: web.Request) -> web.Response:
    """Render va UptimeRobot kabi monitoring vositalari uchun health check endpoint."""
    return web.Response(text="OK", status=200)


async def _index(request: web.Request) -> web.Response:
    index = STATIC_DIR / "index.html"
    if not index.exists():
        return web.Response(text="Bot Dashboard is running", status=200)
    return web.FileResponse(index)


# ---- App ------------------------------------------------------------------
def build_app() -> web.Application:
    app = web.Application(middlewares=[_auth_middleware])
    app.router.add_get("/", _index)
    app.router.add_route("*", "/health", _health)
    app.router.add_get("/api/me", _me)
    app.router.add_post("/api/login", _login)
    app.router.add_get("/api/summary", _summary)
    app.router.add_get("/api/timeseries", _timeseries)
    app.router.add_get("/api/hourly", _hourly)
    app.router.add_get("/api/actions", _actions)
    app.router.add_get("/api/events", _events)
    app.router.add_get("/api/users", _users)
    app.router.add_get("/api/export.csv", _export)
    app.router.add_static("/static/", STATIC_DIR)
    return app


async def start_dashboard(host: str | None = None, port: int | None = None) -> web.AppRunner:
    """Dashboard saytini ishga tushiradi (bot jarayoni ichida asinxron ishlaydi)."""
    h = host or os.getenv("DASHBOARD_HOST") or os.getenv("HOST") or "0.0.0.0"
    if port is None:
        try:
            p = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT") or "8000")
        except ValueError:
            p = 8000
    else:
        p = port

    runner = web.AppRunner(build_app(), access_log=None)
    await runner.setup()
    site = web.TCPSite(runner, h, p)
    await site.start()
    auth_note = "parol bilan" if _password() else "ochiq (parol yo'q)"
    logging.info("Dashboard serveri muvaffaqiyatli ishga tushdi: http://%s:%s (%s)", h, p, auth_note)
    return runner


def run_standalone(host: str | None = None, port: int | None = None) -> None:
    """Faqat dashboard'ni alohida ishga tushiradi (botdan ajratib)."""
    h = host or os.getenv("DASHBOARD_HOST") or os.getenv("HOST") or "0.0.0.0"
    if port is None:
        try:
            p = int(os.getenv("PORT") or os.getenv("DASHBOARD_PORT") or "8000")
        except ValueError:
            p = 8000
    else:
        p = port

    # Windows konsoli (cp1251/cp1252) emoji'ni yubora olmaydi — UTF-8 ga o'tamiz
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:  # noqa: BLE001
            pass

    async def _main():
        runner = await start_dashboard(h, p)
        print(f"\n🚀 Dashboard: http://{h}:{p}")
        if _password():
            print("   (parol talab qilinadi)")
        else:
            print("   (parol yo'q — DASHBOARD_PASSWORD qo'shsangiz, kirish paroli bo'ladi)")
        try:
            while True:
                await asyncio.sleep(3600)
        finally:
            await runner.cleanup()

    try:
        asyncio.run(_main())
    except KeyboardInterrupt:
        print("\nDashboard to'xtatildi.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Bot dashboard (alohida rejim)")
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    args = parser.parse_args()
    run_standalone(args.host, args.port)
