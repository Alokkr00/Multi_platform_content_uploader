"""
publisher.py — X (Twitter) Publisher with Chunked Video Upload & Scraper Support

Publishes tweets with media to X using either official Tweepy API or Twikit scraper mode.
Supports mock posting mode for E2E cycle verification without credentials.
"""

import os
import re
import time
import logging
import mimetypes
import asyncio

import tweepy
import twikit

from db import increment_post_count, get_setting
from downloader import download_video, transcode_for_x, cleanup, TEMP_DIR
from base_publisher import BasePublisher, MockPublisher

logger = logging.getLogger("clipflow.publisher")

# ── Constants ─────────────────────────────────────────────────────────

DAILY_POST_LIMIT = 50
DAILY_POST_WARNING_THRESHOLD = 40

# Media type detection
VIDEO_EXTENSIONS = {".mp4", ".mov", ".avi", ".mkv", ".webm", ".flv"}
IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".webp"}


# ── XPublisher Class ─────────────────────────────────────────────────

class XPublisher(BasePublisher):
    """
    X (Twitter) publisher supporting official API (Tweepy), scraper mode (Twikit),
    and a dry-run mock mode.
    """

    def __init__(self, label: str = "x_account", auth_mode: str = "api", api_key: str = None, api_secret: str = None,
                 access_token: str = None, access_token_secret: str = None,
                 cookie_auth_token: str = None, cookie_ct0: str = None,
                 proxy_url: str = None, user_agent: str = None,
                 mock_posting: bool = False):
        """
        Initialize XPublisher.
        """
        super().__init__(label, mock=mock_posting)
        self.auth_mode = auth_mode

        if self.mock:
            logger.info(f"XPublisher initialized in MOCK mode for '{self.label}'")
            return

        if self.auth_mode == "api":
            if not all([api_key, api_secret, access_token, access_token_secret]):
                raise ValueError("API credentials missing for official mode")
            # v1.1 API (for media uploads)
            auth = tweepy.OAuth1UserHandler(
                api_key, api_secret,
                access_token, access_token_secret
            )
            self.api = tweepy.API(auth, wait_on_rate_limit=True)

            # v2 Client (for creating tweets)
            self.client = tweepy.Client(
                consumer_key=api_key,
                consumer_secret=api_secret,
                access_token=access_token,
                access_token_secret=access_token_secret,
                wait_on_rate_limit=True,
            )
            logger.info("XPublisher (Tweepy API) initialized successfully")
        elif self.auth_mode == "cookie":
            if not cookie_auth_token:
                raise ValueError("Session cookie 'auth_token' missing for scraper mode")
            
            # Fetch proxy and User-Agent from account parameters first, fallback to settings
            proxy_url = proxy_url.strip() if proxy_url else get_setting("proxy_url", "").strip()
            user_agent = user_agent.strip() if user_agent else get_setting("user_agent", "").strip()
            
            self.twikit_client = twikit.Client(
                "en-US",
                proxy=proxy_url if proxy_url else None,
                user_agent=user_agent if user_agent else None
            )
            
            # Build cookie dict
            cookies = {"auth_token": cookie_auth_token}
            if cookie_ct0:
                cookies["ct0"] = cookie_ct0
                
            self.twikit_client.set_cookies(cookies)
            logger.info("XPublisher (Twikit Scraper) initialized successfully")
        else:
            raise ValueError(f"Unknown auth_mode: {self.auth_mode}")

    async def upload_media(self, file_path: str) -> str:
        """
        Upload media to X. Returns media ID.
        """
        if self.mock:
            logger.info(f"MOCK: Uploading media '{os.path.basename(file_path)}'")
            return "mock_media_id_9999"

        if not file_path:
            logger.info("No media file path provided. Skipping media upload for text-only post.")
            return None

        if not os.path.isfile(file_path):
            raise FileNotFoundError(f"Media file not found: {file_path}")

        ext = os.path.splitext(file_path)[1].lower()
        file_size_mb = os.path.getsize(file_path) / (1024 * 1024)


        if self.auth_mode == "api":
            if ext in VIDEO_EXTENSIONS:
                media = await asyncio.to_thread(self._chunked_upload_video, file_path)
            elif ext in IMAGE_EXTENSIONS:
                media = await asyncio.to_thread(self.api.media_upload, filename=file_path)
                logger.info(f"Image uploaded: media_id={media.media_id}")
            else:
                media = await asyncio.to_thread(self._chunked_upload_video, file_path)
            return str(media.media_id)
        elif self.auth_mode == "cookie":
            cat = "tweet_video" if ext in VIDEO_EXTENSIONS else "tweet_image"
            media_id = await self.twikit_client.upload_media(
                source=file_path,
                wait_for_completion=True,
                media_category=cat
            )
            logger.info(f"Twikit media upload complete: media_id={media_id}")
            return media_id

    def _chunked_upload_video(self, file_path: str) -> tweepy.Media:
        """
        Perform chunked video upload via Tweepy.
        """
        file_size = os.path.getsize(file_path)
        file_size_mb = file_size / (1024 * 1024)

        mime_type, _ = mimetypes.guess_type(file_path)
        if not mime_type:
            mime_type = "video/mp4"

        logger.info(f"Starting chunked upload: {file_size_mb:.1f} MB, type={mime_type}")

        media = self.api.chunked_upload(
            filename=file_path,
            file_type=mime_type,
            wait_for_async_finalize=True,
            media_category="tweet_video",
        )

        logger.info(f"Chunked upload complete: media_id={media.media_id}")
        return media

    async def publish_post(self, text: str, media_id: str = None, reply_link: str = None, **kwargs) -> dict:
        """
        Publish a post to X, optionally with media and a threaded auto-reply link.
        """
        if self.mock:
            import random
            tweet_id = str(random.randint(100000000000000000, 999999999999999999))
            tweet_url = f"https://x.com/mock_user/status/{tweet_id}"
            logger.info(f"MOCK: Posted tweet: {tweet_url}")
            if reply_link:
                reply_id = str(random.randint(100000000000000000, 999999999999999999))
                logger.info(f"MOCK: Threaded reply posted: https://x.com/mock_user/status/{reply_id} (replying to {tweet_id} with link: {reply_link})")
            return {
                "id": tweet_id,
                "url": tweet_url,
            }

        effective_text = (text or "").strip()
        if not effective_text:
            if reply_link:
                effective_text = reply_link
                reply_link = None
            elif not media_id:
                raise ValueError("Cannot post tweet: missing required tweet text or media.")

        logger.info(f"Posting tweet ({len(effective_text)} chars, media={'yes' if media_id else 'no'})")

        if self.auth_mode == "api":
            api_kwargs = {}
            if effective_text:
                api_kwargs["text"] = effective_text
            if media_id:
                api_kwargs["media_ids"] = [int(media_id)]
            response = await asyncio.to_thread(self.client.create_tweet, **api_kwargs)
            tweet_id = str(response.data["id"])
            tweet_url = f"https://x.com/i/status/{tweet_id}"
            
            # Post threaded reply if reply_link is provided
            if reply_link:
                try:
                    logger.info(f"Posting threaded reply to tweet {tweet_id} with link: {reply_link}")
                    reply_resp = await asyncio.to_thread(
                        self.client.create_tweet,
                        text=reply_link,
                        in_reply_to_tweet_id=int(tweet_id)
                    )
                    logger.info(f"Threaded reply posted: {reply_resp.data['id']}")
                except Exception as ex:
                    logger.error(f"Failed to post threaded reply: {ex}")
        elif self.auth_mode == "cookie":
            media_ids = [media_id] if media_id else None
            twikit_text = effective_text if effective_text else None
            tweet = await self.twikit_client.create_tweet(text=twikit_text, media_ids=media_ids)
            tweet_id = str(tweet.id)
            tweet_url = f"https://x.com/i/status/{tweet_id}"
            
            # Post threaded reply if reply_link is provided
            if reply_link:
                try:
                    logger.info(f"Posting Twikit threaded reply to tweet {tweet_id} with link: {reply_link}")
                    reply_tweet = await self.twikit_client.create_tweet(text=reply_link, reply_to=tweet_id)
                    logger.info(f"Twikit threaded reply posted: {reply_tweet.id}")
                except Exception as ex:
                    logger.error(f"Failed to post Twikit threaded reply: {ex}")

        logger.info(f"Tweet posted: {tweet_url}")
        return {
            "id": tweet_id,
            "url": tweet_url,
        }

    async def fetch_analytics(self, external_id: str) -> dict:
        """Fetch metrics for a tweet."""
        if self.mock:
            import random
            return {
                "views": random.randint(50, 500),
                "likes": random.randint(5, 50),
                "shares": random.randint(1, 10),
                "comments": random.randint(0, 5)
            }

        tweet_id = None
        if external_id:
            match = re.search(r"/status/(\d+)", external_id)
            if match:
                tweet_id = match.group(1)
            elif external_id.isdigit():
                tweet_id = external_id
                
        if not tweet_id:
            return {"views": 0, "likes": 0, "shares": 0, "comments": 0}

        try:
            if self.auth_mode == "api":
                response = await asyncio.to_thread(
                    self.client.get_tweet,
                    id=int(tweet_id),
                    tweet_fields=["public_metrics"]
                )
                if response and response.data:
                    metrics = response.data.get("public_metrics", {})
                    return {
                        "views": metrics.get("impression_count", 0),
                        "likes": metrics.get("like_count", 0),
                        "shares": metrics.get("retweet_count", 0) + metrics.get("quote_count", 0),
                        "comments": metrics.get("reply_count", 0)
                    }
            elif self.auth_mode == "cookie":
                tweet = await self.twikit_client.get_tweet_by_id(tweet_id)
                if tweet:
                    return {
                        "views": getattr(tweet, "view_count", 0) or 0,
                        "likes": getattr(tweet, "favorite_count", 0) or 0,
                        "shares": getattr(tweet, "retweet_count", 0) or 0,
                        "comments": getattr(tweet, "reply_count", 0) or 0
                    }
        except Exception as e:
            logger.warning(f"Failed to fetch X analytics for tweet {tweet_id}: {e}")
            
        return {"views": 0, "likes": 0, "shares": 0, "comments": 0}

    async def post_video_from_url(self, url: str, caption: str, temp_dir: str = TEMP_DIR) -> dict:
        """
        Full pipeline: download video -> transcode -> upload -> tweet -> cleanup.
        """
        downloaded_path = None
        transcoded_path = None

        try:
            logger.info(f"Pipeline started for: {url}")

            downloaded_path = await asyncio.to_thread(download_video, url, output_dir=temp_dir)

            transcoded_path = await transcode_for_x(downloaded_path)

            media_id = await self.upload_media(transcoded_path)

            tweet_data = await self.publish_post(caption, media_id=media_id)

            logger.info(f"Pipeline complete: {tweet_data['url']}")

            return tweet_data

        except Exception as e:
            logger.error(f"Pipeline failed: {e}")
            raise

        finally:
            if downloaded_path:
                await asyncio.to_thread(cleanup, downloaded_path)
            # Transcoded path is left for caching and will be swept by the background garbage collector


# ── Convenience Function & Factory ─────────────────────────────────────

def get_publisher(account_dict: dict) -> BasePublisher:
    """Factory to load the correct publisher at runtime."""
    from db import get_setting
    mock_posting = (
        get_setting("mock_posting", "false").lower() == "true" 
        or os.getenv("MOCK_POSTING", "false").lower() == "true"
        or bool(account_dict.get("mock_posting"))
        or bool(account_dict.get("mock"))
    )

    platform = account_dict.get("platform", "x")
    label = account_dict.get("label", "unknown")

    if mock_posting:
        return MockPublisher(label=label, platform=platform)

    if platform == "x":
        return XPublisher(
            label=label,
            auth_mode=account_dict.get("auth_mode", "api"),
            api_key=account_dict.get("api_key"),
            api_secret=account_dict.get("api_secret"),
            access_token=account_dict.get("access_token"),
            access_token_secret=account_dict.get("access_token_secret"),
            cookie_auth_token=account_dict.get("cookie_auth_token"),
            cookie_ct0=account_dict.get("cookie_ct0"),
            proxy_url=account_dict.get("proxy_url"),
            user_agent=account_dict.get("user_agent"),
            mock_posting=mock_posting
        )
    elif platform == "instagram":
        from instagram_publisher import InstagramPublisher
        return InstagramPublisher(
            label=label,
            auth_mode=account_dict.get("auth_mode", "api"),
            credentials=account_dict,
            mock=mock_posting
        )
    elif platform == "tiktok":
        from tiktok_publisher import TikTokPublisher
        return TikTokPublisher(
            label=label,
            auth_mode=account_dict.get("auth_mode", "api"),
            credentials=account_dict,
            mock=mock_posting
        )
    elif platform == "youtube":
        from youtube_publisher import YouTubePublisher
        creds = account_dict.get("credentials") if isinstance(account_dict.get("credentials"), dict) else {}
        return YouTubePublisher(
            label=label,
            client_id=account_dict.get("client_id") or creds.get("client_id"),
            client_secret=account_dict.get("client_secret") or creds.get("client_secret"),
            refresh_token=account_dict.get("refresh_token") or creds.get("refresh_token"),
            mock_posting=mock_posting
        )
    else:
        raise ValueError(f"Unsupported platform: {platform}")


async def post_with_account(account_dict: dict, url: str, caption: str) -> dict:
    """
    Convenience function to post a video tweet using an account dictionary (routes dynamically).
    """
    from db import get_setting, get_daily_limit
    mock_posting = get_setting("mock_posting", "false").lower() == "true" or os.getenv("MOCK_POSTING", "false").lower() == "true"

    label = account_dict.get("label", "unknown")
    platform = account_dict.get("platform", "x")

    if not mock_posting:
        # Check daily post limit
        daily_limit = get_daily_limit(platform)
        post_count = account_dict.get("post_count_today", 0)
        if post_count >= daily_limit:
            msg = f"Account '{label}' ({platform}) has reached the daily post limit ({daily_limit}/day)"
            logger.error(msg)
            raise RuntimeError(msg)

        if post_count >= DAILY_POST_WARNING_THRESHOLD:
            msg = (
                f"Account '{label}' ({platform}) approaching daily limit: "
                f"{post_count}/{DAILY_POST_LIMIT} posts today"
            )
            logger.warning(msg)

    # Initialize correct publisher via factory
    publisher = get_publisher(account_dict)

    # Downloader pipeline
    downloaded_path = None
    transcoded_path = None
    try:
        logger.info(f"Pipeline started for {platform} account '{label}': {url}")

        downloaded_path = await asyncio.to_thread(download_video, url, output_dir=TEMP_DIR)
        
        # Transcode using new platform-aware transcoder
        from downloader import transcode_for_platform
        transcoded_path = await transcode_for_platform(downloaded_path, platform)

        media_id = await publisher.upload_media(transcoded_path)
        post_data = await publisher.publish_post(caption, media_id=media_id)

        # Post-publishing cleanup: delete downloaded_path (transcoded is cached)
        if downloaded_path and os.path.exists(downloaded_path) and downloaded_path != transcoded_path:
            await asyncio.to_thread(cleanup, downloaded_path)

        try:
            await asyncio.to_thread(increment_post_count, label, platform)
            logger.info(f"Post count incremented for {platform} account '{label}'")
        except Exception as e:
            logger.warning(f"Failed to increment post count for '{label}': {e}")

        return post_data

    except Exception as e:
        logger.error(f"Pipeline failed for {platform}: {e}")
        raise
    finally:
        # Clean up raw path if it exists
        if downloaded_path and os.path.exists(downloaded_path):
            await asyncio.to_thread(cleanup, downloaded_path)
