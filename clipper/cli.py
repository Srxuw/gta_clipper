import argparse
import glob
import importlib.util
import json
import os
import re
import shutil
import sys
import time
from datetime import datetime

from . import queue as Q
from .captions import swear_windows, transcribe_words
from .detect import find_moments
from .executables import find_executable
from .render import pick_hook, render_clip, verify_clip
from .safety import find_nudity_windows, mute_windows_without_speech

VIDEO_EXT = (".mp4", ".mkv", ".mov", ".webm", ".ts")
_SEEN = set()  # videos already handled in this run (watch/run loops call cmd_watch repeatedly)


def load_cfg(p):
    with open(p, encoding="utf-8") as f:
        cfg = json.load(f)
    key = str(os.environ.get("CLIPPER_STREAMER") or cfg.get("streamer_name", "")).strip().lower()
    profile = cfg.get("streamer_profiles", {}).get(key)
    if profile:
        cfg["streamer_name"] = profile["name"]
        cfg["campaign"]["required_hashtags"] = profile["hashtags"]
        cfg["campaign"]["disclosure"] = profile.get("disclosure", "")
        cfg["accounts"] = {**cfg.get("accounts", {}), **profile.get("accounts", {})}
    elif os.environ.get("CLIPPER_STREAMER"):
        custom_name = os.environ["CLIPPER_STREAMER"].strip()
        cfg["streamer_name"] = custom_name
        safe_tag = "#" + re.sub(r"[^A-Za-z0-9]", "", custom_name)
        cfg["campaign"]["required_hashtags"] = [safe_tag, "#GTA6"]
    cfg.setdefault("campaign", {}).setdefault("required_hashtags", [])
    cfg.setdefault("campaign", {}).setdefault("disclosure", "")
    return cfg


def caption_text(cfg, hook):
    c = cfg["campaign"]
    name = cfg["streamer_name"]
    return f"{name.upper()} {hook.title()} {' '.join(c['required_hashtags'])} {c['disclosure']}".strip()


def _inside(path, folder):
    try:
        return os.path.commonpath([os.path.abspath(path), os.path.abspath(folder)]) == os.path.abspath(folder)
    except ValueError:  # different drives on Windows
        return False


def _done_marker(cfg, src):
    return os.path.join(cfg["paths"]["clips"], os.path.splitext(os.path.basename(src))[0] + ".done")


def already_processed(cfg, src):
    """True only if a previous run FINISHED this video (a crash halfway must be retried)."""
    return os.path.exists(_done_marker(cfg, src))


def process_video(cfg, src):
    d, paths, r = cfg["detect"], cfg["paths"], cfg["render"]
    os.makedirs(paths["clips"], exist_ok=True)
    moments = find_moments(src, d["clip_before_s"], d["clip_after_s"], d["top_n_per_video"], d["min_gap_s"], d["z_min"])
    print(f"{os.path.basename(src)}: {len(moments)} moments")
    items = Q.load(paths["queue"])
    stem = os.path.splitext(os.path.basename(src))[0]
    captions_on = r.get("captions", True)
    bleep_on = r.get("bleep_swears", True)
    blur_on = r.get("blur_nudity", True)
    mute_nudity = r.get("mute_nudity_audio", True)
    extra = tuple(w.lower() for w in r.get("extra_profanity", []))
    need_words = captions_on or bleep_on or (blur_on and mute_nudity)
    rendered = []
    for i, m in enumerate(moments):
        out = os.path.join(paths["clips"], f"{stem}_{i:02d}.mp4")
        dur = m["end"] - m["start"]
        hook = pick_hook(cfg["streamer_name"], i, r.get("hooks"))

        # One transcript per clip feeds captions, bleeps and the "never mute the streamer" rule.
        # Missing models raise (fail closed) instead of silently posting unfiltered clips.
        words = transcribe_words(src, m["start"], m["end"], r.get("whisper_model", "small"),
                                 r.get("language", "en")) if need_words else []
        swears = swear_windows(words, extra) if bleep_on else []
        blur = find_nudity_windows(src, m["start"], m["end"]) if blur_on else []
        silence = mute_windows_without_speech(blur, [(w["start"], w["end"]) for w in words]) \
            if (blur and mute_nudity) else []

        common = dict(words=words if captions_on else None, caption_font=r.get("caption_font", "Arial"),
                      blur_windows=blur, silence_windows=silence, extra_profanity=extra)
        render_clip(src, m["start"], m["end"], out, hook, r["font"],
                    bleep_windows=swears, redact_captions=bleep_on, **common)
        verify_clip(out)
        rendered.append(out)
        if r.get("keep_unbleeped_copy", True) and swears:
            raw_out = out.replace(".mp4", "_unbleeped.mp4")
            render_clip(src, m["start"], m["end"], raw_out, hook, r["font"],
                        bleep_windows=[], redact_captions=False, **common)
            verify_clip(raw_out)

        with open(out.replace(".mp4", ".json"), "w", encoding="utf-8") as f:  # audit trail for review
            json.dump({"source": os.path.basename(src), "moment": m, "hook": hook,
                       "bleeped_at_s": [list(map(lambda v: round(v, 2), w)) for w in swears],
                       "blurred_at_s": [list(map(lambda v: round(v, 2), w)) for w in blur],
                       "muted_at_s": [list(map(lambda v: round(v, 2), w)) for w in silence],
                       "transcript": " ".join(w["text"] for w in words)}, f, indent=2)
        Q.add_clip(items, out, caption_text(cfg, hook), hook, m["score"], list(cfg["accounts"].keys()))
        Q.save(paths["queue"], items)  # save per clip so a later crash doesn't lose finished clips
        print(f"  rendered {out} (score {m['score']}, peak {m['peak']}s, {dur:.0f}s) "
              f"bleeps={len(swears)} blurred={len(blur)} muted={len(silence)}")
    Q.save(paths["queue"], items)
    with open(_done_marker(cfg, src), "w", encoding="utf-8") as f:
        f.write(datetime.now().isoformat(timespec="seconds"))

    # Free disk space only when EVERY clip rendered AND passed verification (verify_clip raises otherwise).
    if moments and len(rendered) == len(moments) and r.get("delete_source_after_clips", False):
        if os.path.isfile(src) and _inside(src, paths["inbox"]):
            os.remove(src)
            print(f"  deleted processed source {src}")


def cmd_process(a):
    process_video(load_cfg(a.config), a.video)


def cmd_watch(a):
    cfg = load_cfg(a.config)
    inbox = cfg["paths"]["inbox"]
    os.makedirs(inbox, exist_ok=True)
    print(f"watching {inbox}/ (drop campaign-approved footage or your own recordings here)")
    while True:
        for f in sorted(glob.glob(os.path.join(inbox, "*"))):
            if f.lower().endswith(VIDEO_EXT) and f not in _SEEN:
                size = os.path.getsize(f)
                time.sleep(3)
                if os.path.getsize(f) != size:  # still being written
                    continue
                _SEEN.add(f)
                if already_processed(cfg, f):
                    continue
                process_video(cfg, f)
                items = Q.schedule(Q.load(cfg["paths"]["queue"]), cfg["schedule"])
                Q.save(cfg["paths"]["queue"], items)
        if a.once:
            return
        time.sleep(20)


def cmd_wait(a):
    cfg = load_cfg(a.config)
    if not cfg.get("launch_time_local"):
        return
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
                title = f"{it['hook'].title()} #Shorts"[:100]  # short title; hashtags + disclosure go in the description
                it["url"] = upload(
                    it["clip"],
                    title,
                    it["title"],
                    yt.get("tags", ["Shorts"]),
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
    if not _inside(source, inbox):
        raise SystemExit("Refusing to delete a file outside the configured inbox/")
    if not source.lower().endswith(VIDEO_EXT):
        raise SystemExit("Source must be a video file")
    if not os.path.isfile(source):
        raise SystemExit(f"Source not found: {source}")
    stem = os.path.splitext(os.path.basename(source))[0]
    clips = [
        os.path.join(cfg["paths"]["clips"], name)
        for name in os.listdir(cfg["paths"]["clips"])
        if re.fullmatch(re.escape(stem) + r"_\d\d\.mp4", name)
    ]
    for c in clips:
        verify_clip(c)  # never delete the source if a clip is broken
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
    p.add_argument("-s", "--streamer", help="Streamer profile key: yourrage, lacy, clix, or a custom profile")
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
    if getattr(a, "streamer", None):
        os.environ["CLIPPER_STREAMER"] = a.streamer
    else:
        os.environ.pop("CLIPPER_STREAMER", None)
    a.f(a)


if __name__ == "__main__":
    main()
