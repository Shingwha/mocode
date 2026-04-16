"""Voice transcription via OpenAI Whisper API"""

import asyncio
import logging
import subprocess
from pathlib import Path

logger = logging.getLogger(__name__)


async def transcribe_audio(audio_path: str, api_key: str, base_url: str = "") -> str:
    """Transcribe audio file via Whisper API.

    - Converts SILK/AMR to WAV via ffmpeg if needed
    - Returns transcribed text, or empty string on failure
    """
    path = Path(audio_path)
    if not path.exists():
        logger.warning("Audio file not found: %s", audio_path)
        return ""

    # Convert SILK/AMR to WAV via ffmpeg
    ext = path.suffix.lower()
    if ext in (".silk", ".amr"):
        wav_path = path.with_suffix(".wav")
        try:
            proc = await asyncio.create_subprocess_exec(
                "ffmpeg", "-i", str(path), "-f", "wav", "-y", str(wav_path),
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
            await proc.wait()
            if wav_path.exists() and wav_path.stat().st_size > 0:
                path = wav_path
            else:
                logger.warning("ffmpeg conversion failed for %s", audio_path)
                return ""
        except FileNotFoundError:
            logger.warning("ffmpeg not found, cannot convert %s", audio_path)
            return ""

    # Call Whisper API
    try:
        from openai import AsyncOpenAI

        kwargs = {"api_key": api_key}
        if base_url:
            kwargs["base_url"] = base_url
        client = AsyncOpenAI(**kwargs)

        with open(path, "rb") as f:
            response = await client.audio.transcriptions.create(
                model="whisper-1",
                file=f,
            )
        return response.text
    except Exception as e:
        logger.warning("Whisper transcription failed: %s", e)
        return ""
