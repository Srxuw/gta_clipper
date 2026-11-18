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
    for p in platforms:
        items.append({"id": f"{os.path.basename(clip_path)}::{p}", "clip": clip_path, "platform": p,
                      "title": title, "hook": hook, "score": score, "status": "pending",
                      "publish_at": None, "url": None})


def schedule(items, cfg_sched, now=None):
    """Spread pending items per platform across a daily window, best clips first."""
    now = now or datetime.now()
    per_day = cfg_sched["posts_per_day_per_platform"]
    start_h, end_h = cfg_sched["window_start_hour"], cfg_sched["window_end_hour"]
    step = (end_h - start_h) / per_day
    platforms = {i["platform"] for i in items}
    for p in platforms:
        todo = sorted([i for i in items if i["platform"] == p and i["status"] == "pending"], key=lambda i: -i["score"])
        day, slot = 0, 0
        for it in todo:
            while True:
                t = now.replace(hour=int(start_h + slot * step), minute=int(((start_h + slot * step) % 1) * 60),
                                second=0, microsecond=0) + timedelta(days=day)
                slot += 1
                if slot >= per_day:
                    slot, day = 0, day + 1
                if t > now:
                    break
            it["publish_at"], it["status"] = t.isoformat(timespec="minutes"), "scheduled"
    return items


def log_submission(path, item):
    new = not os.path.exists(path)
    with open(path, "a", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        if new:
            w.writerow(["id", "platform", "posted_url", "posted_at"])
        w.writerow([item["id"], item["platform"], item["url"], datetime.now().isoformat(timespec="minutes")])
