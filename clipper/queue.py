"""Tiny JSON queue + scheduler. One entry per (clip, platform)."""
import csv
import json
import os
from datetime import datetime, timedelta


def load(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def save(path, items):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(items, f, indent=2)


def add_clip(items, clip_path, title, hook, score, platforms):
    existing = {i["id"] for i in items}
    for p in platforms:
        cid = f"{os.path.basename(clip_path)}::{p}"
        if cid in existing:  # re-processing a video must not double-post its clips
            continue
        items.append({"id": cid, "clip": clip_path, "platform": p, "title": title, "hook": hook,
                      "score": score, "status": "pending", "publish_at": None, "url": None})


def _slots(now, cfg_sched):
    """Yield every future posting slot, day after day, inside the daily window."""
    per_day = cfg_sched["posts_per_day_per_platform"]
    start_h, end_h = cfg_sched["window_start_hour"], cfg_sched["window_end_hour"]
    step = (end_h - start_h) / per_day
    day = 0
    while True:
        for s in range(per_day):
            hour = start_h + s * step
            t = (now + timedelta(days=day)).replace(hour=int(hour), minute=int((hour % 1) * 60),
                                                    second=0, microsecond=0)
            if t > now:
                yield t
        day += 1


def schedule(items, cfg_sched, now=None):
    """Give pending items the next FREE slot per platform, best clips first.
    Slots already taken by earlier batches are skipped, so posts never collide."""
    now = now or datetime.now()
    for p in {i["platform"] for i in items}:
        taken = {i["publish_at"] for i in items if i["platform"] == p and i["publish_at"]}
        todo = sorted((i for i in items if i["platform"] == p and i["status"] == "pending"),
                      key=lambda i: -i["score"])
        slots = _slots(now, cfg_sched)
        for it in todo:
            while True:
                t = next(slots).isoformat(timespec="minutes")
                if t not in taken:
                    break
            taken.add(t)
            it["publish_at"], it["status"] = t, "scheduled"
    return items


def log_submission(path, item):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["id", "platform", "posted_url", "posted_at"])
        w.writerow([item["id"], item["platform"], item["url"], datetime.now().isoformat(timespec="minutes")])
