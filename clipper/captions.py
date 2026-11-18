"""Speech -> word timings (faster-whisper), swear detection, and styled captions.

The clip is transcribed ONCE and the same word list drives the captions, the bleeps and the
"don't mute the streamer" rule. All times are seconds relative to the clip start."""
import os
import re
import subprocess
import tempfile

from .executables import executable_path

# Strong stems: matched anywhere inside the word.
STRONG_STEMS = ("fuck", "shit", "bitch", "cunt", "nigg", "fagg", "whore", "slut", "asshole", "dumbass")
# Weak words: exact match only ("cock" must not catch "cockpit", "ass" must not catch "assume").
WEAK_EXACT = {"ass", "asses", "dick", "dicks", "cock", "cocks", "piss", "pissed", "pussy", "bastard",
              "bastards", "prick", "wanker", "twat"}
# Mild words like "damn" are left alone; add them via render.extra_profanity in the config.


def is_profane(word, extra=()):
    raw = word.strip().lower()
    if re.match(r"^[a-z]\*{2,}", raw):  # Whisper sometimes writes f***, s***
        return True
    cleaned = re.sub(r"[^a-z]", "", raw)
    if not cleaned:
        return False
    return (cleaned in WEAK_EXACT or cleaned in extra
            or any(stem in cleaned for stem in STRONG_STEMS))


_models = {}


def transcribe_words(src, start, end, model_size="small", language="en"):
    """Return [{'text','start','end'}] for src[start:end], times relative to `start`."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as exc:
        raise RuntimeError(
            "Captions / bleeping need faster-whisper, which is not installed, so swears would "
            "go out UNBLEEPED. Run: pip install faster-whisper"
        ) from exc
    if model_size not in _models:
        _models[model_size] = WhisperModel(model_size, compute_type="int8")
    with tempfile.TemporaryDirectory() as td:
        wav = os.path.join(td, "seg.wav")
        subprocess.run([executable_path("ffmpeg"), "-y", "-v", "error", "-ss", str(start), "-i", os.path.abspath(src),
                        "-t", str(end - start), "-vn", "-ac", "1", "-ar", "16000", wav], check=True)
        segments, _ = _models[model_size].transcribe(wav, word_timestamps=True, vad_filter=True, language=language)
        return [{"text": w.word.strip(), "start": float(w.start), "end": float(w.end)}
                for s in segments for w in (s.words or []) if w.word.strip()]


def swear_windows(words, extra=(), pad_before=0.05, pad_after=0.10):
    """Time windows (clip-relative) to bleep, merged if they touch."""
    wins = sorted((max(0.0, w["start"] - pad_before), w["end"] + pad_after)
                  for w in words if is_profane(w["text"], extra))
    merged = []
    for a, b in wins:
        if merged and a <= merged[-1][1]:
            merged[-1] = (merged[-1][0], max(merged[-1][1], b))
        else:
            merged.append((a, b))
    return merged


def _ass_time(t):
    cs = int(round(max(0.0, t) * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    return f"{h}:{m:02}:{cs // 100:02}.{cs % 100:02}"


def make_ass(words, out_path, redact=False, extra=(), font="Arial", font_size=92,
             max_words=3, max_chars=17, margin_v=470, w=1080, h=1920):
    """Short-form style captions: 1-3 big uppercase words, active word in yellow, quick pop-in.

    Authored at the real 1080x1920 resolution, so sizes mean what they say (SRT +
    force_style is scaled to a 288px-tall canvas and comes out ~7x too large)."""
    if not words:
        return None
    shown = []
    for wd in words:
        t = "BLEEP" if redact and is_profane(wd["text"], extra) else wd["text"]
        shown.append({"t": re.sub(r"[{}\\]", "", t).upper(), "start": wd["start"], "end": wd["end"]})

    chunks, cur = [], []
    for wd in shown:
        too_long = len(" ".join(x["t"] for x in cur + [wd])) > max_chars
        pause = cur and wd["start"] - cur[-1]["end"] > 0.6
        if cur and (len(cur) >= max_words or too_long or pause or cur[-1]["t"][-1:] in ".?!"):
            chunks.append(cur)
            cur = []
        cur.append(wd)
    if cur:
        chunks.append(cur)

    yellow, white = r"{\c&H00E6FF&}", r"{\c&HFFFFFF&}"
    pop = r"{\fscx82\fscy82\t(0,90,\fscx100\fscy100)}"
    lines = []
    for chunk in chunks:
        for i, wd in enumerate(chunk):
            nxt = chunk[i + 1]["start"] if i + 1 < len(chunk) else None
            a = wd["start"]
            b = nxt if nxt is not None and nxt - wd["end"] < 0.35 else wd["end"] + 0.12
            text = " ".join((yellow + x["t"] + white) if j == i else x["t"] for j, x in enumerate(chunk))
            lines.append(f"Dialogue: 0,{_ass_time(a)},{_ass_time(max(b, a + 0.08))},Default,,0,0,0,,{pop}{text}")

    header = (
        "[Script Info]\nScriptType: v4.00+\n"
        f"PlayResX: {w}\nPlayResY: {h}\nWrapStyle: 2\nScaledBorderAndShadow: yes\n\n"
        "[V4+ Styles]\n"
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, "
        "MarginL, MarginR, MarginV, Encoding\n"
        f"Style: Default,{font},{font_size},&H00FFFFFF,&H00FFFFFF,&H00000000,&H80000000,-1,0,0,0,100,100,0,0,1,7,3,2,60,60,{margin_v},1\n\n"
        "[Events]\nFormat: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
    )
    with open(out_path, "w", encoding="utf-8") as f:
        f.write(header + "\n".join(lines) + "\n")
    return out_path
