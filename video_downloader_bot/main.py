import os
import json
import logging
import asyncio
import time
import hashlib
from contextlib import suppress
from dotenv import load_dotenv
from aiogram import Bot, Dispatcher, types, F, BaseMiddleware
from aiogram.filters import CommandStart, Command
from utils.helpers import is_valid_url, cleanup_file, cleanup_stale_files
from downloader import download_video_async, download_audio_async, extract_description_async
from analytics import track

load_dotenv()
BOT_TOKEN = os.getenv("BOT_TOKEN")

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN topilmadi! .env faylini tekshiring.")

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
URL_CACHE_FILE = os.getenv("URL_CACHE_FILE", ".music_cache.json")
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
        "👋 Xush kelibsiz!\n\n"
        "Menga Instagram, TikTok yoki YouTube havolasini yuboring — "
        "men uni eng yuqori sifatda yuklab beraman.\n\n"
        "🎵 Musiqa uchun: /music <havola yoki qo'shiq nomi>\n"
        "   misol: /music Dilni og'ritma yoki /music https://youtu.be/...\n"
        "   yoki video ostidagi 🎵 Musiqasini olish tugmasini bosing.\n\n"
        "📜 Tavsif: video ostidagi 📜 Tavsif tugmasi video muallifining "
        "izohini (description) ko'rsatadi.\n\n"
        "⏳ Yuklash davomida soat animatsiyasi ko'rinib turadi."
    )


@dp.message(Command("music"))
async def music_cmd(message: types.Message):
    """Musiqa: URL bo'lsa audiosi, matn bo'lsa YouTube'dan qidirib yuklaydi."""
    text = (message.text or "").strip()
    arg = text[len("/music"):].strip() if text.lower().startswith("/music") else ""
    arg = arg.split("\n", 1)[0].strip()

    if not arg:
        await message.answer(
            "🎵 Musiqani qanday yuklash mumkin?\n\n"
            "/music <havola> — YouTube/Instagram/TikTok videosidan audio;\n"
            "/music <qo'shiq nomi> — internetdan (YouTube) qidirib, "
            "TO'LIQ trekni yuklaydi (Shorts/preview emas).\n\n"
            "Misol: /music Dilni og'ritma\n"
            "Yoki: yuborilgan video ostidagi tugmani bosing."
        )
        return

    await _handle_music(message, arg)


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
    status_msg = await message.answer("⏳ Musiqa qidirilmoqda...")
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
            final_text = (
                "🔒 Bu havola cookie'siz yuklanmayapti. utils/cookies.txt ga "
                "cookie'larni qo'ying va botni qayta ishga tushiring "
                "(yo'riqnoma: README.md)."
            )
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

            title = os.path.splitext(os.path.basename(file_path))[0]
            if title.endswith("]") and " [" in title:
                title = title.rsplit(" [", 1)[0]

            await message.answer_audio(
                audio=types.FSInputFile(file_path),
                title=title,
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

@dp.message(F.text)
async def process_video_url(message: types.Message):
    url = message.text.strip()

    if not is_valid_url(url):
        return

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
                "📌 Yechim: brauzerдан Instagram cookie'larini export qilib "
                "utils/cookies.txt fayliga joylang va botni qayta ishga tushiring.\n"
                "Yo'riqnoma: README.md → Instagram cookies bo'limi."
            )
        else:
            final_text = "❌ Videoni yuklashda xatolik yuz berdi."
    except Exception as e:  # noqa: BLE001
        logging.error(f"Xatolik: {e}")
        final_text = "❌ Videoni yuklab bo'lmadi. Havolani qayta tekshiring."
    finally:
        # Avval animatsiyani to'xtatamiz — aks holda u yakuniy xabarni
        # keyin yana "yangilab" yuborishi mumkin
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
    except Exception as e:  # noqa: BLE001 — xabar o'chgach edit rad etilishi va h.k.
        logging.warning(f"Status xabarini yangilab bo'lmadi: {e}")
    finally:
        if file_path:
            cleanup_file(file_path)

async def main():
    download_dir = os.getenv("DOWNLOAD_DIR", "downloads")
    if not os.path.exists(download_dir):
        os.makedirs(download_dir)

    logging.basicConfig(level=logging.INFO)

    # ---- Dashboard (foydalanuvchilar statistikasi) ------------------------
    if os.getenv("DASHBOARD_ENABLED", "1").strip().lower() not in ("0", "false", "no"):
        try:
            from dashboard import start_dashboard, set_bot_info
            await start_dashboard()
            try:
                me = await bot.get_me()
                set_bot_info(me.username, me.id)
            except Exception as e:  # noqa: BLE001
                logging.warning("Bot profilini olib bo'lmadi: %s", e)
        except Exception as e:  # noqa: BLE001 — dashboard botning ishiga xalaqit bermasin
            logging.error("Dashboard'ni ishga tushirib bo'lmadi: %s", e)

    if _url_cache:
        logging.info(f"URL kesh yuklandi: {len(_url_cache)} ta 🎵 tugma (qayta ishga tushganda saqlanib kelgan)")

    # Oldingi sessiyadan yotib qolgan (yarim yuklangan) fayllarni tozalaymiz
    removed = cleanup_stale_files(download_dir, max_age_seconds=3600)
    if removed:
        logging.info(f"Startup tozalash: {removed} ta eski fayl o'chirildi.")

    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())
