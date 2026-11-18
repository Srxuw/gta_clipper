"""Audio-energy highlight detection.

Streamer reactions (shouting, laughing, chat-hype moments) show up as loudness spikes
relative to the local baseline. This is a heuristic, not magic: review what it picks.

Audio is streamed through ffmpeg in chunks, so a 6-hour VOD uses a few MB of RAM
instead of several GB.
"""
import subprocess
import numpy as np
from .executables import executable_path

SR = 16000


def window_db(path, win=0.5):
    """Loudness (dB) of every `win`-second window, computed while streaming from ffmpeg."""
    n = int(SR * win)
    cmd = [executable_path("ffmpeg"), "-v", "error", "-i", path, "-vn", "-ac", "1",
           "-ar", str(SR), "-f", "s16le", "-"]
    proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    parts, buf = [], b""
    while True:
        data = proc.stdout.read(n * 2 * 240)  # ~2 minutes per read
        if not data:
            break
        buf += data
        k = len(buf) // (n * 2)
        if k:
            x = np.frombuffer(buf[: k * n * 2], dtype=np.int16).astype(np.float32) / 32768.0
            parts.append(np.sqrt((x.reshape(k, n) ** 2).mean(axis=1) + 1e-12))
            buf = buf[k * n * 2:]
    err = proc.stderr.read().decode("utf-8", "replace")
    if proc.wait() != 0:
        raise RuntimeError(f"ffmpeg could not read audio from {path}: {err[:300]}")
    if not parts:
        return np.array([])
    return 20 * np.log10(np.concatenate(parts))


def find_moments(path, clip_before=8, clip_after=14, top_n=8, min_gap=25, z_min=2.5, win=0.5):
    db = window_db(path, win)
    m = len(db)
    duration = m * win
    if m < 8:
        return []

    # local baseline = rolling median over ~60s each side
    k = int(60 / win)
    baseline = np.array([np.median(db[max(0, i - k): i + k + 1]) for i in range(m)])
    mad = np.median(np.abs(db - baseline)) + 1e-6
    z = (db - baseline) / (1.4826 * mad)

    # smooth over ~2s so sustained screaming beats a single door slam
    w = max(1, int(2 / win))
    z = np.convolve(z, np.ones(w) / w, mode="same")

    picked = []
    for i in np.argsort(-z):
        if z[i] < z_min:
            break
        t = float(i * win)
        if any(abs(t - p["peak"]) < min_gap for p in picked):
            continue
        start = max(0.0, t - clip_before)
        end = min(duration, t + clip_after)
        if end - start < 6:
            continue
        picked.append({"peak": round(t, 2), "start": round(start, 2), "end": round(end, 2),
                       "score": round(float(z[i]), 2)})
        if len(picked) >= top_n:
            break
    return sorted(picked, key=lambda p: -p["score"])
