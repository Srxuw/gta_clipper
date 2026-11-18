import os
import subprocess
import tempfile

from .executables import executable_path


UNSAFE_LABELS = (
    "EXPOSED",
    "NUDE",
    "SEX",
)


def speech_windows_from_srt(srt_path):
    if not srt_path or not os.path.exists(srt_path):
        return []
    windows = []
    with open(srt_path, "r", encoding="utf-8") as srt:
        lines = [line.strip() for line in srt.readlines()]
    for line in lines:
        if "-->" not in line:
            continue
        start_text, end_text = (part.strip() for part in line.split("-->", 1))

        def seconds(value):
            hours, minutes, remainder = value.replace(",", ".").split(":")
            return int(hours) * 3600 + int(minutes) * 60 + float(remainder)

        windows.append((seconds(start_text), seconds(end_text)))
    return windows


def mute_windows_without_speech(safety_windows, srt_path, clip_start):
    speech = speech_windows_from_srt(srt_path)
    muted = []
    for safety_start, safety_end in safety_windows:
        relative_start = safety_start - clip_start
        relative_end = safety_end - clip_start
        overlaps_speech = any(
            relative_start < speech_end and relative_end > speech_start
            for speech_start, speech_end in speech
        )
        if not overlaps_speech:
            muted.append((relative_start, relative_end))
    return muted


def find_nudity_windows(src, start, end, sample_interval_s=1.0):
    try:
        from nudenet import NudeDetector
    except ImportError:
        return []

    detector = NudeDetector()
    unsafe = []
    with tempfile.TemporaryDirectory() as frame_dir:
        frame_pattern = os.path.join(frame_dir, "frame_%06d.jpg")
        subprocess.run(
            [
                executable_path("ffmpeg"), "-v", "error", "-ss", str(start),
                "-i", os.path.abspath(src), "-t", str(end - start),
                "-vf", f"fps=1/{sample_interval_s}",
                "-frames:v", str(max(1, int((end - start) / sample_interval_s) + 1)),
                frame_pattern,
            ],
            check=True,
        )
        frame_paths = sorted(
            os.path.join(frame_dir, name)
            for name in os.listdir(frame_dir)
            if name.lower().endswith(".jpg")
        )
        for index, image_path in enumerate(frame_paths):
            timestamp = start + index * sample_interval_s
            detections = detector.detect(image_path)
            if any(
                item.get("score", 0) >= 0.45
                and any(token in str(item.get("class", "")).upper() for token in UNSAFE_LABELS)
                for item in detections
            ):
                unsafe.append(timestamp)
    if not unsafe:
        return []

    windows = []
    window_start = unsafe[0]
    window_end = unsafe[0]
    for timestamp in unsafe[1:]:
        if timestamp - window_end <= sample_interval_s * 2:
            window_end = timestamp
        else:
            windows.append((max(start, window_start - 0.5), min(end, window_end + 0.5)))
            window_start = window_end = timestamp
    windows.append((max(start, window_start - 0.5), min(end, window_end + 0.5)))
    return windows
