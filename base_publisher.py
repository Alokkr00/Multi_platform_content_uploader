"""
base_publisher.py — Abstract Base Publisher & Mock Publisher

Defines the interface for all platform-specific publishers and provides a dry-run mock publisher.
"""

import os
import time
import logging
from abc import ABC, abstractmethod

logger = logging.getLogger("x_automation.publisher")


class BasePublisher(ABC):
    """Abstract base class for all social media publishers."""
    
    def __init__(self, label: str, mock: bool = False, **kwargs):
        self.label = label
        self.mock = mock or os.getenv("MOCK_POSTING", "false").lower() == "true"

    @abstractmethod
    async def upload_media(self, file_path: str) -> str:
        """Upload media to the platform. Returns a unique media ID/reference."""
        pass

    @abstractmethod
    async def post_tweet(self, text: str, media_id: str = None, **kwargs) -> dict:
        """
        Publish post content to the platform. Returns dict with 'id' and 'url'.
        (Kept as 'post_tweet' name for backward-compatibility with scheduler/server code).
        """
        pass


    @abstractmethod
    async def fetch_analytics(self, external_id: str) -> dict:
        """
        Fetch metrics/analytics for a posted item.
        Returns a dict: {"views": int, "likes": int, "shares": int, "comments": int}
        """
        pass


class MockPublisher(BasePublisher):
    """Mock publisher for dry-runs and integration testing."""
    
    def __init__(self, label: str, platform: str = "mock", mock: bool = True, **kwargs):
        super().__init__(label, mock=True, **kwargs)
        self.platform = platform
        logger.info(f"MockPublisher ({self.platform}) initialized for account '{self.label}'")

    async def upload_media(self, file_path: str) -> str:
        filename = os.path.basename(file_path)
        logger.info(f"[MOCK-{self.platform.upper()}] Uploading media: {filename}")
        return f"mock_media_id_{self.platform}_{int(time.time())}"

    async def post_tweet(self, text: str, media_id: str = None, **kwargs) -> dict:
        import random
        post_id = str(random.randint(100000000000000000, 999999999999999999))
        post_url = f"https://{self.platform}.com/mock_user/status/{post_id}"
        
        logger.info(f"[MOCK-{self.platform.upper()}] Published content: {text[:60]}... URL: {post_url}")
        
        return {
            "id": post_id,
            "url": post_url
        }

    async def fetch_analytics(self, external_id: str) -> dict:
        import random
        return {
            "views": random.randint(50, 500),
            "likes": random.randint(5, 50),
            "shares": random.randint(1, 10),
            "comments": random.randint(0, 5)
        }
