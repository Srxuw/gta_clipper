"""Render a moment as a 1080x1920 clip: blurred fill background, gameplay centred, hook text for
the first few seconds, styled captions, loudness-normalised 48 kHz audio, blur/mute/bleep windows.

Every window passed in is (start, end) in seconds RELATIVE TO THE CLIP START."""
import json
import os
import re
import shutil
import subprocess
import tempfile
import textwrap

from .captions import make_ass
from .executables import executable_path

HOOKS = [
    "WAIT FOR IT...",
    "{name} WAS NOT READY",
    "THE REACTION SAYS IT ALL",
    "CHAT LOST IT",
    "NOBODY SAW THIS COMING",
    "THIS PART IS INSANE",
]


def pick_hook(name, idx, hooks=None):
    hooks = hooks or HOOKS
    return hooks[idx % len(hooks)].format(name=name.upper())


def _enable(windows):
    return "+".join(f"between(t,{a:.3f},{b:.3f})" for a, b in windows)


def _has_audio(path):
    out = subprocess.run([executable_path("ffprobe"), "-v", "error", "-select_streams", "a",
                          "-show_entries", "stream=index", "-of", "csv=p=0", path],
                         capture_output=True, text=True)
    return bool(out.stdout.strip())


def render_clip(src, start, end, out, hook, font, words=None, caption_font="Arial",
                blur_windows=None, silence_windows=None, bleep_windows=None,
                redact_captions=False, extra_profanity=(), w=1080, h=1920):
    """blur_windows: picture blurred. silence_windows: audio muted (nudity with no one talking).
    bleep_windows: audio replaced by a bleep tone (the streamer's swear words)."""
    dur = end - start
    silence_windows = list(silence_windows or [])
    bleep_windows = list(bleep_windows or [])
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "hook.txt"), "w", encoding="utf-8") as f:
            f.write("\n".join(textwrap.wrap(hook, 18)))
        def _esc(p):
            p = p.replace("\\", "/")
            if re.match(r"^[A-Za-z]:/", p):  # Windows drive letter needs escaping inside a filter
                drive, rest = p.split(":", 1)
                p = drive + r"\:" + rest
            return p
        font_filter = _esc(font)
        fonts_dir = _esc(os.path.dirname(os.path.abspath(font)) if os.path.isabs(font) else os.path.dirname(font) or ".")

        fc = (
            "[0:v]split=2[a][b];"
            f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=30:5[bg];"
            f"[b]scale={w}:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0];"
        )
        if blur_windows:
            fc += f"[v0]boxblur=35:8:enable='{_enable(blur_windows)}'[v1];"
        else:
            fc += "[v0]null[v1];"
        fc += (
            f"[v1]drawtext=fontfile='{font_filter}':textfile='hook.txt':fontsize=76:fontcolor=white:"
            "box=1:boxcolor=black@0.65:boxborderw=26:line_spacing=10:x=(w-text_w)/2:y=230:"
            "enable='between(t,0,3.5)'[v2]"
        )
        last = "[v2]"
        if words and make_ass(words, os.path.join(td, "cap.ass"), redact=redact_captions,
                              extra=extra_profanity, font=caption_font):
            fc += f";{last}subtitles=cap.ass:fontsdir='{fonts_dir}'[v3]"
            last = "[v3]"

        # audio: clean-up + loudness -> 48 kHz (loudnorm leaves it at 192 kHz otherwise) -> mute/bleep
        a_chain = "highpass=f=80,lowpass=f=14000,loudnorm=I=-16:TP=-1.5:LRA=11,aresample=48000"
        mute_all = silence_windows + bleep_windows
        if mute_all:
            a_chain += f",volume=0:enable='{_enable(mute_all)}'"
        has_audio = _has_audio(src)
        amap = None
        if has_audio:
            if bleep_windows:
                fc += (f";[0:a]{a_chain}[am];sine=frequency=1000:sample_rate=48000:duration={dur:.3f},"
                       f"volume='2.0*({_enable(bleep_windows)})':eval=frame[bp];"
                       "[am][bp]amix=inputs=2:duration=first:normalize=0[aout]")
            else:
                fc += f";[0:a]{a_chain}[aout]"
            amap = "[aout]"

        cmd = [executable_path("ffmpeg"), "-y", "-v", "error", "-ss", str(start), "-i", os.path.abspath(src),
               "-t", str(dur), "-filter_complex", fc, "-map", last]
        if amap:
            cmd += ["-map", amap, "-c:a", "aac", "-b:a", "160k", "-ar", "48000"]
        cmd += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
                "-r", "30", "-movflags", "+faststart", os.path.abspath(out)]
        subprocess.run(cmd, check=True, cwd=td)
    return out


def verify_clip(path, max_mb=60):
    """Check the file is what YouTube Shorts / TikTok accept. Raises with the reason if not."""
    if not os.path.isfile(path) or os.path.getsize(path) == 0:
        raise RuntimeError(f"{path}: missing or empty")
    res = subprocess.run(
        [executable_path("ffprobe"), "-v", "error", "-show_entries",
         "stream=codec_type,codec_name,width,height,sample_rate:format=duration", "-of", "json", path],
        capture_output=True, text=True)
    if res.returncode != 0:
        raise RuntimeError(f"{path}: unreadable or corrupt video file")
    probe = json.loads(res.stdout)
    v = next((s for s in probe["streams"] if s["codec_type"] == "video"), None)
    a = next((s for s in probe["streams"] if s["codec_type"] == "audio"), None)
    dur = float(probe["format"]["duration"])
    problems = []
    if not v or (v["codec_name"], v["width"], v["height"]) != ("h264", 1080, 1920):
        problems.append("video is not 1080x1920 H.264")
    if not a:
        problems.append("no audio track")
    elif a["codec_name"] != "aac" or int(a["sample_rate"]) != 48000:
        problems.append("audio is not 48 kHz AAC")
    if not 3 <= dur <= 180:
        problems.append(f"duration {dur:.1f}s outside 3-180s")
    if os.path.getsize(path) > max_mb * 1024 * 1024:
        problems.append(f"file larger than {max_mb} MB")
    if problems:
        raise RuntimeError(f"{path}: " + "; ".join(problems))
    return dur
