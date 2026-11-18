"""Visual safety: find frames with exposed nudity so they can be blurred (and muted when
nobody is talking). All returned windows are in seconds RELATIVE TO THE CLIP START."""
import os
import subprocess
import tempfile

from .executables import executable_path

# Explicit list. Do NOT substring-match "EXPOSED": that also catches FEET_EXPOSED,
# BELLY_EXPOSED, ARMPITS_EXPOSED and MALE_BREAST_EXPOSED and blurs ordinary footage.
UNSAFE_LABELS = {
    "FEMALE_BREAST_EXPOSED",
    "FEMALE_GENITALIA_EXPOSED",
    "MALE_GENITALIA_EXPOSED",
    "BUTTOCKS_EXPOSED",
    "ANUS_EXPOSED",
    # NudeNet 2.x naming, in case an older version is installed:
    "EXPOSED_BREAST_F", "EXPOSED_GENITALIA_F", "EXPOSED_GENITALIA_M", "EXPOSED_BUTTOCKS", "EXPOSED_ANUS",
}

_detector = None


def _get_detector():
    global _detector
    if _detector is None:
        try:
            from nudenet import NudeDetector
        except ImportError as exc:
            raise RuntimeError(
                "blur_nudity is on but NudeNet is not installed, so NOTHING would be blurred. "
                "Run: pip install nudenet   (or set render.blur_nudity to false on purpose)."
            ) from exc
        _detector = NudeDetector()
    return _detector


def mute_windows_without_speech(blur_windows, speech_windows):
    """Mute a blurred stretch only if nobody is talking in it: the streamer is never muted."""
    return [
        (a, b) for a, b in blur_windows
        if not any(a < s_end and b > s_start for s_start, s_end in speech_windows)
    ]


def find_nudity_windows(src, start, end, sample_interval_s=0.5, threshold=0.40):
    detector = _get_detector()
    dur = end - start
    flagged = []
    with tempfile.TemporaryDirectory() as frame_dir:
        subprocess.run(
            [executable_path("ffmpeg"), "-v", "error", "-ss", str(start), "-i", os.path.abspath(src),
             "-t", str(dur), "-vf", f"fps=1/{sample_interval_s},scale=960:-2",
             os.path.join(frame_dir, "f_%06d.jpg")],
            check=True,
        )
        frames = sorted(n for n in os.listdir(frame_dir) if n.endswith(".jpg"))
        for idx, name in enumerate(frames):
            hits = detector.detect(os.path.join(frame_dir, name))
            if any(h.get("score", 0) >= threshold and str(h.get("class", "")).upper() in UNSAFE_LABELS
                   for h in hits):
                flagged.append(idx * sample_interval_s)
    if not flagged:
        return []

    windows, w_start, w_end = [], flagged[0], flagged[0]
    for t in flagged[1:]:
        if t - w_end <= sample_interval_s * 2:
            w_end = t
        else:
            windows.append((w_start, w_end))
            w_start = w_end = t
    windows.append((w_start, w_end))
    pad = 0.5  # a frame stands for the next `interval` seconds, plus a safety margin
    return [(max(0.0, a - pad), min(dur, b + sample_interval_s + pad)) for a, b in windows]
