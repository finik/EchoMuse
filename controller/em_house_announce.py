"""
House announcement — say "announce", then say the message, hear your voice
in every room.

The controller has no voice of its own. The prompt is a recorded line played
on the device that was spoken to. The message is the recording of what was
said next, played back as-is. Home Assistant never sees that second
utterance, so it cannot treat "dinner is ready" as a command.
"""

from __future__ import annotations

import os

import numpy as np

MIC_RATE = 16000
SPEAKER_RATE = 48000

# A sentence or two. Longer than this is a mistake nobody wants in every room.
MAX_S = 15.0
# How long to wait for the first word after the prompt.
WAIT_S = 8.0
# Silence that ends the recording, long enough for a pause mid-sentence.
HUSH_S = 1.2

# int16 RMS above which a chunk counts as speech. Room noise on these
# devices sits well under this once the stream has the usual gain.
SPEECH_RMS = 600

PROMPT_FILE = os.path.join(
    os.path.dirname(os.path.abspath(__file__)), "sounds", "announce_prompt.pcm"
)

_COMMANDS = {
    "announce",
    "announcement",
    "make an announcement",
}
_FILLER = {"please", "now"}


def _words(text: str) -> list[str]:
    if not text:
        return []
    lowered = text.lower().replace("'", "").replace("’", "")
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in lowered)
    return [w for w in cleaned.split() if w not in _FILLER]


def is_announce_command(text: str) -> bool:
    """True only when the utterance is the command and nothing else."""
    return " ".join(_words(text)) in _COMMANDS


# Longest prefix first, so "make an announcement" is not read as "announcement".
_PREFIXES = ("make an announcement", "announcement", "announce")

# 15s of 16 kHz mono. The one-shot recording is kept in memory only.
MAX_PCM = int(MAX_S * MIC_RATE * 2)


def announcement_body(text: str) -> str | None:
    """The message after "announce", or None if this is not that sentence."""
    joined = " ".join(_words(text))
    for prefix in _PREFIXES:
        lead = prefix + " "
        if joined.startswith(lead):
            body = joined[len(lead):].strip()
            return body or None
    return None


def is_speech(pcm: bytes) -> bool:
    if len(pcm) < 2:
        return False
    samples = np.frombuffer(pcm, dtype=np.int16)
    if samples.size == 0:
        return False
    rms = float(np.sqrt(np.mean(samples.astype(np.float64) ** 2)))
    return rms >= SPEECH_RMS


def to_speaker(pcm16: bytes) -> bytes:
    """16 kHz mic audio to the 48 kHz speaker plane, by repeating samples."""
    if len(pcm16) < 2:
        return b""
    samples = np.frombuffer(pcm16, dtype=np.int16)
    if samples.size == 0:
        return b""
    up = np.repeat(samples, SPEAKER_RATE // MIC_RATE)
    return up.astype(np.int16).tobytes()


def chime() -> bytes:
    """Two short rising notes, so the playback is not a voice from nowhere."""
    sr = SPEAKER_RATE
    parts = []
    for freq, ms in ((659, 90), (988, 160)):
        n = int(sr * ms / 1000)
        t = np.arange(n) / sr
        env = np.minimum(1.0, np.minimum(t * 40, (n - np.arange(n)) / (sr * 0.02)))
        wave = 0.25 * np.sin(2 * np.pi * freq * t) * env
        parts.append((wave * 32767).astype(np.int16))
    gap = np.zeros(int(sr * 0.04), dtype=np.int16)
    return np.concatenate([parts[0], gap, parts[1], gap]).tobytes()


def load_prompt() -> bytes:
    try:
        with open(PROMPT_FILE, "rb") as f:
            return f.read()
    except OSError:
        return b""
