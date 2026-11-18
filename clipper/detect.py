"""Audio-energy highlight detection.

Streamer reactions (shouting, laughing, chat-hype moments) show up as loudness spikes
relative to the local baseline. This is a heuristic, not magic: review what it picks.
"""
import subprocess
import numpy as np
from .executables import executable_path

SR = 16000


def load_audio(path):
    cmd = [executable_path("ffmpeg"), "-v", "error", "-i", path, "-vn", "-ac", "1", "-ar", str(SR), "-f", "s16le", "-"]
    raw = subprocess.run(cmd, capture_output=True, check=True).stdout
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def find_moments(path, clip_before=8, clip_after=14, top_n=8, min_gap=25, z_min=2.5, win=0.5):
    x = load_audio(path)
    duration = len(x) / SR
    n = int(SR * win)
    m = len(x) // n
    if m < 8:
        return []
    rms = np.sqrt((x[: m * n].reshape(m, n) ** 2).mean(axis=1) + 1e-12)
    db = 20 * np.log10(rms)

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
        t = i * win
        if any(abs(t - p["peak"]) < min_gap for p in picked):
            continue
        start = max(0.0, t - clip_before)
        end = min(duration, t + clip_after)
        if end - start < 6:
            continue
        picked.append({"peak": round(t, 2), "start": round(start, 2), "end": round(end, 2), "score": round(float(z[i]), 2)})
        if len(picked) >= top_n:
            break
    return sorted(picked, key=lambda p: -p["score"])
