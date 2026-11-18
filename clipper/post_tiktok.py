"""TikTok posting via the official Content Posting API.

IMPORTANT (from TikTok's docs): until your app passes TikTok's audit, every post made through
the API is forced to private (SELF_ONLY), even though the calls all "succeed". Two modes:
  direct : publishes to your profile (private until audited)
  inbox  : drops the video in your TikTok inbox as a draft; you tap Post in the app
           (lower approval bar - needs the video.upload scope)

Setup: register an app at developers.tiktok.com, add the Content Posting API, add your redirect URI,
then export TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET and run `python3 -m clipper auth-tiktok`.
"""
import json
import os
import time
import urllib.parse

import requests

BASE = os.environ.get("TIKTOK_API_BASE", "https://open.tiktokapis.com")
TOKEN_FILE = "tiktok_token.json"
SCOPES = {"direct": "user.info.basic,video.publish", "inbox": "user.info.basic,video.upload"}


def _keys():
    try:
        return os.environ["TIKTOK_CLIENT_KEY"], os.environ["TIKTOK_CLIENT_SECRET"]
    except KeyError as exc:
        raise RuntimeError(
            "Set TIKTOK_CLIENT_KEY and TIKTOK_CLIENT_SECRET environment variables "
            "before running TikTok authorization"
        ) from exc


def auth_url(redirect_uri, mode="direct"):
    key, _ = _keys()
    q = urllib.parse.urlencode({"client_key": key, "scope": SCOPES[mode], "response_type": "code",
                                "redirect_uri": redirect_uri, "state": "clipper"})
    return f"https://www.tiktok.com/v2/auth/authorize/?{q}"


def _token_request(data):
    r = requests.post(f"{BASE}/v2/oauth/token/", data=data,
                      headers={"Content-Type": "application/x-www-form-urlencoded"}, timeout=30)
    j = r.json()
    if "access_token" not in j:
        raise RuntimeError(f"token error: {j}")
    j["obtained_at"] = int(time.time())
    with open(TOKEN_FILE, "w") as f:
        json.dump(j, f)
    return j


def exchange_code(code, redirect_uri):
    key, secret = _keys()
    return _token_request({"client_key": key, "client_secret": secret, "code": urllib.parse.unquote(code),
                           "grant_type": "authorization_code", "redirect_uri": redirect_uri})


def access_token():
    with open(TOKEN_FILE) as f:
        t = json.load(f)
    if time.time() > t["obtained_at"] + t.get("expires_in", 86400) - 300:
        key, secret = _keys()
        t = _token_request({"client_key": key, "client_secret": secret,
                            "grant_type": "refresh_token", "refresh_token": t["refresh_token"]})
    return t["access_token"]


def _hdr(tok):
    return {"Authorization": "Bearer " + tok, "Content-Type": "application/json; charset=UTF-8"}


def upload(path, title, mode="direct", privacy_pref="PUBLIC_TO_EVERYONE", paid_partnership=False, poll_s=300):
    tok = access_token()
    size = os.path.getsize(path)
    if size > 64 * 1024 * 1024:
        raise RuntimeError("clip >64MB: chunked upload not implemented (clips from this tool are ~10MB)")
    source = {"source": "FILE_UPLOAD", "video_size": size, "chunk_size": size, "total_chunk_count": 1}

    if mode == "direct":
        ci = requests.post(f"{BASE}/v2/post/publish/creator_info/query/", headers=_hdr(tok), timeout=30).json()
        options = ci.get("data", {}).get("privacy_level_options", ["SELF_ONLY"])
        privacy = privacy_pref if privacy_pref in options else options[0]
        post_info = {"title": title[:2200], "privacy_level": privacy, "disable_comment": False}
        if paid_partnership:
            post_info["brand_content_toggle"] = True
        body = {"post_info": post_info, "source_info": source}
        endpoint = "/v2/post/publish/video/init/"
    else:
        privacy = None
        body = {"source_info": source}
        endpoint = "/v2/post/publish/inbox/video/init/"

    init = requests.post(f"{BASE}{endpoint}", headers=_hdr(tok), json=body, timeout=30).json()
    if init.get("error", {}).get("code") != "ok":
        raise RuntimeError(f"init failed: {init.get('error')}")
    publish_id, upload_url = init["data"]["publish_id"], init["data"]["upload_url"]

    with open(path, "rb") as f:
        put = requests.put(upload_url, data=f.read(), timeout=300,
                           headers={"Content-Type": "video/mp4", "Content-Range": f"bytes 0-{size - 1}/{size}"})
    if put.status_code not in (200, 201, 206):
        raise RuntimeError(f"upload failed: {put.status_code} {put.text[:200]}")

    deadline = time.time() + poll_s
    while time.time() < deadline:
        st = requests.post(f"{BASE}/v2/post/publish/status/fetch/", headers=_hdr(tok),
                           json={"publish_id": publish_id}, timeout=30).json().get("data", {})
        s = st.get("status")
        if s == "PUBLISH_COMPLETE" or (mode == "inbox" and s == "SEND_TO_USER_INBOX"):
            return {"publish_id": publish_id, "privacy": privacy, "status": s,
                    "post_ids": st.get("publicaly_available_post_id", [])}
        if s == "FAILED":
            raise RuntimeError(f"publish failed: {st.get('fail_reason')}")
        time.sleep(5)
    raise RuntimeError(f"timed out waiting for TikTok (publish_id={publish_id})")
