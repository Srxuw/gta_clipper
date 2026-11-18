import argparse
import glob
import importlib.util
import json
import os
import shutil
import sys
import time
from datetime import datetime

from . import queue as Q
from .captions import make_srt, find_swear_windows
from .detect import find_moments
from .executables import find_executable
from .render import pick_hook, render_clip
from .safety import find_nudity_windows, mute_windows_without_speech

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".ts")


def load_cfg(p):
    with open(p, encoding="utf-8") as f:
        return json.load(f)


def caption_text(cfg, hook):
    c = cfg["campaign"]
    return f"{hook.title()} {' '.join(c['required_hashtags'])} {c['disclosure']}".strip()


def process_video(cfg, src):
    d, paths = cfg["detect"], cfg["paths"]
    os.makedirs(paths["clips"], exist_ok=True)
    moments = find_moments(src, d["clip_before_s"], d["clip_after_s"], d["top_n_per_video"], d["min_gap_s"], d["z_min"])
    print(f"{os.path.basename(src)}: {len(moments)} moments")
    items = Q.load(paths["queue"])
    stem = os.path.splitext(os.path.basename(src))[0]
    for i, m in enumerate(moments):
        out = os.path.join(paths["clips"], f"{stem}_{i:02d}.mp4")
        hook = pick_hook(cfg["streamer_name"], i)
        render_cfg = cfg["render"]
        srt = None
        if cfg["render"]["captions"]:
            srt = make_srt(
                src,
                m["start"],
                m["end"],
                out.replace(".mp4", ".srt"),
                redact_swears=render_cfg.get("bleep_swears", True),
            )
        safety_windows = []
        mute_windows = []
        swear_windows = find_swear_windows(src, m["start"], m["end"]) if cfg["render"]["captions"] else []
        if render_cfg.get("blur_nudity", True):
            safety_windows = find_nudity_windows(src, m["start"], m["end"])
        if render_cfg.get("mute_nudity_audio", False) and safety_windows:
            mute_windows = mute_windows_without_speech(safety_windows, srt, m["start"])
        render_clip(
            src,
            m["start"],
            m["end"],
            out,
            hook,
            render_cfg["font"],
            srt,
            safety_windows=safety_windows,
            mute_windows=mute_windows,
            swear_windows=swear_windows,
            bleep_swears=render_cfg.get("bleep_swears", True),
        )
        if render_cfg.get("keep_unbleeped_copy", True) and swear_windows:
            original_out = out.replace(".mp4", "_unbleeped.mp4")
            original_srt = None
            if render_cfg["captions"]:
                original_srt = make_srt(
                    src, m["start"], m["end"],
                    original_out.replace(".mp4", ".srt"),
                    redact_swears=False,
                )
            render_clip(
                src, m["start"], m["end"], original_out, hook,
                render_cfg["font"], original_srt,
                safety_windows=safety_windows,
                mute_windows=mute_windows,
                bleep_swears=False,
            )
        Q.add_clip(items, out, caption_text(cfg, hook), hook, m["score"], list(cfg["accounts"].keys()))
        print(f"  rendered {out} (score {m['score']}, peak {m['peak']}s)")
    Q.save(paths["queue"], items)
    if moments and cfg["render"].get("delete_source_after_clips", False):
        if os.path.isfile(src) and os.path.commonpath([os.path.abspath(src), os.path.abspath(paths["inbox"])]) == os.path.abspath(paths["inbox"]):
            os.remove(src)
            print(f"  deleted processed source {src}")


def cmd_process(a):
    process_video(load_cfg(a.config), a.video)


def cmd_watch(a):
    cfg = load_cfg(a.config)
    inbox, seen = cfg["paths"]["inbox"], set()
    os.makedirs(inbox, exist_ok=True)
    print(f"watching {inbox}/ (drop campaign-approved footage or your own recordings here)")
    while True:
        for f in sorted(glob.glob(os.path.join(inbox, "*"))):
            if f.lower().endswith(VIDEO_EXT) and f not in seen:
                size = os.path.getsize(f)
                time.sleep(3)
                if os.path.getsize(f) != size:  # still being written
                    continue
                seen.add(f)
                process_video(cfg, f)
                items = Q.schedule(Q.load(cfg["paths"]["queue"]), cfg["schedule"])
                Q.save(cfg["paths"]["queue"], items)
        if a.once:
            return
        time.sleep(20)


def cmd_wait(a):
    cfg = load_cfg(a.config)
    t = datetime.fromisoformat(cfg["launch_time_local"])
    while datetime.now() < t:
        left = t - datetime.now()
        print(f"launch in {left}", end="\r")
        time.sleep(min(30, max(1, left.total_seconds())))
    print("\nlaunch time reached")


def cmd_schedule(a):
    cfg = load_cfg(a.config)
    Q.save(cfg["paths"]["queue"], Q.schedule(Q.load(cfg["paths"]["queue"]), cfg["schedule"]))
    cmd_status(a)


def cmd_post(a):
    cfg = load_cfg(a.config)
    items = Q.load(cfg["paths"]["queue"])
    tt = cfg.get("tiktok", {})
    now = datetime.now()
    for it in items:
        if it["status"] != "scheduled" or datetime.fromisoformat(it["publish_at"]) > now:
            continue
        try:
            if it["platform"] == "youtube":
                from .post_youtube import upload
                yt = cfg.get("youtube", {})
                it["url"] = upload(
                    it["clip"],
                    it["title"][:100],
                    it["title"],
                    ["GTA6", "Shorts"],
                    secrets=yt.get("client_secret", "client_secret.json"),
                    token=yt.get("token", "yt_token.json"),
                    privacy=yt.get("privacy", "public"),
                )
                it["status"] = "posted"
                Q.log_submission(cfg["paths"]["submissions"], it)
                print("youtube posted public", it["url"])
            elif it["platform"] == "tiktok":
                from .post_tiktok import upload
                r = upload(it["clip"], it["title"], tt.get("mode", "direct"),
                           tt.get("privacy", "PUBLIC_TO_EVERYONE"), tt.get("paid_partnership", False))
                handle = cfg["accounts"].get("tiktok", "").lstrip("@")
                if r["post_ids"] and handle:
                    it["url"] = f"https://www.tiktok.com/@{handle}/video/{r['post_ids'][0]}"
                it["status"] = "posted" if r["privacy"] in (None, "PUBLIC_TO_EVERYONE") else "posted_" + str(r["privacy"]).lower()
                if tt.get("mode") == "inbox":
                    it["status"] = "in_tiktok_inbox"
                print(f"tiktok {it['status']} (publish_id {r['publish_id']})")
                if it["url"] and it["status"] == "posted":  # private posts can't be submitted to campaigns
                    Q.log_submission(cfg["paths"]["submissions"], it)
            else:
                it["status"] = "manual"
                print(f"UPLOAD MANUALLY to {it['platform']}: {it['clip']}\n  caption: {it['title']}")
        except Exception as e:  # keep the queue alive, retry nothing silently
            it["status"] = "failed"
            it["error"] = str(e)[:300]
            print(f"{it['platform']} failed:", e)
    Q.save(cfg["paths"]["queue"], items)


def cmd_auth_tiktok(a):
    from . import post_tiktok as T
    cfg = load_cfg(a.config)
    redirect = cfg["tiktok"]["redirect_uri"]
    print("Open this URL, approve, then paste the `code` value from the redirect URL:\n")
    print(T.auth_url(redirect, cfg["tiktok"].get("mode", "direct")), "\n")
    T.exchange_code(input("code: ").strip(), redirect)
    print("saved tiktok_token.json")


def cmd_auth_youtube(a):
    from googleapiclient.discovery import build  # noqa: F401  (fail early if not installed)
    from .post_youtube import SCOPES
    from google_auth_oauthlib.flow import InstalledAppFlow
    cfg = load_cfg(a.config)
    yt = cfg.get("youtube", {})
    secrets = yt.get("client_secret", "client_secret.json")
    token = yt.get("token", "yt_token.json")
    creds = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES).run_local_server(port=0)
    open(token, "w", encoding="utf-8").write(creds.to_json())
    print(f"saved {token}")


def cmd_url(a):
    """Link in, clips out: download a source you're allowed to clip, then run the normal pipeline."""
    import subprocess
    cfg = load_cfg(a.config)
    yt_dlp = shutil.which("yt-dlp")
    if importlib.util.find_spec("yt_dlp") is not None:
        cmd_prefix = [sys.executable, "-m", "yt_dlp"]
    elif yt_dlp:
        cmd_prefix = [yt_dlp]
    else:
        raise SystemExit("yt-dlp not found. Install it with: pip install yt-dlp")
    inbox = cfg["paths"]["inbox"]
    os.makedirs(inbox, exist_ok=True)
    before = {
        name: os.path.getmtime(os.path.join(inbox, name))
        for name in os.listdir(inbox)
    }
    section = ""
    if a.start or a.end:
        section_start = (a.start or "0").replace(":", "-")
        section_end = (a.end or "inf").replace(":", "-")
        section = f"_{section_start}-{section_end}"
    cmd = cmd_prefix + ["--no-playlist", "-f", "bv*[height<=1080]+ba/b[height<=1080]/b",
           "--merge-output-format", "mp4", "--force-overwrites",
           "-o", os.path.join(inbox, "%(id)s" + section + ".%(ext)s")]
    ffmpeg = find_executable("ffmpeg")
    if ffmpeg:
        cmd += ["--ffmpeg-location", os.path.dirname(ffmpeg)]
    else:
        raise SystemExit(
            "ffmpeg not found. Install it with: winget install Gyan.FFmpeg"
        )
    if a.start or a.end:
        cmd += ["--download-sections", f"*{a.start or '0'}-{a.end or 'inf'}"]
    subprocess.run(cmd + [a.url], check=True)
    new = [
        f for f in os.listdir(inbox)
        if f.lower().endswith(VIDEO_EXT)
        and (f not in before or os.path.getmtime(os.path.join(inbox, f)) > before[f])
    ]
    if not new:
        raise SystemExit("download finished but no new video file found in inbox/")
    for f in new:
        process_video(cfg, os.path.join(inbox, f))
    Q.save(cfg["paths"]["queue"], Q.schedule(Q.load(cfg["paths"]["queue"]), cfg["schedule"]))
    cmd_status(a)


def cmd_mark(a):
    cfg = load_cfg(a.config)
    items = Q.load(cfg["paths"]["queue"])
    for it in items:
        if it["id"] == a.id:
            it["status"], it["url"] = "posted", a.url
            Q.log_submission(cfg["paths"]["submissions"], it)
    Q.save(cfg["paths"]["queue"], items)


def cmd_cleanup_source(a):
    cfg = load_cfg(a.config)
    source = os.path.abspath(a.source)
    inbox = os.path.abspath(cfg["paths"]["inbox"])
    if os.path.commonpath([source, inbox]) != inbox:
        raise SystemExit("Refusing to delete a file outside the configured inbox/")
    if not source.lower().endswith(VIDEO_EXT):
        raise SystemExit("Source must be a video file")
    if not os.path.isfile(source):
        raise SystemExit(f"Source not found: {source}")
    stem = os.path.splitext(os.path.basename(source))[0]
    clips = [
        os.path.join(cfg["paths"]["clips"], name)
        for name in os.listdir(cfg["paths"]["clips"])
        if name.startswith(stem + "_") and name.lower().endswith(".mp4")
    ]
    if not clips:
        raise SystemExit("No rendered clips found; source was not deleted")
    if not a.force:
        raise SystemExit("Pass --force to delete the source after its clips are rendered")
    os.remove(source)
    print(f"deleted source: {source}")


def cmd_status(a):
    for it in Q.load(load_cfg(a.config)["paths"]["queue"]):
        print(f"{it['status']:>9}  {it['publish_at'] or '-':<17}  {it['platform']:<9}  {it['id']}")


def cmd_run(a):
    cmd_wait(a)
    cfg = load_cfg(a.config)
    print("running: processing inbox + posting due items (Ctrl+C to stop)")
    while True:
        a.once = True
        cmd_watch(a)
        cmd_post(a)
        time.sleep(30)


def main():
    p = argparse.ArgumentParser(prog="clipper")
    p.add_argument("-c", "--config", default="config.json")
    sub = p.add_subparsers(dest="cmd", required=True)
    s = sub.add_parser("process"); s.add_argument("video"); s.set_defaults(f=cmd_process)
    s = sub.add_parser("watch"); s.add_argument("--once", action="store_true"); s.set_defaults(f=cmd_watch)
    s = sub.add_parser("url"); s.add_argument("url"); s.add_argument("--start"); s.add_argument("--end"); s.set_defaults(f=cmd_url)
    sub.add_parser("wait-launch").set_defaults(f=cmd_wait)
    sub.add_parser("schedule").set_defaults(f=cmd_schedule)
    sub.add_parser("post").set_defaults(f=cmd_post)
    sub.add_parser("auth-tiktok").set_defaults(f=cmd_auth_tiktok)
    sub.add_parser("auth-youtube").set_defaults(f=cmd_auth_youtube)
    sub.add_parser("status").set_defaults(f=cmd_status)
    s = sub.add_parser("mark"); s.add_argument("id"); s.add_argument("url"); s.set_defaults(f=cmd_mark)
    s = sub.add_parser("cleanup-source"); s.add_argument("source"); s.add_argument("--force", action="store_true"); s.set_defaults(f=cmd_cleanup_source)
    sub.add_parser("run").set_defaults(f=cmd_run)
    a = p.parse_args()
    a.f(a)


if __name__ == "__main__":
    main()
