"""Render a moment as a 1080x1920 clip: blurred fill background, gameplay centred,
hook text for the first few seconds, optional burned-in captions."""
import os
import shutil
import subprocess
import tempfile
import textwrap
import re
from .executables import executable_path

HOOKS = [
    "WAIT FOR IT...",
    "{name} WAS NOT READY",
    "THE REACTION SAYS IT ALL",
    "CHAT LOST IT",
    "NOBODY SAW THIS COMING",
    "THIS PART IS INSANE",
]


def pick_hook(name, idx):
    return HOOKS[idx % len(HOOKS)].format(name=name.upper())


def render_clip(src, start, end, out, hook, font, srt=None, w=1080, h=1920,
                safety_windows=None, mute_windows=None, swear_windows=None,
                bleep_swears=False):
    dur = end - start
    with tempfile.TemporaryDirectory() as td:
        hook_file = os.path.join(td, "hook.txt")
        with open(hook_file, "w", encoding="utf-8") as f:
            f.write("\n".join(textwrap.wrap(hook, 18)))
        font_filter = font.replace("\\", "/")
        if re.match(r"^[A-Za-z]:/", font_filter):
            drive, rest = font_filter.split(":", 1)
            font_filter = drive + r"\:" + rest
        fc = (
            "[0:v]split=2[a][b];"
            f"[a]scale={w}:{h}:force_original_aspect_ratio=increase,crop={w}:{h},boxblur=30:5[bg];"
            f"[b]scale={w}:-2[fg];"
            "[bg][fg]overlay=(W-w)/2:(H-h)/2[v0];"
        )
        if safety_windows:
            enables = "+".join(
                f"between(t,{window_start - start:.3f},{window_end - start:.3f})"
                for window_start, window_end in safety_windows
            )
            fc += f"[v0]boxblur=35:8:enable='{enables}'[v1];"
            last = "[v1]"
        else:
            fc += "[v0]null[v1];"
            last = "[v1]"
        fc += (
            f"{last}drawtext=fontfile='{font_filter}':textfile='hook.txt':fontsize=76:fontcolor=white:"
            "box=1:boxcolor=black@0.65:boxborderw=26:line_spacing=10:x=(w-text_w)/2:y=230:"
            "enable='between(t,0,3.5)'[v2]"
        )
        last = "[v2]"
        if srt:
            shutil.copy(srt, os.path.join(td, "cap.srt"))
            style = "FontSize=24,Bold=1,PrimaryColour=&HFFFFFF00,OutlineColour=&H001A1A1A,BorderStyle=1,Outline=5,Shadow=1,Alignment=2,MarginV=260"
            fc += f";{last}subtitles=cap.srt:force_style='{style}'[v3]"
            last = "[v3]"
        cmd = [
            executable_path("ffmpeg"), "-y", "-v", "error", "-ss", str(start),
            "-i", os.path.abspath(src), "-t", str(dur),
            "-filter_complex", fc, "-map", last,
        ]
        mute_intervals = list(mute_windows or [])
        if bleep_swears:
            mute_intervals.extend(
                (window_start - start, window_end - start)
                for window_start, window_end in (swear_windows or [])
            )
        if mute_intervals:
            enable = "+".join(
                f"between(t,{window_start:.3f},{window_end:.3f})"
                for window_start, window_end in mute_intervals
            )
            cmd += ["-map", "0:a?", "-af", f"highpass=f=80,lowpass=f=14000,loudnorm=I=-16:TP=-1.5:LRA=11,volume=0:enable='{enable}'"]
        else:
            cmd += ["-map", "0:a?", "-af", "highpass=f=80,lowpass=f=14000,loudnorm=I=-16:TP=-1.5:LRA=11"]
        cmd += [
            "-c:v", "libx264", "-preset", "veryfast", "-crf", "20",
            "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "160k",
            "-movflags", "+faststart", os.path.abspath(out),
        ]
        subprocess.run(cmd, check=True, cwd=td)
    return out
