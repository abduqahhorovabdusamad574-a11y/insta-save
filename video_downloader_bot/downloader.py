"""
Instagram / YouTube / TikTok videolarini yt-dlp va FFmpeg yordamida
yuklab oluvchi asosiy modul.

Asosiy vazifalari:
  1. `download_video_async(url)` — bot uchun async kirish nuqtasi
     (yuklash asyncio.to_thread ichida, ya'ni bot bloklanmaydi).
  2. Telegram'ning 50 MB limitiga mos formatni tanlash — avval yuqori
     sifat, agar hajm limitdan oshsa sifatni pasaytirib qayta urinish.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import uuid

from pathlib import Path
import sys
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

_env_path = BASE_DIR / ".env"
if _env_path.exists():
    load_dotenv(dotenv_path=_env_path)
else:
    load_dotenv()

import yt_dlp

from utils.helpers import is_valid_url

logger = logging.getLogger(__name__)

# ---- Sozlamalar ----------------------------------------------------------
DOWNLOAD_DIR = os.getenv("DOWNLOAD_DIR", str(BASE_DIR / "downloads"))
COOKIES_FILE = os.getenv("COOKIES_FILE") or str(BASE_DIR / "utils" / "cookies.txt")

try:
    MAX_FILE_SIZE = int(os.getenv("MAX_FILE_SIZE", "52428800"))  # 50 MB
except ValueError:
    MAX_FILE_SIZE = 50 * 1024 * 1024

# Birin-ketin sinab ko'riladigan formatlar (yuqori sifatdan pastga tomon).
# Eslatma: yt-dlp'da `best`/`worst` faqat video+audio BIR faylda bo'lgan
# formatlarni tanlaydi — bunday format yo'q bo'lgan saytlar (masalan YouTube)
# uchun `worstvideo+worstaudio` zaxirasi qo'shilgan.
CANDIDATE_FORMATS = (
    "bestvideo[height<=1080]+bestaudio/best[height<=1080]/best",
    "bestvideo[height<=720]+bestaudio/best[height<=720]/best",
    "bestvideo[height<=480]+bestaudio/best[height<=480]/best",
    "worstvideo+worstaudio/best",
)


def _has_ffmpeg() -> bool:
    """FFmpeg mavjudligini tekshiradi (env orqali yo'l ham qo'llab-quvvatlanadi)."""
    custom = os.getenv("FFMPEG_PATH")
    if custom:
        return os.path.exists(custom)
    return shutil.which("ffmpeg") is not None


def _candidate_formats() -> tuple:
    """FFmpeg bo'lsa — birlashtiriladigan formatlar, yo'qsa — bitta faylli formatlar."""
    if _has_ffmpeg():
        return CANDIDATE_FORMATS
    logger.warning(
        "FFmpeg topilmadi: bitta faylli (progressive) formatlar ishlatiladi. "
        "Birikkan sifatli video kerak bo'lsa FFmpeg o'rnating."
    )
    return ("best[ext=mp4]/best",)


def _init_cookies() -> None:
    """Agar COOKIES_CONTENT, INSTAGRAM_COOKIES yoki COOKIES_BASE64 muhit o'zgaruvchisi berilgan bo'lsa, uni COOKIES_FILE ga saqlaydi."""
    raw = os.getenv("COOKIES_CONTENT") or os.getenv("INSTAGRAM_COOKIES") or ""
    b64 = os.getenv("COOKIES_BASE64") or ""
    if b64.strip():
        import base64
        try:
            raw = base64.b64decode(b64.strip()).decode("utf-8", errors="replace")
        except Exception as e:
            logger.warning(f"COOKIES_BASE64 dekodlashda xatolik: {e}")
    if raw.strip():
        try:
            p = Path(COOKIES_FILE)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(raw.strip() + "\n", encoding="utf-8")
            logger.info("Cookies muhit o'zgaruvchisidan muvaffaqiyatli saqlandi.")
        except Exception as e:
            logger.warning(f"Cookies muhit o'zgaruvchisini saqlashda xatolik: {e}")

_init_cookies()


def _cookies_available() -> bool:
    """cookies.txt ichida haqiqiy cookie (kamida 6 ta TAB bilan) borligini tekshiradi."""
    _init_cookies()
    try:
        with open(COOKIES_FILE, encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                if line.count("\t") >= 6:
                    return True
    except OSError:
        return False
    return False


def _browser_cookies() -> tuple | None:
    """
    `COOKIES_FROM_BROWSER` sozlamasi orqali brauzer cookie'larini
    qo'llash (masalan `COOKIES_FROM_BROWSER=edge`).
    Eslatma: brauzer yopilgan bo'lishi kerak — aks holda baza qulflanadi.
    """
    name = (os.getenv("COOKIES_FROM_BROWSER") or "").strip().lower()
    return (name,) if name else None


def _base_options() -> dict:
    """yt-dlp uchun umumiy (barcha formatlar uchun bir xil) sozlamalar."""
    options = {
        "outtmpl": os.path.join(DOWNLOAD_DIR, "%(title).70s [%(id)s].%(ext)s"),
        "noplaylist": True,
        "merge_output_format": "mp4",
        "quiet": True,
        "no_warnings": True,
        "noprogress": True,
        "retries": 3,
        "fragment_retries": 3,
        "socket_timeout": 30,
        "http_headers": {
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
            "Accept-Language": "en-US,en;q=0.9",
        },
    }
    node_bin = shutil.which("node")
    if node_bin:
        options["js_runtimes"] = {"node": {}}
        options["remote_components"] = ["ejs:github"]
    ffmpeg = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg")
    if ffmpeg:
        options["ffmpeg_location"] = ffmpeg
    if _cookies_available():
        options["cookiefile"] = COOKIES_FILE
    browser = _browser_cookies()
    if browser:
        options["cookiesfrombrowser"] = browser
    return options


def _estimate_size(info: dict) -> int:
    """extract_info natijasidan taxminiy fayl hajmini (bayt) hisoblaydi."""
    if not info:
        return 0
    size = info.get("filesize") or info.get("filesize_approx") or 0
    if size:
        return int(size)

    total = 0
    known = False
    for fmt in info.get("requested_formats") or []:
        part = fmt.get("filesize") or fmt.get("filesize_approx") or 0
        if part:
            known = True
            total += int(part)
    return total if known else 0


def _approx_size(url: str, options: dict) -> int:
    """Yuklab olmasdan turib format hajmini taxmin qiladi (0 = noma'lum)."""
    probe = dict(options)
    probe["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(probe) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001 — probe muvaffaqiyatsiz bo'lsa, sinab ko'ramiz
        logger.debug(f"Hajmni aniqlab bo'lmadi: {e}")
        return 0
    return _estimate_size(info)


def _resolve_path(ydl: yt_dlp.YoutubeDL, info: dict):
    """Yuklangan faylning haqiqiy yo'lini topadi."""
    if not info:
        return None

    if info.get("_type") in ("playlist", "multi_video"):
        for entry in info.get("entries") or []:
            path = _resolve_path(ydl, entry)
            if path:
                return path
        return None

    for item in info.get("requested_downloads") or []:
        path = item.get("filepath")
        if path and os.path.exists(path):
            return path

    path = info.get("filepath")
    if path and os.path.exists(path):
        return path

    candidate = ydl.prepare_filename(info)
    if os.path.exists(candidate):
        return candidate

    # merge_output_format / FFmpegExtractAudio tufayli kengaytma o'zgargan bo'lishi mumkin
    root, _ = os.path.splitext(candidate)
    for ext in (".mp4", ".mkv", ".webm", ".mov", ".m4a", ".mp3", ".opus", ".ogg", ".wav"):
        path = root + ext
        if os.path.exists(path):
            return path
    return None


def _unique_path(path: str) -> str:
    """Fayl mavjud bo'lsa unga ` (1)`, ` (2)` ... qo'shib unikal yo'l qaytaradi."""
    if not os.path.exists(path):
        return path
    root, ext = os.path.splitext(path)
    n = 1
    while os.path.exists(f"{root} ({n}){ext}"):
        n += 1
    return f"{root} ({n}){ext}"


def _download_once(url: str, options: dict, workdir: str) -> str:
    """
    Bitta format bilan video yuklaydi va fayl yo'lini qaytaradi.

    Yuklash `workdir` (vaqtinchalik) papkada amalga oshiriladi — shu
    yerda yt-dlp yaratadigan oraliq fayllar (`.part`, `title.f137.mp4`
    va h.k.) muvaffaqiyatsiz urinishdan keyin ham qolib ketmaydi,
    chunki papka har doim o'chiriladi. Yakuniy fayl esa `DOWNLOAD_DIR`
    ga ko'chiriladi.
    """
    opts = dict(options)
    opts["outtmpl"] = os.path.join(workdir, "%(title).70s [%(id)s].%(ext)s")

    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(url, download=True)
        path = _resolve_path(ydl, info)
    if not path:
        raise RuntimeError("yt-dlp yuklangan faylni topa olmadi")

    final = _unique_path(os.path.join(DOWNLOAD_DIR, os.path.basename(path)))
    shutil.move(path, final)
    return final


def _is_auth_error(message: str) -> bool:
    """Xatolik cookie/login (autentifikatsiya) bilan bog'liqligini aniqlaydi."""
    msg = (message or "").lower()
    markers = (
        "login required",
        "please log in",
        "sign in to confirm",
        "cookies for the authentication",
        "empty media response",
        "rate-limit reached",
        "authentication",  # e.g. failed to decrypt with DPAPI
    )
    return any(m in msg for m in markers)


def _download_sync(url: str) -> str:
    """Video yuklaydi (bloklovchi). Async o'ramda `download_video_async` chaqiradi."""
    if not is_valid_url(url):
        raise ValueError("INVALID_URL")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    base = _base_options()

    too_large = False
    auth_error = False
    last_error: Exception | None = None

    for fmt in _candidate_formats():
        options = dict(base)
        options["format"] = fmt

        # 1-qadam: yuklamdan oldin taxminiy hajmni tekshiramiz
        approx = _approx_size(url, options)
        if approx and approx > MAX_FILE_SIZE:
            logger.info(f"Taxminiy hajm {approx} bayt > {MAX_FILE_SIZE} — keyingi format.")
            too_large = True
            continue

        # 2-qadam: haqiqiy yuklash (vaqtinchalik workdir ichida)
        workdir = os.path.join(DOWNLOAD_DIR, f".work_{uuid.uuid4().hex[:10]}")
        os.makedirs(workdir, exist_ok=True)
        try:
            file_path = _download_once(url, options, workdir)
        except Exception as e:  # noqa: BLE001 — keyingi format bilan davom etamiz
            logger.warning(f"Format bilan yuklash bo'lmadi ({fmt}): {e}")
            last_error = e
            if _is_auth_error(str(e)):
                auth_error = True
            continue
        finally:
            # Oraliq fayllar (.part, title.f137.mp4 va h.k.) — o'chiramiz
            shutil.rmtree(workdir, ignore_errors=True)

        size = os.path.getsize(file_path)
        if size > MAX_FILE_SIZE:
            logger.info(f"Fayl hajmi {size} bayt > {MAX_FILE_SIZE} — o'chiriladi.")
            try:
                os.remove(file_path)
            except OSError as e:
                logger.error(f"Katta faylni o'chirib bo'lmadi: {e}")
            too_large = True
            continue

        logger.info(f"Yuklandi: {file_path} ({size} bayt)")
        return file_path

    if too_large:
        raise ValueError("FILE_TOO_LARGE")
    if auth_error:
        # Instagram/YouTube cookie'siz bloklaydi — bot aniq ko'rsatma beradi
        raise ValueError("LOGIN_REQUIRED")
    if last_error is not None:
        raise last_error
    raise RuntimeError("Videoni yuklab bo'lmadi")


async def download_video_async(url: str) -> str:
    """Videoni yuklab, fayl yo'lini qaytaradi (botni bloklamaydi)."""
    return await asyncio.to_thread(_download_sync, url)


# ---- Audio (musiqa) ----------------------------------------------------
try:
    AUDIO_QUALITY = str(os.getenv("AUDIO_QUALITY", "192"))  # kbps
except Exception:  # noqa: BLE001 — noto'g'ri qiymat uchun
    AUDIO_QUALITY = "192"

# "To'liq qo'shiq" deb hisoblanadigan minimal/maksimal davomiylik (soniya)
FULL_TRACK_MIN = 60
FULL_TRACK_MAX = 20 * 60


def _search_audio_target(text: str) -> str:
    """
    YouTube qidiruvi (`ytsearch5:`) — natijalardan **to'liq** qo'shiqni tanlaydi:

      1) 45 soniyadan 20 daqiqagacha bo'lgan birinchi natija (klip/qo'shiq);
      2) bunday bo'lmasa — eng uzun natija (Shorts/preview emas);
      3) natija bo'lmasa — RuntimeError.

    Qidiruv faqat metadata olaydi (yuklamaydi), shuning uchun tez.
    """
    probe = dict(_base_options())
    probe.update({
        "noplaylist": False,
        "extract_flat": "in_playlist",  # faqat metadata, video yuklanmaydi
        "skip_download": True,
    })
    try:
        with yt_dlp.YoutubeDL(probe) as ydl:
            info = ydl.extract_info(f"ytsearch5:{text}", download=False)
    except Exception as e:  # noqa: BLE001
        raise RuntimeError("Qidiruv amalga oshmadi") from e

    entries = [e for e in (info.get("entries") or []) if e]
    if not entries:
        raise RuntimeError("Musiqa topilmadi")

    def dur(entry: dict) -> float:
        try:
            return float(entry.get("duration") or 0)
        except (TypeError, ValueError):
            return 0.0

    full = [e for e in entries if FULL_TRACK_MIN <= dur(e) <= FULL_TRACK_MAX]
    chosen = full[0] if full else max(entries, key=dur)

    url = chosen.get("url") or chosen.get("webpage_url")
    if not url and chosen.get("id"):
        url = f"https://www.youtube.com/watch?v={chosen['id']}"
    if not url:
        raise RuntimeError("Musiqa topilmadi")

    logger.info(
        f"Qidiruvda tanlandi: {chosen.get('title')} "
        f"({dur(chosen):.0f}s) — {len(entries)} natijadan"
    )
    return url


def _audio_request(text: str) -> str:
    """
    Kirish matni URL bo'lsa — o'sha URL, aks holda (qo'shiq nomi) —
    YouTube'dan **to'liq** trek qidirilib tanlanadi.
    """
    text = (text or "").strip()
    if is_valid_url(text):
        return text
    return _search_audio_target(text)


def _download_audio_sync(text: str) -> str:
    """
    Musiqa yuklaydi (mp3) va fayl yo'lini qaytaradi.

    * Kirish — URL (YouTube/Instagram/TikTok) bo'lsa o'sha media audiosi;
    * Kirish — matn (qo'shiq nomi) bo'lsa YouTube'dan qidirib topiladi.

    Hamma yuklash vaqtinchalik `.work_...` papkada bajariladi —
    oraliq fayllar qolmaydi. Hajm `MAX_FILE_SIZE` dan oshsa FILE_TOO_LARGE.
    """
    query = _audio_request(text)   # URL yoki "to'liq qo'shiq" qidiruv natijasi

    if not _has_ffmpeg():
        raise ValueError("FFMPEG_MISSING")

    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    base = _base_options()
    base["format"] = "bestaudio/best"
    base["postprocessors"] = [{
        "key": "FFmpegExtractAudio",
        "preferredcodec": "mp3",
        "preferredquality": str(AUDIO_QUALITY),
    }]

    workdir = os.path.join(DOWNLOAD_DIR, f".work_{uuid.uuid4().hex[:10]}")
    os.makedirs(workdir, exist_ok=True)
    try:
        options = dict(base)
        options["outtmpl"] = os.path.join(workdir, "%(title).70s [%(id)s].%(ext)s")
        path = None
        auth_error = False
        last_error = None

        for fmt in ("bestaudio/best", "best[height<=720]/best", "best"):
            options["format"] = fmt
            try:
                with yt_dlp.YoutubeDL(options) as ydl:
                    info = ydl.extract_info(query, download=True)
                    path = _resolve_path(ydl, info)
                if path and os.path.exists(path):
                    break
            except Exception as e:  # noqa: BLE001
                last_error = e
                if _is_auth_error(str(e)):
                    auth_error = True
                continue

        if not path or not os.path.exists(path):
            if auth_error:
                raise ValueError("LOGIN_REQUIRED") from last_error
            if last_error:
                raise last_error
            raise RuntimeError("yt-dlp yuklangan audiofaylni topa olmadi")

        size = os.path.getsize(path)
        if size > MAX_FILE_SIZE:
            raise ValueError("FILE_TOO_LARGE")

        final = _unique_path(os.path.join(DOWNLOAD_DIR, os.path.basename(path)))
        shutil.move(path, final)
        logger.info(f"Audio yuklandi: {final} ({size} bayt)")
        return final
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


async def download_audio_async(text: str) -> str:
    """Musiqani yuklab (mp3), fayl yo'lini qaytaradi — botni bloklamaydi."""
    return await asyncio.to_thread(_download_audio_sync, text)


def search_music_tracks_sync(query: str, limit: int = 5) -> list[dict]:
    """
    YouTube qidiruvi orqali bir nechta (limit ta) to'liq musiqalarni topadi.
    Shorts, tizer va juda qisqa videolarni chetlab o'tib, to'liq treklarni tanlaydi.
    """
    probe = dict(_base_options())
    probe.update({
        "extract_flat": "in_playlist",
        "skip_download": True,
    })
    search_limit = max(limit * 3, 15)
    try:
        with yt_dlp.YoutubeDL(probe) as ydl:
            info = ydl.extract_info(f"ytsearch{search_limit}:{query}", download=False)
    except Exception as e:  # noqa: BLE001
        logger.warning(f"Qidiruv xatoligi ({query}): {e}")
        return []

    entries = [e for e in (info.get("entries") or []) if e]
    results = []
    for entry in entries:
        try:
            dur = float(entry.get("duration") or 0)
        except (TypeError, ValueError):
            dur = 0.0

        # Juda qisqa (Shorts / parcha < 60s) yoki juda uzun (podkast/efir > 30 min) larni o'tkazib yuboramiz
        if dur > 0 and (dur < 60 or dur > 30 * 60):
            continue

        raw_title = (entry.get("title") or "").strip()
        if not raw_title:
            continue

        lower_title = raw_title.lower()
        # Shorts va tizerlarni aniq chetlatish
        if any(bad in lower_title for bad in ("#shorts", "#short", "teaser", "treyler", "promo", "anons")):
            continue

        vid_id = entry.get("id") or ""
        url = entry.get("url") or entry.get("webpage_url") or (f"https://www.youtube.com/watch?v={vid_id}" if vid_id else "")
        if not url:
            continue

        m, s = divmod(int(dur), 60)
        dur_str = f"{m:02d}:{s:02d}" if dur > 0 else "--:--"

        results.append({
            "id": vid_id,
            "title": raw_title,
            "duration": int(dur),
            "duration_str": dur_str,
            "uploader": (entry.get("uploader") or entry.get("channel") or "").strip(),
            "url": url,
        })
        if len(results) >= limit:
            break

    # Agar natija topilmasa va qidiruvda "audio" so'zi bo'lmasa, qayta urinib ko'ramiz
    if not results and not query.lower().endswith("audio"):
        return search_music_tracks_sync(f"{query} audio", limit=limit)

    return results


async def search_music_tracks_async(query: str, limit: int = 5) -> list[dict]:
    """Musiqalarni qidirish (async)."""
    return await asyncio.to_thread(search_music_tracks_sync, query, limit)


# ---- Video tavsifi (description) ----------------------------------------
def _extract_meta_sync(url: str) -> dict:
    """Videoning metama'lumotini (sarlavha, tavsif, muallif) yuklamadan oladi."""
    if not is_valid_url(url):
        raise ValueError("INVALID_URL")

    options = _base_options()
    options["skip_download"] = True
    try:
        with yt_dlp.YoutubeDL(options) as ydl:
            info = ydl.extract_info(url, download=False)
    except Exception as e:  # noqa: BLE001
        if _is_auth_error(str(e)):
            raise ValueError("LOGIN_REQUIRED") from e
        raise RuntimeError("Metama'lumotni olib bo'lmadi") from e

    # Playlist/stream turida ma'lumot kirishda bo'lishi mumkin
    if info and info.get("_type") in ("playlist", "multi_video"):
        entries = [e for e in (info.get("entries") or []) if e]
        info = entries[0] if entries else info

    info = info or {}
    return {
        "title": (info.get("title") or "").strip(),
        "description": (info.get("description") or "").strip(),
        "uploader": (info.get("uploader") or info.get("channel") or "").strip(),
        "duration": info.get("duration") or 0,
        "webpage_url": info.get("webpage_url") or url,
    }


async def extract_description_async(url: str) -> dict:
    """Video tavsifini (YouTube description) botni bloklamaydi oladi."""
    return await asyncio.to_thread(_extract_meta_sync, url)
