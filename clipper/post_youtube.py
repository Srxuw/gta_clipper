"""YouTube Shorts upload via the official Data API (OAuth, your own channel).

Setup: create a Google Cloud project, enable YouTube Data API v3, create an OAuth *desktop* client,
save it as client_secret.json. Public publication also requires the OAuth app and account to meet
YouTube verification and test-user requirements.
"""
import os

SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]


def upload(path, title, description, tags=None, secrets="client_secret.json", token="yt_token.json",
           privacy="public"):
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    creds = Credentials.from_authorized_user_file(token, SCOPES) if os.path.exists(token) else None
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            creds = InstalledAppFlow.from_client_secrets_file(secrets, SCOPES).run_local_server(port=0)
        with open(token, "w") as f:
            f.write(creds.to_json())
    yt = build("youtube", "v3", credentials=creds)
    if privacy not in ("public", "unlisted", "private"):
        raise ValueError("YouTube privacy must be public, unlisted, or private")
    body = {"snippet": {"title": title[:100], "description": description, "tags": tags or [], "categoryId": "20"},
            "status": {"privacyStatus": privacy, "selfDeclaredMadeForKids": False}}
    req = yt.videos().insert(part="snippet,status", body=body,
                             media_body=MediaFileUpload(path, chunksize=-1, resumable=True))
    resp = None
    while resp is None:
        _, resp = req.next_chunk()
    actual_privacy = resp.get("status", {}).get("privacyStatus")
    if actual_privacy != privacy:
        raise RuntimeError(
            f"YouTube returned privacyStatus={actual_privacy or 'unknown'} instead of {privacy}"
        )
    return f"https://youtube.com/shorts/{resp['id']}"
