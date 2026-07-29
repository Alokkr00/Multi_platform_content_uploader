"""
youtube_publisher.py — Official YouTube Shorts Publisher via YouTube Data API v3

Publishes video Shorts to YouTube using official OAuth 2.0 refresh token authentication.
Includes chunked resumable upload, token auto-refresh, Shorts auto-tagging, and mock mode support.
"""

import os
import json
import time
import logging
import asyncio
import urllib.request
import urllib.parse
import urllib.error

from base_publisher import BasePublisher
from db import increment_post_count

logger = logging.getLogger("x_automation.youtube_publisher")

OAUTH_TOKEN_URL = "https://oauth2.googleapis.com/token"
YOUTUBE_UPLOAD_URL = "https://www.googleapis.com/upload/youtube/v3/videos"
YOUTUBE_API_URL = "https://www.googleapis.com/youtube/v3/videos"


class YouTubePublisher(BasePublisher):
    """
    Official YouTube Shorts publisher supporting OAuth 2.0 credential refresh,
    resumable video upload, and dry-run mock mode.
    """

    def __init__(self, label: str = "youtube_account", client_id: str = None, client_secret: str = None,
                 refresh_token: str = None, mock_posting: bool = False, **kwargs):
        super().__init__(label, mock=mock_posting or kwargs.get("mock", False))
        self.client_id = client_id
        self.client_secret = client_secret
        self.refresh_token = refresh_token
        self.access_token = None
        self.token_expiry = 0

        if self.mock:
            logger.info(f"YouTubePublisher initialized in MOCK mode for '{self.label}'")
            return

        if not all([self.client_id, self.client_secret, self.refresh_token]):
            raise ValueError("YouTube OAuth missing client_id, client_secret, or refresh_token")

        logger.info(f"YouTubePublisher initialized for '{self.label}' (OAuth)")

    async def _ensure_access_token(self) -> str:
        """
        Verify access_token validity or fetch a fresh token using refresh_token.
        """
        if self.mock:
            return "mock_access_token"

        now = time.time()
        if self.access_token and now < (self.token_expiry - 60):
            return self.access_token

        logger.info(f"Refreshing YouTube OAuth access token for account '{self.label}'...")
        payload = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": self.refresh_token,
            "grant_type": "refresh_token"
        }).encode("utf-8")

        req = urllib.request.Request(
            OAUTH_TOKEN_URL,
            data=payload,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST"
        )

        def _do_token_request():
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    return data
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8")
                logger.error(f"YouTube OAuth token refresh failed ({e.code}): {body}")
                if "quota" in body.lower():
                    raise RuntimeError("YouTube API daily quota exceeded.") from e
                elif "invalid_grant" in body.lower():
                    raise ValueError("YouTube refresh token expired or revoked. Please re-authenticate.") from e
                raise RuntimeError(f"YouTube token refresh failed: {e.reason}") from e

        data = await asyncio.to_thread(_do_token_request)
        self.access_token = data.get("access_token")
        expires_in = int(data.get("expires_in", 3600))
        self.token_expiry = now + expires_in
        logger.info(f"YouTube access token refreshed successfully for '{self.label}' (valid for {expires_in}s)")

        # Persist refreshed token back to DB
        try:
            from db import update_account_credentials
            new_creds = {
                "client_id": self.client_id,
                "client_secret": self.client_secret,
                "refresh_token": self.refresh_token,
                "access_token": self.access_token,
                "token_expiry": self.token_expiry
            }
            await asyncio.to_thread(update_account_credentials, self.label, "youtube", new_creds)
        except Exception as pe:
            logger.warning(f"Failed to persist refreshed token for '{self.label}': {pe}")

        return self.access_token

    async def upload_media(self, file_path: str) -> str:
        """
        Upload video file to YouTube as a Short using true chunked resumable upload protocol.
        Sends video in 5MB chunks with Content-Range headers. Returns created YouTube Video ID.
        """
        if self.mock:
            filename = os.path.basename(file_path)
            logger.info(f"[MOCK-YOUTUBE] Uploading Short: {filename}")
            return f"mock_yt_id_{int(time.time())}"

        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Video file not found for upload: {file_path}")

        token = await self._ensure_access_token()
        file_size = os.path.getsize(file_path)

        # Pre-upload snippet metadata
        snippet_data = {
            "snippet": {
                "title": f"Uploaded Short {int(time.time())} #Shorts",
                "description": "Uploaded via Content Uploader #Shorts",
                "categoryId": "22"  # People & Blogs
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }

        body_bytes = json.dumps(snippet_data).encode("utf-8")
        params = urllib.parse.urlencode({"uploadType": "resumable", "part": "snippet,status"})
        init_url = f"{YOUTUBE_UPLOAD_URL}?{params}"

        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json; charset=UTF-8",
            "X-Upload-Content-Length": str(file_size),
            "X-Upload-Content-Type": "video/mp4"
        }

        req = urllib.request.Request(init_url, data=body_bytes, headers=headers, method="POST")

        def _init_upload():
            with urllib.request.urlopen(req, timeout=20) as resp:
                upload_location = resp.headers.get("Location")
                if not upload_location:
                    raise RuntimeError("YouTube resumable upload did not return Location header")
                return upload_location

        upload_location = await asyncio.to_thread(_init_upload)
        logger.info(f"Resumable upload session initiated for '{os.path.basename(file_path)}' ({file_size / (1024*1024):.1f} MB)")

        # Stream binary data in 5MB chunks (multiple of 256KB required by YouTube)
        CHUNK_SIZE = 5 * 1024 * 1024  # 5 MB

        def _stream_video_chunks():
            with open(file_path, "rb") as f:
                start_byte = 0
                while start_byte < file_size:
                    chunk = f.read(CHUNK_SIZE)
                    chunk_len = len(chunk)
                    end_byte = start_byte + chunk_len - 1

                    content_range = f"bytes {start_byte}-{end_byte}/{file_size}"
                    logger.info(f"Uploading chunk ({start_byte}-{end_byte}/{file_size} bytes)...")

                    chunk_req = urllib.request.Request(
                        upload_location,
                        data=chunk,
                        headers={
                            "Content-Length": str(chunk_len),
                            "Content-Type": "video/mp4",
                            "Content-Range": content_range
                        },
                        method="PUT"
                    )

                    try:
                        with urllib.request.urlopen(chunk_req, timeout=120) as resp:
                            body = resp.read().decode("utf-8")
                            if body:
                                return json.loads(body)
                    except urllib.error.HTTPError as e:
                        if e.code == 308:
                            # 308 Resume Incomplete (expected for intermediate chunks)
                            start_byte = end_byte + 1
                            continue
                        else:
                            err_body = e.read().decode("utf-8")
                            logger.error(f"YouTube chunk upload failed ({e.code}): {err_body}")
                            raise RuntimeError(f"YouTube chunk upload error: {err_body}") from e

            raise RuntimeError("YouTube video upload ended without final response payload")

        res_json = await asyncio.to_thread(_stream_video_chunks)
        video_id = res_json.get("id")
        if not video_id:
            raise RuntimeError(f"YouTube upload succeeded but no video ID returned: {res_json}")

        logger.info(f"Video uploaded to YouTube successfully in chunks! Video ID: {video_id}")
        return video_id

    async def post_tweet(self, text: str, media_id: str = None, **kwargs) -> dict:
        """
        Finalize and publish YouTube Short metadata.
        (Named post_tweet for BasePublisher compatibility).
        """
        if self.mock:
            video_id = media_id or f"mock_yt_id_{int(time.time())}"
            short_url = f"https://youtube.com/shorts/{video_id}"
            logger.info(f"[MOCK-YOUTUBE] Published Short: {text[:60]}... URL: {short_url}")
            return {"id": video_id, "url": short_url}

        token = await self._ensure_access_token()
        video_id = media_id

        if not video_id:
            raise ValueError("YouTube Shorts publishing requires a video file upload")

        # Auto-append #Shorts if missing
        final_text = text or "YouTube Short #Shorts"
        if "#shorts" not in final_text.lower():
            final_text = f"{final_text.strip()} #Shorts"

        lines = final_text.split("\n", 1)
        title = lines[0][:100]  # Max 100 chars title
        desc = lines[1] if len(lines) > 1 else final_text

        update_payload = {
            "id": video_id,
            "snippet": {
                "title": title,
                "description": desc,
                "categoryId": "22"
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        }

        body_bytes = json.dumps(update_payload).encode("utf-8")
        url = f"{YOUTUBE_API_URL}?part=snippet,status"
        headers = {
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json"
        }

        req = urllib.request.Request(url, data=body_bytes, headers=headers, method="PUT")

        def _update_video():
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as e:
                err_body = e.read().decode("utf-8")
                logger.error(f"YouTube video metadata update failed ({e.code}): {err_body}")
                raise RuntimeError(f"YouTube metadata update failed: {err_body}") from e

        await asyncio.to_thread(_update_video)
        await asyncio.to_thread(increment_post_count, self.label, "youtube")

        short_url = f"https://youtube.com/shorts/{video_id}"
        logger.info(f"YouTube Short metadata updated & published: {short_url}")

        return {
            "id": video_id,
            "url": short_url
        }

    async def fetch_analytics(self, external_id: str) -> dict:
        """
        Fetch view, like, and comment counts for a YouTube Short video ID.
        """
        if self.mock:
            import random
            return {
                "views": random.randint(100, 2000),
                "likes": random.randint(10, 200),
                "shares": random.randint(2, 20),
                "comments": random.randint(1, 30)
            }

        token = await self._ensure_access_token()
        params = urllib.parse.urlencode({"part": "statistics", "id": external_id})
        url = f"{YOUTUBE_API_URL}?{params}"

        headers = {"Authorization": f"Bearer {token}"}
        req = urllib.request.Request(url, headers=headers, method="GET")

        def _get_stats():
            try:
                with urllib.request.urlopen(req, timeout=15) as resp:
                    data = json.loads(resp.read().decode("utf-8"))
                    items = data.get("items", [])
                    if not items:
                        return {"views": 0, "likes": 0, "shares": 0, "comments": 0}
                    stats = items[0].get("statistics", {})
                    return {
                        "views": int(stats.get("viewCount", 0)),
                        "likes": int(stats.get("likeCount", 0)),
                        "shares": 0,  # YouTube API v3 does not expose share count directly
                        "comments": int(stats.get("commentCount", 0))
                    }
            except Exception as e:
                logger.warning(f"Failed to fetch YouTube analytics for video {external_id}: {e}")
                return {"views": 0, "likes": 0, "shares": 0, "comments": 0}

        return await asyncio.to_thread(_get_stats)
