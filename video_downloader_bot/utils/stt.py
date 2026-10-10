"""
Ovozli xabarlarni matnga aylantirish (Speech-to-Text / STT) moduli.
FFmpeg orqali audio WAV formatga o'tkaziladi va Google STT orqali matn aniqlanadi.
"""

from __future__ import annotations

import asyncio
import logging
import os
import shutil
import subprocess

import speech_recognition as sr

logger = logging.getLogger(__name__)


def convert_audio_to_wav(input_path: str, output_path: str) -> bool:
    """FFmpeg yordamida audio faylni 16kHz mono WAV formatiga o'tkazadi."""
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


def transcribe_audio_sync(audio_path: str) -> str | None:
    """
    Audio faylni matnga aylantiradi (Speech-to-Text).
    Avval o'zbek tili (uz-UZ), so'ngra rus (ru-RU) va ingliz (en-US) tillarida sinab ko'radi.
    """
    wav_path = os.path.splitext(audio_path)[0] + "_temp.wav"
    try:
        ok = convert_audio_to_wav(audio_path, wav_path)
        if not ok or not os.path.exists(wav_path):
            logger.error("WAV fayl yaratilmadi.")
            return None

        recognizer = sr.Recognizer()
        with sr.AudioFile(wav_path) as source:
            # shovqinni hisobga olish
            recognizer.adjust_for_ambient_noise(source, duration=0.2)
            audio_data = recognizer.record(source)

        for lang in ("uz-UZ", "ru-RU", "en-US"):
            try:
                text = recognizer.recognize_google(audio_data, language=lang)
                if text and text.strip():
                    logger.info(f"Ovoz tanildi ({lang}): {text}")
                    return text.strip()
            except sr.UnknownValueError:
                continue
            except sr.RequestError as e:
                logger.error(f"Google STT so'rov xatosi ({lang}): {e}")
                break

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
