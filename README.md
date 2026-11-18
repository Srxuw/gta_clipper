# GTA 6 clipper

## Streamer profiles

Set `streamer_name` in `config.json`, or select a profile for one command:

```powershell
python -m clipper -s lacy process inbox\video.mp4
python -m clipper -s yourrage url "TWITCH_OR_APPROVED_URL"
python -m clipper -s clix watch --once
```

Built-in profiles add the correct name and required hashtag:

- `lacy`: `LACY` and `#LACY`
- `yourrage`: `YourRAGE` and `#yourrage`
- `clix`: `Clix` and `#Clix`

For another streamer, add a profile to `streamer_profiles` or use `-s CustomName`.
The custom mode uses `#CustomName` plus `#GTA6` as a default and preserves the
campaign disclosure and account settings from `config.json`.

## Public app information

- [Terms of Service](./TERMS.md)
- [Privacy Policy](./PRIVACY.md)

Finds the loudest reaction moments in a stream/VOD, renders them as 1080x1920 clips with a hook
and optional burned-in captions, and queues them for YOUR accounts (one per platform).

## Setup
1. `pip install -r requirements.txt` and make sure `ffmpeg` is installed.
2. `cp config.example.json config.json` and edit it:
   - `launch_time_local`: set the actual launch time in your timezone (the date is Nov 19, 2026).
   - `campaign`: paste the campaign's real rules, required hashtags, and disclosure text.
   - `render.font`: path to a bold font on your machine (Windows paths with `:` need escaping).
   - `accounts`: your own accounts. One per platform.
3. Join the campaign (Whop Content Rewards, etc.) yourself and read its brief. Only drop footage
   into `inbox/` that the brief or the creator allows you to clip.

## Use
    python3 -m clipper run              # wait for launch, then watch inbox/, schedule, post
    python3 -m clipper process FILE     # clip one video now
    python3 -m clipper url LINK [--start 1:02:00 --end 1:30:00]   # download a source you're allowed to clip, then clip it
    python3 -m clipper watch --once     # process everything in inbox/ once
    python3 -m clipper status           # see the queue
    python3 -m clipper mark ID URL      # record the live URL after a manual upload

`submissions.csv` collects posted URLs so you can submit them to the campaign.

## Auto-posting setup (do this BEFORE launch)
- **YouTube:** create a Google Cloud project, enable YouTube Data API v3, and make a *desktop app* OAuth
  client (not a web application). Download the JSON and save it as `client_secret.json`. No redirect URI
  needs to be entered manually for the loopback redirect used by `auth-youtube`; Google generates a
  temporary localhost redirect. Add the YouTube account as a test user while the OAuth consent screen is
  in Testing, then run `python3 -m clipper auth-youtube`. Set `"youtube.privacy"` to `"public"` in
  `config.json`. Public publication still depends on valid account and API verification status.
- **TikTok:** register an app at developers.tiktok.com, add the Content Posting API (Direct Post) and a
  redirect URI, `export TIKTOK_CLIENT_KEY=... TIKTOK_CLIENT_SECRET=...`, put the redirect URI in
  `config.json`, run `python3 -m clipper auth-tiktok`. Per TikTok's docs, **posts from an unaudited app are
  always private** (queue status shows `posted_self_only`). Set `"mode": "inbox"` to drop clips into your
  TikTok inbox as drafts instead, and tap Post in the app. That works publicly right away.
- Private posts are not logged to `submissions.csv`, since campaigns can't count them.

### TikTok public Direct Posts
- Sandbox Direct Posts are test posts and are expected to remain private. Add the TikTok account under
  Sandbox test users, then keep `"mode": "direct"` only after `video.publish` is approved.
- Register `http://localhost:8080/callback` exactly as the redirect URI if TikTok supports that callback
  for this app type. Otherwise use the exact HTTPS redirect URI registered in the developer dashboard.
- Set credentials per terminal, never in `config.json`:

  ```powershell
  $env:TIKTOK_CLIENT_KEY="your-client-key"
  $env:TIKTOK_CLIENT_SECRET="your-client-secret"
  python -m clipper auth-tiktok
  ```

- Public posting requires a completed TikTok app audit and `video.publish` production approval. No app
  setting or `"privacy": "PUBLIC_TO_EVERYONE"` request can make unaudited API posts public.

## Clip quality and safety
- Each source uses audio-energy peak detection, 1080p gameplay, a blurred 9:16 fill, and readable burned-in subtitles.
- Audio is normalized and filtered for consistent levels.
- Nudity detections are blurred around the detected time range.
- Configured swear words are bleeps in the default clip and a separate `_unbleeped.mp4` copy is kept when enabled.
- Set `"render.delete_source_after_clips"` to `true` for automatic inbox cleanup, or use
  `python3 -m clipper cleanup-source inbox/FILE.mp4 --force` after reviewing generated clips.

## What's tested vs not
- Tested here on synthetic video: detection, 9:16 render, hook text, scheduling.
- Tested against a local mock of TikTok's API: auth, creator-info, init, upload, status, private fallback.
- NOT tested: captions (needs a Whisper model download), YouTube upload, the real TikTok API, real GTA 6 streams.
- YouTube uploads from unverified API projects are forced private until Google audits the project.
- Instagram: the official API needs a business account, so those items are marked `manual`.
- Detection is a loudness heuristic. Review clips before posting, and follow the campaign's
  rules (no leaked footage, no other tool's watermark, paid-partnership label turned on in-app).
