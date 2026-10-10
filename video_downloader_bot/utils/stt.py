"""
Ovozli xabarlarni matnga aylantirish (Speech-to-Text / STT) moduli.
Kengaytirilgan imkoniyatlar:
  1. FFmpeg dynaudnorm filtri — past yoki shovqinli ovozlarni kuchaytiradi va ravshanlashtiradi.
  2. Katta va uzun ovozli xabarlarni (10s dan 60s gacha) qismlarga (chunk) bo'lib to'liq transkripsiya qilish.
  3. O'zbek, rus va ingliz tillarida avtomatik aniqlash.
  4. Nutqdagi ortiqcha so'zlarni tozalash (clean_music_query) — YouTube qidiruvi aniq ishlashi uchun.
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import shutil
import subprocess
import wave

import speech_recognition as sr

logger = logging.getLogger(__name__)


def convert_audio_to_wav(input_path: str, output_path: str) -> bool:
    """FFmpeg yordamida audio faylni 16kHz mono WAV formatiga o'tkazadi va dynaudnorm bilan ovozni normallashtiradi."""
    ffmpeg = os.getenv("FFMPEG_PATH") or shutil.which("ffmpeg")
    if not ffmpeg:
        logger.error("FFmpeg topilmadi!")
        return False
    try:
        cmd = [
            ffmpeg,
            "-y",
            "-i",
            input_path,
            "-af",
            "dynaudnorm",
            "-ac",
            "1",
            "-ar",
            "16000",
            output_path,
        ]
        subprocess.run(
            cmd,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=30,
            check=True,
        )
        return os.path.exists(output_path)
    except Exception as e:
        logger.error(f"Audio konvertatsiyasida xatolik: {e}")
        return False


def _get_wav_duration(wav_path: str) -> float:
    """WAV fayl davomiyligini (soniya) hisoblaydi."""
    try:
        with wave.open(wav_path, "rb") as wf:
            frames = wf.getnframes()
            rate = wf.getframerate()
            if rate > 0:
                return frames / float(rate)
    except Exception as e:
        logger.warning(f"WAV davomiyligini aniqlab bo'lmadi: {e}")
    return 0.0


def _recognize_single_audio_data(recognizer: sr.Recognizer, audio_data: sr.AudioData) -> str | None:
    """Bitta audio_data obyektini uz-UZ -> ru-RU -> en-US tillarida sinab ko'radi."""
    for lang in ("uz-UZ", "ru-RU", "en-US"):
        try:
            text = recognizer.recognize_google(audio_data, language=lang)
            if text and text.strip():
                return text.strip()
        except sr.UnknownValueError:
            continue
        except sr.RequestError as e:
            logger.error(f"Google STT so'rov xatosi ({lang}): {e}")
            break
    return None


def transcribe_audio_sync(audio_path: str) -> str | None:
    """
    Audio faylni matnga aylantiradi (Speech-to-Text).
    Qisqa ham, uzun (10-60 soniyali) ovozli xabarlarni ham to'liq transkripsiya qiladi.
    """
    wav_path = os.path.splitext(audio_path)[0] + "_temp.wav"
    try:
        ok = convert_audio_to_wav(audio_path, wav_path)
        if not ok or not os.path.exists(wav_path):
            logger.error("WAV fayl yaratilmadi.")
            return None

        duration = _get_wav_duration(wav_path)
        recognizer = sr.Recognizer()

        # Agar audio 10 soniyadan qisqa bo'lsa — bir martada o'qiymiz
        if duration <= 10.0:
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)
            text = _recognize_single_audio_data(recognizer, audio_data)
            if text:
                logger.info(f"Ovoz tanildi (qisqa, {duration:.1f}s): {text}")
                return text

        # Agar audio uzunroq bo'lsa (10-60 soniya):
        # 8 soniyalik bo'laklarga bo'lib, har bir bo'lakni transkripsiya qilamiz
        chunk_size = 8.0
        current_offset = 0.0
        collected_chunks: list[str] = []

        with sr.AudioFile(wav_path) as source:
            while current_offset < duration:
                chunk_dur = min(chunk_size, duration - current_offset)
                if chunk_dur < 1.0:
                    break
                try:
                    audio_chunk = recognizer.record(source, duration=chunk_dur)
                    part = _recognize_single_audio_data(recognizer, audio_chunk)
                    if part:
                        # Takrorlanishlarning oldini olamiz
                        if not collected_chunks or collected_chunks[-1].lower() != part.lower():
                            collected_chunks.append(part)
                except Exception as e:
                    logger.warning(f"Chunk STT xatosi: {e}")
                current_offset += chunk_dur

        if collected_chunks:
            full_text = " ".join(collected_chunks).strip()
            logger.info(f"Ovoz tanildi (uzun, {duration:.1f}s, {len(collected_chunks)} chunk): {full_text}")
            return full_text

        # Zaxira: butun faylni bir marta to'liq sinab ko'rish
        try:
            with sr.AudioFile(wav_path) as source:
                audio_data = recognizer.record(source)
            fallback_text = _recognize_single_audio_data(recognizer, audio_data)
            if fallback_text:
                return fallback_text
        except Exception:
            pass

        return None
    finally:
        if os.path.exists(wav_path):
            try:
                os.remove(wav_path)
            except OSError:
                pass


async def transcribe_audio_async(audio_path: str) -> str | None:
    """Ovozli xabarni matnga aylantirish (async)."""
    return await asyncio.to_thread(transcribe_audio_sync, audio_path)


def clean_music_query(text: str) -> str:
    """
    Ovozli xabardan yoki matndan ortiqcha so'zlashuv iboralarini tozalab,
    asosiy musiqa va ijrochi nomini ajratib oladi.

    Masalan:
      "Menga Ozoda Nursaidovaning Sevgilim degan qo'shig'ini topib ber"
      -> "Ozoda Nursaidova Sevgilim"
    """
    original = (text or "").strip()
    if not original:
        return ""

    q = original
    stopwords = [
        r"\b(?:menga|bizga|manga|manga\s+ham|menga\s+ham)\b",
        r"\b(?:iltimos|marhamat|iltimos\s+qilib)\b",
        r"\b(?:topib\s+ber(?:gin|ing)?|chiqarib\s+ber(?:gin|ing)?|tashlab\s+ber(?:gin|ing)?|yuklab\s+ber(?:gin|ing)?)\b",
        r"\b(?:yuboring|yubor|tashla|topib|chiqarib|eshitmoqchiman|eshitgim\s+kelyapti)\b",
        r"\b(?:qo[\'\`\ʼ]?shig[\'\`\ʼ]?(?:i|ini|dan|ga|da)?|qoshig[\'\`\ʼ]?(?:i|ini)?|qo[\'\`\ʼ]shiq|qoshiq)\b",
        r"\b(?:musiqas(?:i|ini)?|musikas(?:i|ini)?|musiqa|musika|trek(?:i|ini)?|audio(?:si|sini)?)\b",
        r"\b(?:degan|aytgan|ijro\s+etgan|ijrosidagi)\b",
        r"\b(?:kerak\s+edi|kerak|bormi|keragidi)\b",
        r"\b(?:yangi|eski|eng\s+yangi|oxirgi)\b",
        r"\b(?:bot|botjon|salom|assalomu\s+alaykum)\b",
        r"\b(?:найди|скинь|включи|пожалуйста|песню|песня|трек|музыку|музыка|хочу|скачать)\b",
        r"\b(?:please|download|find|song|track|music)\b",
    ]

    for pat in stopwords:
        q = re.sub(pat, " ", q, flags=re.IGNORECASE)

    q = re.sub(r"[?!.,;:\"'«»—_(){}\[\]]", " ", q)

    words = []
    for w in q.split():
        # Masalan "Ozodaning" -> "Ozoda", "Sobirovning" -> "Sobirov"
        w_clean = re.sub(r"(?:ning|ningdek)$", "", w, flags=re.IGNORECASE)
        if len(w_clean) >= 2:
            words.append(w_clean)

    cleaned = " ".join(words).strip()
    return cleaned if len(cleaned) >= 3 else original
