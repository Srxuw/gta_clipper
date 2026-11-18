"""Optional burned-in captions via faster-whisper. Returns an .srt path or None."""
import subprocess
import tempfile
import os
import re
from .executables import executable_path


PROFANITY = {
    "fuck", "fucking", "fucked", "shit", "shitty", "bitch", "bitches",
    "asshole", "dick", "piss", "cock", "cunt", "motherfucker",
}


def find_swear_windows(src, start, end):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return []
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "seg.wav")
        subprocess.run([
            executable_path("ffmpeg"), "-y", "-v", "error", "-ss", str(start),
            "-i", src, "-t", str(end - start), "-vn", "-ac", "1", "-ar", "16000", wav,
        ], check=True)
        model = WhisperModel("small", compute_type="int8")
        segments, _ = model.transcribe(wav, word_timestamps=True, vad_filter=True)
        windows = []
        for segment in segments:
            for word in segment.words or []:
                cleaned = re.sub(r"[^a-z]", "", word.word.lower())
                if cleaned in PROFANITY:
                    windows.append((
                        round(start + max(0, word.start), 3),
                        round(start + min(end - start, word.end + 0.06), 3),
                    ))
        return windows


def make_srt(src, start, end, out_srt, words_per_chunk=3, redact_swears=False):
    try:
        from faster_whisper import WhisperModel
    except ImportError:
        return None
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "seg.wav")
        subprocess.run([executable_path("ffmpeg"), "-y", "-v", "error", "-ss", str(start), "-i", src, "-t", str(end - start),
                        "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
        model = WhisperModel("small", compute_type="int8")
        segments, _ = model.transcribe(wav, word_timestamps=True, vad_filter=True)
        words = [w for s in segments for w in (s.words or [])]
    if not words:
        return None

    def ts(t):
        h, rem = divmod(t, 3600)
        mnt, sec = divmod(rem, 60)
        return f"{int(h):02}:{int(mnt):02}:{int(sec):02},{int((sec % 1) * 1000):03}"

    lines = []
    for i in range(0, len(words), words_per_chunk):
        chunk = words[i:i + words_per_chunk]
        text_parts = []
        for word in chunk:
            cleaned = re.sub(r"[^a-z]", "", word.word.lower())
            text_parts.append("BLEEP" if redact_swears and cleaned in PROFANITY else word.word.strip())
        text = " ".join(text_parts).upper()
        lines.append(f"{len(lines) + 1}\n{ts(chunk[0].start)} --> {ts(chunk[-1].end)}\n{text}\n")
    with open(out_srt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return out_srt
