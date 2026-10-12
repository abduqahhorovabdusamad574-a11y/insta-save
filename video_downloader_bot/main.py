import os
import sys
import json
import html
import logging
import asyncio
import time
import hashlib
import uuid
from pathlib import Path
from contextlib import suppress
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
if str(BASE_DIR) not in sys.path:
    sys.path.insert(0, str(BASE_DIR))

# .env yuklash (agar mavjud bo'lsa)
env_path = BASE_DIR / ".env"
if env_path.exists():
    load_dotenv(dotenv_path=env_path)
else:
    load_dotenv()

from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from aiogram.exceptions import TelegramConflictError
from utils.helpers import is_valid_url, cleanup_file, cleanup_stale_files
from utils.stt import transcribe_audio_async, clean_music_query
from downloader import (
    download_video_async,
    download_audio_async,
    extract_description_async,
    search_music_tracks_async,
    get_cookie_status,
)
from analytics import track

BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN topilmadi! Render Environment sozlamalari yoki .env faylini tekshiring.")

bot = Bot(token=BOT_TOKEN)
dp = Dispatcher()


# ---- Statistika (dashboard uchun) -----------------------------------------
class AnalyticsMiddleware(BaseMiddleware):
    """
    Har bir kiruvchi update'ni analytics'ga yozadi:
    foydalanuvchi botga kirgani, xabar/tugma orqali faol bo'lgani.
    Xatolik statistikani buzmasligi uchun hamma narsa try/except ichida.
    """

    async def __call__(self, handler, event: types.Update, data: dict):
        try:
            user = None
            action = None
            msg = event.message or event.edited_message
            if msg is not None:
                user, action = msg.from_user, "message"
            elif event.callback_query is not None:
                user, action = event.callback_query.from_user, "callback"
            if user is not None and action and not user.is_bot:
                track(user, action)
        except Exception as e:  # noqa: BLE001 — statistika botni to'xtatmasin
            logging.warning("Analytics middleware xatosi: %s", e)
        return await handler(event, data)


dp.update.middleware(AnalyticsMiddleware())

# ---- Kutish animatsiyasi (soat ramkalari) -------------------------------
CLOCK_FRAMES = (
    "🕐", "🕑", "🕒", "🕓", "🕔", "🕕",
    "🕖", "🕗", "🕘", "🕙", "🕚", "🕛",
)
ANIMATION_INTERVAL = 1.2  # soniya (Telegram edit limitiga mos)


async def _animate_status(message: types.Message, action: str):
    """
    `message` xabarini har ~1.2 soniyada yangilab turadi:
        🕒 Video yuklanmoqda...
        ⏱ 5 soniya
    Agar edit taqiqlansa ("message is not modified", rate-limit) —
    xatolik e'tiborsiz tark etiladi, animatsiya davom etadi.
    """
    start = time.monotonic()
    i = 0
    while True:
        frame = CLOCK_FRAMES[i % len(CLOCK_FRAMES)]
        elapsed = int(time.monotonic() - start)
        text = f"{frame} {action}\n⏱ {elapsed} soniya o'tdi"
        try:
            await message.edit_text(text)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001 — "not modified" / rate-limit — e'tiborsiz
            pass
        i += 1
        try:
            await asyncio.sleep(ANIMATION_INTERVAL)
        except asyncio.CancelledError:
            raise


async def _start_animation(message: types.Message, action: str) -> asyncio.Task:
    """Animatsiya vazifasini ishga tushiradi va Task obyektini qaytaradi."""
    return asyncio.create_task(_animate_status(message, action))


async def _stop_animation(task: asyncio.Task | None):
    """Animatsiyani to'xtatadi (xatoliklar e'tiborsiz)."""
    if task is None:
        return
    task.cancel()
    with suppress(asyncio.CancelledError, Exception):
        await task


# ---- "Musiqasini olish" tugmasi uchun URL kesh --------------------------
# callback_data Telegram'da 64 baytdan oshmasligi shart, shuning uchun
# to'liq URL keshda saqlanadi, tugmaga esa qisqa kalit (16 belgi) kiradi.
# Kesh JSON faylga yoziladi — bot qayta ishga tushsa ham tugmalar ishlayveradi.
URL_CACHE_TTL = 6 * 3600  # 6 soat
URL_CACHE_FILE = os.getenv("URL_CACHE_FILE", str(BASE_DIR / ".music_cache.json"))
_url_cache: dict[str, tuple[str, float]] = {}


def _load_cache() -> dict:
    """Diskdagi keshni o'qiydi (yaroqsiz bo'lsa — bo'sh lug'at)."""
    try:
        with open(URL_CACHE_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, ValueError):
        return {}
    if not isinstance(data, dict):
        return {}
    now = time.time()
    clean = {}
    for key, value in data.items():
        if isinstance(value, (list, tuple)) and len(value) == 2:
            try:
                url, ts = str(value[0]), float(value[1])
            except (TypeError, ValueError):
                continue
            if now - ts <= URL_CACHE_TTL:
                clean[str(key)] = (url, ts)
    return clean


def _save_cache() -> None:
    """Keshni diska yozadi (xatolik — faqat logga)."""
    try:
        with open(URL_CACHE_FILE, "w", encoding="utf-8") as fh:
            json.dump({k: [v[0], v[1]] for k, v in _url_cache.items()}, fh, ensure_ascii=False)
    except OSError as e:
        logging.warning(f"URL keshni saqlab bo'lmadi: {e}")


_url_cache.update(_load_cache())


def _cache_url(url: str) -> str:
    """URL'ni keshga saqlaydi va qisqa kalitni qaytaradi."""
    key = hashlib.sha1(url.encode("utf-8")).hexdigest()[:16]
    _url_cache[key] = (url, time.time())
    now = time.time()
    for old_key, (_, ts) in list(_url_cache.items()):
        if now - ts > URL_CACHE_TTL:
            _url_cache.pop(old_key, None)
    _save_cache()
    return key


def _video_markup(url: str) -> types.InlineKeyboardMarkup:
    """Video ostidagi ikkita tugma: 🎵 musiqa va 📜 tavsif."""
    key = _cache_url(url)
    return types.InlineKeyboardMarkup(inline_keyboard=[[
        types.InlineKeyboardButton(text="🎵 Musiqasini olish", callback_data=f"music:{key}"),
        types.InlineKeyboardButton(text="📜 Tavsif", callback_data=f"desc:{key}"),
    ]])


def _chunk_text(text: str, limit: int = 4000) -> list[str]:
    """Uzun matnni Telegram chegarasiga (4096) mos bo'laklarga bo'ladi."""
    parts: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind("\n", 0, limit)
        if cut < limit // 2:
            cut = limit
        parts.append(rest[:cut])
        rest = rest[cut:].lstrip("\n")
    if rest:
        parts.append(rest)
    return parts

@dp.message(CommandStart())
async def start_cmd(message: types.Message):
    track(message.from_user, "start")
    await message.answer(
        "👋 <b>Xush kelibsiz!</b>\n\n"
        "🎬 <b>Video yuklash:</b>\n"
        "Instagram, TikTok yoki YouTube havolasini yuboring — "
        "men uni eng yuqori sifatda yuklab beraman.\n\n"
        "🎵 <b>Musiqa qidirish va yuklash:</b>\n"
        "• Shunchaki qo'shiq yoki ijrochi nomini yozib yuboring (masalan: <i>Dilni og'ritma</i>);\n"
        "• Yoki 🎙 <b>ovozli xabar</b> yuboring (qo'shiq nomini aytsangiz, bot o'zi tushunib topadi!);\n"
        "• Yoki <i>/music Dilni og'ritma</i> buyrug'idan foydalaning.\n\n"
        "📜 <b>Tavsif:</b> video ostidagi 📜 Tavsif tugmasi video muallifining "
        "izohini (description) ko'rsatadi.\n\n"
        "⏳ Yuklash davomida soat animatsiyasi ko'rinib turadi.",
        parse_mode="HTML",
    )


async def handle_music_search(
    message: types.Message,
    query: str,
    status_msg: types.Message | None = None,
    header_prefix: str = "",
):
    """
    Qo'shiq nomi bo'yicha YouTube'dan qidirib, top-5 ta natijani
    tugmalar ([1], [2], ...) bilan chiqaradi.
    """
    cleaned_query = clean_music_query(query) or query
    search_target = cleaned_query if len(cleaned_query) >= 3 else query

    if status_msg is None:
        status_msg = await message.answer(
            f"🔍 <i>«{html.escape(search_target)}» qidirilmoqda...</i>",
            parse_mode="HTML",
        )
    else:
        try:
            await status_msg.edit_text(
                f"{header_prefix}🔍 <i>«{html.escape(search_target)}» qidirilmoqda...</i>",
                parse_mode="HTML",
            )
        except Exception:
            pass

    try:
        tracks = await search_music_tracks_async(search_target, limit=5)
        # Agar tozalangan so'rovdan kam natija chiqsa, asl so'rov bilan ham sinab ko'ramiz
        if len(tracks) < 2 and search_target != query:
            extra_tracks = await search_music_tracks_async(query, limit=5)
            existing_ids = {t["id"] for t in tracks}
            for t in extra_tracks:
                if t["id"] not in existing_ids:
                    tracks.append(t)
                    existing_ids.add(t["id"])
                    if len(tracks) >= 5:
                        break
    except Exception as e:
        logging.error(f"Musiqa qidiruv xatosi: {e}")
        tracks = []

    if not tracks:
        await status_msg.edit_text(
            f"{header_prefix}❌ <b>«{html.escape(query)}»</b> bo'yicha musiqa topilmadi.\n"
            "Iltimos, qo'shiq yoki ijrochi nomini boshqacha aytib/yozib ko'ring.",
            parse_mode="HTML",
        )
        return

    text_lines = []
    if header_prefix:
        text_lines.append(header_prefix.strip())
    text_lines.append(f"🎵 <b>«{html.escape(search_target)}» bo'yicha topilgan musiqalar:</b>\n")
    buttons_row = []

    for idx, track_info in enumerate(tracks, 1):
        clean_title = html.escape(track_info["title"])
        dur = track_info["duration_str"]
        text_lines.append(f"{idx}. 🎶 <b>{clean_title}</b> <i>({dur})</i>")
        key = _cache_url(track_info["url"])
        buttons_row.append(
            types.InlineKeyboardButton(text=f"{idx}️⃣", callback_data=f"music:{key}")
        )

    text_lines.append("\n👇 <i>Yuklab olish uchun quyidagi raqamlardan birini tanlang:</i>")
    full_text = "\n".join(text_lines)

    keyboard = [
        buttons_row,
        [types.InlineKeyboardButton(text="❌ Bekor qilish", callback_data="cancel_search")],
    ]
    markup = types.InlineKeyboardMarkup(inline_keyboard=keyboard)

    await status_msg.edit_text(full_text, parse_mode="HTML", reply_markup=markup)


@dp.callback_query(F.data == "cancel_search")
async def cancel_search_callback(callback: types.CallbackQuery):
    """Qidiruv natijalarini yopish."""
    with suppress(Exception):
        await callback.message.delete()
    await callback.answer("Bekor qilindi.")


@dp.message(Command("music"))
async def music_cmd(message: types.Message):
    """Musiqa: URL bo'lsa audiosi, nom bo'lsa top-5 qidirib ko'rsatadi."""
    text = (message.text or "").strip()
    arg = text[len("/music"):].strip() if text.lower().startswith("/music") else ""
    arg = arg.split("\n", 1)[0].strip()

    if not arg:
        await message.answer(
            "🎵 <b>Musiqani qanday yuklash mumkin?</b>\n\n"
            "1️⃣ <b>Qo'shiq nomi</b> — shunchaki nomini yozib yuboring:\n"
            "   <i>Misol: Dilni og'ritma</i> yoki <i>Xamdam Sobirov</i>\n\n"
            "2️⃣ <b>Buyruq orqali:</b>\n"
            "   <i>/music Dilni og'ritma</i>\n\n"
            "3️⃣ <b>Havola orqali:</b>\n"
            "   <i>/music https://youtu.be/...</i>\n\n"
            "4️⃣ Video yuborganingizda, video ostidagi <b>🎵 Musiqasini olish</b> tugmasini bosing.",
            parse_mode="HTML",
        )
        return

    if is_valid_url(arg):
        await _handle_music(message, arg)
    else:
        await handle_music_search(message, arg)


@dp.callback_query(F.data.startswith("music:"))
async def music_button_callback(callback: types.CallbackQuery):
    """Video ostidagi 🎵 tugma — o'sha videoning musiqasini (mp3) yuboradi."""
    key = callback.data.split(":", 1)[1]
    entry = _url_cache.get(key)

    if not entry:
        await callback.answer(
            "⏳ Havola topilmadi (bot qayta ishga tushgan bo'lishi mumkin). "
            "Havolani qayta yuboring.",
            show_alert=True,
        )
        return

    target = callback.message
    if target is None or not hasattr(target, "answer"):
        await callback.answer("⏳ Xabarga hozircha murojaat qilib bo'lmadi.", show_alert=True)
        return

    await callback.answer("⏳ Musiqa tayyorlanmoqda…")  # tugma spinini o'chiradi
    await _handle_music(target, entry[0])


@dp.callback_query(F.data.startswith("desc:"))
async def desc_button_callback(callback: types.CallbackQuery):
    """📜 Tavsif tugmasi — video muallifining izohini (description) yuboradi."""
    key = callback.data.split(":", 1)[1]
    entry = _url_cache.get(key)

    if not entry:
        await callback.answer(
            "⏳ Havola topilmadi (bot qayta ishga tushgan bo'lishi mumkin). "
            "Havolani qayta yuboring.",
            show_alert=True,
        )
        return

    target = callback.message
    if target is None or not hasattr(target, "answer"):
        await callback.answer("⏳ Xabarga hozircha murojaat qilib bo'lmadi.", show_alert=True)
        return

    await callback.answer("📜 Tavsif olinmoqda…")

    try:
        meta = await extract_description_async(entry[0])
    except ValueError as ve:
        code = str(ve)
        track(callback.from_user, "desc", 0)
        if code == "LOGIN_REQUIRED":
            await target.answer(
                "🔒 Bu video tavsifini olish uchun cookie kerak. utils/cookies.txt "
                "fayliga Instagram cookie'larini qo'ying va botni qayta ishga tushiring "
                "(yo'riqnoma: README.md)."
            )
        else:
            await target.answer("❌ Tavsifni olib bo'lmadi.")
        return
    except Exception as e:  # noqa: BLE001
        logging.error(f"Tavsif xatoligi: {e}")
        track(callback.from_user, "desc", 0)
        await target.answer("❌ Tavsifni olib bo'lmadi. Havolani qayta tekshiring.")
        return

    title = meta.get("title") or "Nomsiz video"
    uploader = meta.get("uploader") or ""
    desc = (meta.get("description") or "").strip()

    header = f"📜 {title}"
    if uploader:
        header += f"\n👤 {uploader}"
    header += "\n" + "―" * 20

    if not desc:
        track(callback.from_user, "desc", 1)
        await target.answer(header + "\n\n⚠️ Bu video uchun tavsif topilmadi.")
        return

    if len(desc) > 6000:
        desc = desc[:6000] + "\n… (davomi kesilib qoldi)"

    for part in _chunk_text(f"{header}\n\n{desc}"):
        await target.answer(part)
    track(callback.from_user, "desc", 1)


async def _handle_music(message: types.Message, query: str):
    """
    Musiqani yuklab (yoki qidirib topib) yuboradi — animatsiya bilan.
    `query` — URL yoki qo'shiq nomi.
    """
    initial_text = "⏳ Musiqa yuklanmoqda, kuting..." if is_valid_url(query) else "⏳ Musiqa qidirilmoqda..."
    status_msg = await message.answer(initial_text)
    anim = await _start_animation(status_msg, "Musiqa yuklanmoqda...")
    file_path = None
    final_text = None
    audio_ready = False

    try:
        file_path = await download_audio_async(query)
        audio_ready = True
    except ValueError as ve:
        code = str(ve)
        if code == "FILE_TOO_LARGE":
            final_text = "❌ Audio hajmi 50 MB dan katta — yuborib bo'lmadi."
        elif code == "LOGIN_REQUIRED":
            if is_valid_url(query):
                final_text = (
                    "🔒 Ushbu havola mualliflik huquqi yoki cheklov tufayli cookie talab qilmoqda.\n"
                    "utils/cookies.txt ga cookie qo'yib qayta urinib ko'rishingiz mumkin."
                )
            else:
                final_text = "❌ Ushbu musiqani yuklab bo'lmadi. Ro'yxatdan boshqa raqamni tanlab ko'ring."
        elif code == "FFMPEG_MISSING":
            final_text = "❌ FFmpeg topilmadi — audio uchun FFmpeg o'rnatish shart."
        else:
            final_text = "❌ Musiqani yuklashda xatolik yuz berdi."
    except Exception as e:  # noqa: BLE001
        logging.error(f"Audio xatoligi: {e}")
        final_text = "❌ Musiqani topib bo'lmadi. Boshqa nom bilan urinib ko'ring."
    finally:
        await _stop_animation(anim)

    try:
        if audio_ready:
            await status_msg.edit_text("📤 Audio Telegram'ga yuborilmoqda...")

            filename = os.path.splitext(os.path.basename(file_path))[0]
            if filename.endswith("]") and " [" in filename:
                filename = filename.rsplit(" [", 1)[0]

            performer = None
            title = filename
            if " - " in filename:
                parts = filename.split(" - ", 1)
                performer = parts[0].strip()
                title = parts[1].strip()

            await message.answer_audio(
                audio=types.FSInputFile(file_path),
                title=title,
                performer=performer,
                caption="✅ Musiqangiz tayyor! 🎵",
            )
            track(message.from_user, "music", 1)
            await status_msg.delete()
        else:
            track(message.from_user, "music", 0)
            await status_msg.edit_text(final_text)
    except Exception as e:  # noqa: BLE001
        logging.warning(f"Status xabarini yangilab bo'lmadi: {e}")
    finally:
        if file_path:
            cleanup_file(file_path)


@dp.message(F.voice | F.audio)
async def process_voice_or_audio(message: types.Message):
    """
    Foydalanuvchi yuborgan ovozli xabarni (voice) yoki audio faylni qabul qilib,
    Speech-to-Text (STT) orqali matnga aylantiradi va musiqani qidiradi.
    """
    track(message.from_user, "message")
    status_msg = await message.answer("🎙 <i>Ovozli xabar tinglanmoqda...</i>", parse_mode="HTML")

    download_dir = os.getenv("DOWNLOAD_DIR", "downloads")
    os.makedirs(download_dir, exist_ok=True)

    file_id = None
    ext = ".ogg"
    if message.voice:
        file_id = message.voice.file_id
        ext = ".ogg"
    elif message.audio:
        file_id = message.audio.file_id
        ext = ".mp3"

    if not file_id:
        await status_msg.edit_text("❌ Ovozli fayl topilmadi.")
        return

    voice_path = os.path.join(download_dir, f"voice_{uuid.uuid4().hex[:10]}{ext}")

    try:
        tg_file = await bot.get_file(file_id)
        if not tg_file.file_path:
            await status_msg.edit_text("❌ Telegram'dan ovozni yuklab bo'lmadi.")
            return

        await bot.download_file(tg_file.file_path, voice_path)

        # STT — nutqni matnga aylantiramiz
        recognized_text = await transcribe_audio_async(voice_path)

        if not recognized_text:
            await status_msg.edit_text(
                "❌ <b>Ovozni aniqlab bo'lmadi.</b>\n"
                "Iltimos, qo'shiq nomini aniqroq ayting yoki matn sifatida yozib yuboring.",
                parse_mode="HTML",
            )
            return

        header_prefix = f"🗣 <b>Eshitildi:</b> <i>«{html.escape(recognized_text)}»</i>\n\n"
        await handle_music_search(message, recognized_text, status_msg=status_msg, header_prefix=header_prefix)

    except Exception as e:
        logging.error(f"Ovozli xabar xatoligi: {e}")
        await status_msg.edit_text("❌ Ovozli xabarni tahlil qilishda xatolik yuz berdi.")
    finally:
        cleanup_file(voice_path)


@dp.message(F.text)
async def process_text_message(message: types.Message):
    """
    Foydalanuvchi yuborgan matnni tahlil qiladi:
      1) Agar havola bo'lsa (Instagram, TikTok, YouTube) — videoni yuklaydi;
      2) Aks holda (qo'shiq yoki ijrochi nomi) — YouTube'dan top-5 natijalarni
         qidirib, tanlash tugmalari bilan chiqaradi.
    """
    text = (message.text or "").strip()
    if not text:
        return

    # Komandalar (/start, /music) aiogram filtrlarida alohida ishlanadi
    if text.startswith("/"):
        return

    if is_valid_url(text):
        await _process_video_download(message, text)
    else:
        await handle_music_search(message, text)


async def _process_video_download(message: types.Message, url: str):
    """Video yuklab yuborish logikasi."""
    status_msg = await message.answer("⏳ Video yuklanmoqda, kuting...")
    anim = await _start_animation(status_msg, "Video yuklanmoqda...")
    file_path = None
    final_text = None
    video_ready = False

    try:
        file_path = await download_video_async(url)
        video_ready = True
    except ValueError as ve:
        code = str(ve)
        if code == "FILE_TOO_LARGE":
            final_text = "❌ Video hajmi 50 MB dan katta bo'lgani uchun yuborib bo'lmadi."
        elif code == "LOGIN_REQUIRED":
            final_text = (
                "🔒 Bu havola cookie'siz yuklanmayapti (Instagram ko'pchilik postlarni "
                "kirishga ruxsat so'raydi).\n\n"
                "📌 Yechim: brauzerdan Instagram cookie'larini export qilib "
                "utils/cookies.txt fayliga joylang va botni qayta ishga tushiring.\n"
                "Yo'riqnoma: README.md → Instagram cookies bo'limi."
            )
        else:
            final_text = "❌ Videoni yuklashda xatolik yuz berdi."
    except Exception as e:  # noqa: BLE001
        logging.error(f"Xatolik: {e}")
        final_text = "❌ Videoni yuklab bo'lmadi. Havolani qayta tekshiring."
    finally:
        await _stop_animation(anim)

    try:
        if video_ready:
            await status_msg.edit_text("📤 Video Telegram'ga yuborilmoqda...")
            video_input = types.FSInputFile(file_path)
            await message.answer_video(
                video=video_input,
                caption="✅ Videongiz tayyor!\n"
                        "🎵 Musiqasi — pastdagi 🎵 tugma, "
                        "muallif izohi — 📜 Tavsif tugmasi.",
                reply_markup=_video_markup(url),
            )
            track(message.from_user, "video", 1)
            await status_msg.delete()
        else:
            track(message.from_user, "video", 0)
            await status_msg.edit_text(final_text)
    except Exception as e:  # noqa: BLE001
        logging.warning(f"Status xabarini yangilab bo'lmadi: {e}")
    finally:
        if file_path:
            cleanup_file(file_path)

class ConflictWarningFilter(logging.Filter):
    """
    Agar boshqa bot nusxasi getUpdates so'rovini qilsa va TelegramConflictError
    yuz bersa, konsolga aniq va tushunarli yo'riqnoma chiqaradi.
    """
    def __init__(self):
        super().__init__()
        self._last_warn = 0.0

    def filter(self, record: logging.LogRecord) -> bool:
        msg = str(record.getMessage())
        if "TelegramConflictError" in msg or "Conflict: terminated by other getUpdates" in msg:
            now = time.time()
            if now - self._last_warn > 30:  # Har 30 soniyada ko'pi bilan 1 marta ogohlantirish
                self._last_warn = now
                logging.warning(
                    "\n" + "=" * 65 + "\n"
                    "⚠️ [DIQQAT] TelegramConflictError aniqlandi!\n"
                    "Ushbu bot tokeni bilan boshqa joyda bot ishlab turibdi:\n"
                    "1. Lokal kompyuteringizda yoki boshqa serverda bot yoniq qolgan bo'lsa,\n"
                    "   uni darhol to'xtating (bitta tokenga 2 ta bot ulanolmaydi)!\n"
                    "2. Agar Render'da yangi versiya deploy bo'layotgan bo'lsa, eski konteyner\n"
                    "   bir necha soniyada o'chiriladi va bot avtomatik ulanadi.\n"
                    + "=" * 65
                )
        return True


async def main():
    download_dir = os.getenv("DOWNLOAD_DIR", str(BASE_DIR / "downloads"))
    os.makedirs(download_dir, exist_ok=True)

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    logging.getLogger().addFilter(ConflictWarningFilter())

    cookie_st = get_cookie_status()
    if cookie_st["available"]:
        logging.info("🍪 Cookies holati: FAOL (%d ta haqiqiy cookie mavjud)", cookie_st["count"])
    else:
        logging.warning(
            "⚠️ Cookies holati: NOFAOL (%s). Env: COOKIES_CONTENT=%s, COOKIES_BASE64=%s",
            cookie_st["reason"],
            bool(os.getenv("COOKIES_CONTENT")),
            bool(os.getenv("COOKIES_BASE64")),
        )

    dashboard_runner = None
    # ---- 1. Veb-dashboard serverini ishga tushirish (Render portini ochish) ----
    if os.getenv("DASHBOARD_ENABLED", "1").strip().lower() not in ("0", "false", "no"):
        try:
            from dashboard import start_dashboard, set_bot_info
            dashboard_runner = await start_dashboard()
            logging.info("Veb-dashboard serveri muvaffaqiyatli ishga tushirildi.")
            try:
                me = await bot.get_me()
                set_bot_info(me.username, me.id)
                logging.info("Bot ma'lumotlari: @%s (ID: %s)", me.username, me.id)
            except Exception as e:  # noqa: BLE001
                logging.warning("Bot profilini olib bo'lmadi: %s", e)
        except Exception as e:
            logging.error("Dashboard serverini ishga tushirishda xatolik: %s", e)
            raise  # Render Web Service uchun port ochilishi shart

    if _url_cache:
        logging.info("URL kesh yuklandi: %d ta tugma (qayta ishga tushganda saqlanib kelgan)", len(_url_cache))

    # Oldingi sessiyadan yotib qolgan fayllarni tozalaymiz
    removed = cleanup_stale_files(download_dir, max_age_seconds=3600)
    if removed:
        logging.info("Startup tozalash: %d ta eski fayl o'chirildi.", removed)

    # ---- 2. Webhook'ni tozalash (Conflict va eski xabarlar qoldiqlarini yo'qotish) ---
    for attempt in range(1, 4):
        try:
            logging.info("Telegram webhook holati tekshirilmoqda/tozalanmoqda (urinish %d/3)...", attempt)
            await bot.delete_webhook(drop_pending_updates=True)
            logging.info("Telegram webhook muvaffaqiyatli tozalandi (drop_pending_updates=True).")
            break
        except Exception as e:
            logging.warning("delete_webhook xatosi: %s", e)
            if attempt < 3:
                await asyncio.sleep(2)

    # ---- 3. Pollingni boshlash (Dashboard bilan bir vaqtda asinxron ishlaydi) --------
    logging.info("Telegram bot polling boshlanmoqda...")
    try:
        await dp.start_polling(
            bot,
            allowed_updates=dp.resolve_used_update_types(),
            close_bot_session=False,
        )
    finally:
        logging.info("Ilova to'xtatilmoqda. Resurslar tozalanmoqda...")
        if dashboard_runner:
            try:
                await dashboard_runner.cleanup()
                logging.info("Dashboard serveri muvaffaqiyatli to'xtatildi.")
            except Exception as e:
                logging.warning("Dashboard tozalashda xatolik: %s", e)
        try:
            await bot.session.close()
            logging.info("Bot sessiyasi yopildi.")
        except Exception as e:
            logging.warning("Bot sessiyasini yopishda xatolik: %s", e)


if __name__ == "__main__":
    asyncio.run(main())
