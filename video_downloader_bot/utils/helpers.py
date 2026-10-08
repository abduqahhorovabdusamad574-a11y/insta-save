import os
import re
import logging
import shutil
import time

# Instagram, YouTube va TikTok URL'larini aniqlash regexi
URL_PATTERN = re.compile(
    r'https?://(?:www\.)?(?:instagram\.com/(?:p|reel|reels|tv)/[a-zA-Z0-9_-]+|'
    r'youtu\.be/[a-zA-Z0-9_-]+|(?:www\.)?youtube\.com/(?:watch\?v=|shorts/)[a-zA-Z0-9_-]+|'
    r'tiktok\.com/@[a-zA-Z0-9_.-]+/video/\d+|vt\.tiktok\.com/[a-zA-Z0-9_-]+)'
)

def is_valid_url(url: str) -> bool:
    """URL to'g'ri platformaga tegishli ekanligini tekshiradi."""
    return bool(URL_PATTERN.search(url))

def cleanup_stale_files(directory: str, max_age_seconds: int = 3600) -> int:
    """
    Bot ishga tushganda `downloads/` papkasini tozaladi:

      * `.part` / `.ytdl` / `.tmp` — yarim yuklab qolgan (vaqtinchalik)
        fayllar **darhol** o'chiriladi: ular hech qachon yakuniy natija
        emas va bot ishga tushganda hech qanday yuklash jonli bo'lmaydi.
      * qolgan fayllar — `max_age_seconds` dan eski bo'lsa o'chiriladi
        (ya'ni avvalgi sessiya buzilib qolgan videolar).

    Yangi yaratilgan (lekin hali eskirmagan) fayllar tegishli qoladi,
    shuning uchun bu funksiya xavfsiz. O'chirilgan fayllar sonini qaytaradi.
    """
    temp_exts = (".part", ".ytdl", ".tmp")
    removed = 0
    try:
        now = time.time()
        for name in os.listdir(directory):
            path = os.path.join(directory, name)
            if os.path.isdir(path):
                # Yuklashning vaqtinchalik papkasi (.work_...) — bot ishga
                # tushganda hech qanday yuklash jonli bo'lmaydi, xavfsiz
                if name.startswith(".work_"):
                    try:
                        shutil.rmtree(path, ignore_errors=True)
                        removed += 1
                        logging.info(f"Tozalashda o'chirildi (papka): {path}")
                    except OSError as e:
                        logging.error(f"Papkani o'chirib bo'lmadi ({path}): {e}")
                continue
            if not os.path.isfile(path):
                continue
            try:
                is_temp = name.lower().endswith(temp_exts)
                if is_temp or now - os.path.getmtime(path) >= max_age_seconds:
                    os.remove(path)
                    removed += 1
                    logging.info(f"Tozalashda o'chirildi: {path}")
            except OSError as e:
                logging.error(f"Faylni o'chirib bo'lmadi ({path}): {e}")
    except FileNotFoundError:
        pass
    except OSError as e:
        logging.error(f"Papkani o'qishda xatolik ({directory}): {e}")
    return removed


def cleanup_file(file_path: str):
    """Server diskini bo'shatish uchun yuklangan faylni o'chiradi."""
    try:
        if file_path and os.path.exists(file_path):
            os.remove(file_path)
            logging.info(f"Fayl o'chirildi: {file_path}")
    except Exception as e:
        logging.error(f"Faylni o'chirishda xatolik: {e}")
