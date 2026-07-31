"""
instagram_publisher.py — Instagram Reels & Posts Publisher

Supports official Meta Graph API and unofficial instagrapi (cookie-based private API).
Includes a dry-run mock mode.
"""

import os
import time
import logging
import json
import asyncio
from base_publisher import BasePublisher

logger = logging.getLogger("clipflow.publisher")

# Safe import for instagrapi
try:
    from instagrapi import Client as InstagrapiClient
    from instagrapi.exceptions import ChallengeRequired, TwoFactorRequired
    HAS_INSTAGRAPI = True
except ImportError:
    HAS_INSTAGRAPI = False


class InstagramPublisher(BasePublisher):
    """
    Instagram publisher supporting official Graph API and scraper mode (instagrapi).
    """

    def __init__(self, label: str, auth_mode: str = "api", credentials: dict = None, mock: bool = False):
        super().__init__(label, mock=mock)
        self.auth_mode = auth_mode
        self.credentials = credentials or {}

        if self.mock:
            logger.info(f"InstagramPublisher initialized in MOCK mode for '{self.label}'")
            return

        if self.auth_mode == "api":
            self.access_token = self.credentials.get("access_token")
            self.ig_account_id = self.credentials.get("instagram_account_id")
            if not all([self.access_token, self.ig_account_id]):
                raise ValueError("API credentials (access_token, instagram_account_id) missing for Instagram")
            logger.info(f"InstagramPublisher (Official Graph API) initialized for '{self.label}'")
            
        elif self.auth_mode == "cookie":
            if not HAS_INSTAGRAPI:
                raise ImportError("instagrapi is required for Instagram Cookie mode. Run: pip install instagrapi")
            
            self.username = self.credentials.get("username")
            self.password = self.credentials.get("password")
            if not all([self.username, self.password]):
                raise ValueError("Cookie credentials (username, password) missing for Instagram")
                
            self.cl = InstagrapiClient()
            
            # Load proxy and user-agent from account credentials
            proxy_url = self.credentials.get("proxy_url", "").strip()
            user_agent = self.credentials.get("user_agent", "").strip()
            if proxy_url:
                self.cl.set_proxy(proxy_url)
            if user_agent:
                self.cl.set_user_agent(user_agent)
                
            logger.info(f"InstagramPublisher (instagrapi) initialized for '{self.label}'")
        else:
            raise ValueError(f"Unknown auth_mode: {self.auth_mode}")

    async def upload_media(self, file_path: str) -> str:
        """
        Upload media to Instagram. Returns a media container ID (API mode) or file path reference (Cookie mode).
        """
        if self.mock:
            logger.info(f"MOCK: Instagram uploading media '{os.path.basename(file_path)}'")
            return "mock_ig_container_999"

        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Media file not found: {file_path}")

        logger.info(f"Instagram uploading: {os.path.basename(file_path)}")

        if self.auth_mode == "api":
            # Official API requires a publicly accessible URL.
            # In a local scenario, we'll log a warning and expect the URL to be handled,
            # or raise an error explaining the Graph API public URL requirement.
            raise NotImplementedError(
                "Official Instagram Graph API requires the video to be hosted on a public URL. "
                "For local files, please use Instagram Cookie Mode or Mock Mode."
            )
        elif self.auth_mode == "cookie":
            # For cookie mode, we return the path directly as upload/post are combined in instagrapi
            return file_path

    async def publish_post(self, text: str, media_id: str = None, **kwargs) -> dict:
        """
        Post media to Instagram feed or Reels.
        """
        if self.mock:
            import random
            post_id = f"ig_{random.randint(1000000000, 9999999999)}"
            post_url = f"https://instagram.com/p/{post_id}"
            logger.info(f"MOCK: Posted to Instagram: {post_url}")
            return {"id": post_id, "url": post_url}

        if self.auth_mode == "api":
            raise NotImplementedError("Official Instagram Graph API post not implemented.")
            
        elif self.auth_mode == "cookie":
            # Perform login and publish using instagrapi
            # Running synchronous instagrapi calls in thread pool to avoid blocking event loop
            return await asyncio.to_thread(self._post_cookie_mode, media_id, text)

    def _post_cookie_mode(self, file_path: str, caption: str) -> dict:
        """Helper to run instagrapi login and upload in a background thread."""
        try:
            # Try to load cached session
            session_settings = self.credentials.get("session_settings")
            if session_settings:
                try:
                    self.cl.set_settings(json.loads(session_settings))
                    self.cl.get_timeline_feed()  # Check if session is still valid
                    logger.info("Instagram: Reused cached session successfully")
                except Exception:
                    logger.info("Instagram: Cached session expired. Logging in via credentials...")
                    self._login_instagrapi()
            else:
                self._login_instagrapi()

            # Upload Media (Photo vs Reel/Video)
            ext = os.path.splitext(file_path)[1].lower()
            if ext in {".png", ".jpg", ".jpeg", ".webp"}:
                logger.info("Instagram: Uploading Photo...")
                media = self.cl.photo_upload(file_path, caption)
            else:
                logger.info("Instagram: Uploading Reel...")
                media = self.cl.clip_upload(file_path, caption)

            post_id = media.code
            post_url = f"https://instagram.com/p/{post_id}"
            
            logger.info(f"Instagram content posted: {post_url}")
            return {"id": post_id, "url": post_url}
        except Exception as e:
            logger.error(f"Instagram upload failed: {e}")
            raise

    def _login_instagrapi(self):
        """Helper for username/password login."""
        self.cl.login(self.username, self.password)
        try:
            import db
            settings = self.cl.get_settings()
            self.credentials["session_settings"] = json.dumps(settings)
            db.update_account_credentials(self.label, "instagram", self.credentials)
            logger.info("Instagram: Login success & session settings cached to database")
        except Exception as db_err:
            logger.error(f"Instagram: Failed to save updated session settings to DB: {db_err}")

    async def fetch_analytics(self, external_id: str) -> dict:
        """Fetch metrics for Instagram post."""
        if self.mock:
            import random
            return {
                "views": random.randint(50, 500),
                "likes": random.randint(5, 50),
                "shares": random.randint(1, 10),
                "comments": random.randint(0, 5)
            }
            
        if self.auth_mode == "cookie":
            return await asyncio.to_thread(self._fetch_analytics_cookie_mode, external_id)
            
        return {"views": 0, "likes": 0, "shares": 0, "comments": 0}

    def _fetch_analytics_cookie_mode(self, external_id: str) -> dict:
        try:
            # Extract media code robustly
            clean_url = external_id.rstrip('/')
            code = clean_url.split('/')[-1] if '/' in clean_url else clean_url
            media_id = self.cl.media_id(self.cl.media_pk_from_code(code))
            info = self.cl.media_info(media_id)
            return {
                "views": getattr(info, "view_count", 0) or getattr(info, "play_count", 0) or 0,
                "likes": getattr(info, "like_count", 0) or 0,
                "shares": 0,
                "comments": getattr(info, "comment_count", 0) or 0
            }
        except Exception as e:
            logger.warning(f"Failed to fetch Instagram analytics for {external_id}: {e}")
            return {"views": 0, "likes": 0, "shares": 0, "comments": 0}
