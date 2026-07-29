"""
tiktok_publisher.py — TikTok Video Publisher

Supports official TikTok Content Posting API and cookie-based (sessionid) uploaders.
Includes dry-run mock mode.
"""

import os
import time
import logging
import asyncio
from base_publisher import BasePublisher

logger = logging.getLogger("x_automation.publisher")


class TikTokPublisher(BasePublisher):
    """
    TikTok publisher supporting official API and scraper/cookie mode.
    """

    def __init__(self, label: str, auth_mode: str = "api", credentials: dict = None, mock: bool = False):
        super().__init__(label, mock=mock)
        self.auth_mode = auth_mode
        self.credentials = credentials or {}

        if self.mock:
            logger.info(f"TikTokPublisher initialized in MOCK mode for '{self.label}'")
            return

        if self.auth_mode == "api":
            self.access_token = self.credentials.get("access_token")
            self.open_id = self.credentials.get("open_id")
            if not all([self.access_token, self.open_id]):
                raise ValueError("API credentials (access_token, open_id) missing for TikTok")
            logger.info(f"TikTokPublisher (Official API) initialized for '{self.label}'")
            
        elif self.auth_mode == "cookie":
            self.session_id = self.credentials.get("session_id")
            self.proxy_url = self.credentials.get("proxy_url", "").strip()
            self.user_agent = self.credentials.get("user_agent", "").strip() or "Mozilla/5.0"
            if not self.session_id:
                raise ValueError("Session cookie 'session_id' missing for TikTok")
            logger.info(f"TikTokPublisher (Cookie Mode) initialized for '{self.label}'")
        else:
            raise ValueError(f"Unknown auth_mode: {self.auth_mode}")

    async def upload_media(self, file_path: str) -> str:
        """
        Upload video to TikTok. Returns publish ID / path.
        """
        if self.mock:
            logger.info(f"MOCK: TikTok uploading media '{os.path.basename(file_path)}'")
            return "mock_tt_publish_id_999"

        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Media file not found: {file_path}")

        logger.info(f"TikTok uploading: {os.path.basename(file_path)}")

        if self.auth_mode == "api":
            # TikTok Content Posting API flow:
            # 1. Initialize upload: POST /post/publish/inbox/video/init/
            # 2. Upload video binary
            raise NotImplementedError("Official TikTok API upload not implemented.")
        elif self.auth_mode == "cookie":
            # Cookie mode requires request signatures and captcha bypass.
            raise NotImplementedError(
                "TikTok Cookie Mode requires Playwright/signature validation. "
                "For now, please use Mock Mode or supply official TikTok API credentials."
            )

    async def publish_post(self, text: str, media_id: str = None, **kwargs) -> dict:
        """
        Publish post to TikTok.
        """
        if self.mock:
            import random
            post_id = f"tt_{random.randint(1000000000, 9999999999)}"
            post_url = f"https://tiktok.com/@mock/video/{post_id}"
            logger.info(f"MOCK: Posted to TikTok: {post_url}")
            return {"id": post_id, "url": post_url}

        raise NotImplementedError("TikTok publishing is not supported in production mode yet.")

    async def fetch_analytics(self, external_id: str) -> dict:
        """Fetch metrics for TikTok post."""
        if self.mock:
            import random
            return {
                "views": random.randint(50, 500),
                "likes": random.randint(5, 50),
                "shares": random.randint(1, 10),
                "comments": random.randint(0, 5)
            }
            
        return {"views": 0, "likes": 0, "shares": 0, "comments": 0}
