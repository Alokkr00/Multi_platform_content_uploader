"""
scheduler.py - Background Scheduler for X Automation Bot

Periodically checks monitored sources for new videos, downloads them,
generates captions, and posts to X via the publisher.
"""

import os
import time
import asyncio
import random
import logging
import traceback
from datetime import datetime, timezone

import db
from downloader import fetch_metadata, fetch_latest_video, download_video, transcode_for_platform, cleanup
from caption_gen import generate_caption
from publisher import get_publisher

logger = logging.getLogger("clipflow.scheduler")


async def send_webhook_notification(post_id: int, status: str, details: dict):
    """Send a webhook notification on post success or failure."""
    webhook_url = await asyncio.to_thread(db.get_setting, "webhook_url", "")
    webhook_url = webhook_url.strip()
    if not webhook_url:
        return
        
    logger.info(f"Sending webhook notification for post {post_id} (status: {status})")
    
    payload = {
        "content": f"🚀 **Video Posting Alert!** [Status: {status.upper()}]",
        "embeds": [
            {
                "title": details.get("title", f"Post ID: {post_id}"),
                "description": details.get("caption", ""),
                "fields": [
                    {"name": "Platform", "value": details.get("platform", "Unknown").upper(), "inline": True},
                    {"name": "Account", "value": details.get("account_label", "Unknown"), "inline": True},
                    {"name": "Status", "value": status.upper(), "inline": True}
                ],
                "color": 3066993 if status == "success" else 15158332
            }
        ]
    }
    
    if details.get("external_id"):
        payload["embeds"][0]["fields"].append(
            {"name": "Link", "value": details.get("external_id"), "inline": False}
        )
    if details.get("error_msg"):
        payload["embeds"][0]["fields"].append(
            {"name": "Error", "value": details.get("error_msg"), "inline": False}
        )
        
    import urllib.request
    import json
    
    def _send():
        req = urllib.request.Request(
            webhook_url,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "x-automation-bot/1.0"}
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                logger.info(f"Webhook notification sent successfully: {resp.status}")
        except Exception as e:
            logger.warning(f"Failed to send webhook notification: {e}")
            
    try:
        await asyncio.to_thread(_send)
    except Exception as e:
        logger.warning(f"Error in send_webhook_notification: {e}")


class Scheduler:
    """Background scheduler that monitors sources and posts new videos to X."""

    def __init__(self):
        self.running = False
        self.paused = False
        self.task: asyncio.Task | None = None
        self.last_run: str | None = None
        self.current_status: str = "idle"

    def _log(self, level: str, message: str):
        """Log to Python logger (which automatically writes to database asynchronously)."""
        getattr(logger, level.lower(), logger.info)(message)

    async def start(self):
        """Start the scheduler background task."""
        if self.running:
            self._log("WARN", "Scheduler already running")
            return
        self.running = True
        self.task = asyncio.create_task(self._loop())
        self.analytics_task = asyncio.create_task(self._analytics_loop())
        self._log("INFO", "Scheduler started")

    async def stop(self):
        """Stop the scheduler."""
        self.running = False
        if self.task:
            self.task.cancel()
            try:
                await self.task
            except asyncio.CancelledError:
                pass
        if hasattr(self, "analytics_task") and self.analytics_task:
            self.analytics_task.cancel()
            try:
                await self.analytics_task
            except asyncio.CancelledError:
                pass
        self._log("INFO", "Scheduler stopped")

    def pause(self):
        """Pause the scheduler."""
        self.paused = True
        self._log("INFO", "Scheduler paused")

    def resume(self):
        """Resume the scheduler."""
        self.paused = False
        self._log("INFO", "Scheduler resumed")

    def _cleanup_temp_media(self, max_age_hours: float = 2.0):
        """Clean up orphaned media files in temp_media folder."""
        temp_dir = "temp_media"
        if not os.path.exists(temp_dir):
            return
        
        now = time.time()
        max_age_seconds = max_age_hours * 3600
        cleaned_count = 0
        try:
            for filename in os.listdir(temp_dir):
                filepath = os.path.join(temp_dir, filename)
                if os.path.isfile(filepath):
                    mtime = os.path.getmtime(filepath)
                    if (now - mtime) > max_age_seconds:
                        os.remove(filepath)
                        cleaned_count += 1
            if cleaned_count > 0:
                self._log("INFO", f"Garbage Collector: Removed {cleaned_count} orphaned temp media files from {temp_dir}")

            # Also invoke uploaded media lifecycle cleanup for published files older than 7 days
            purged_uploads = db.cleanup_old_uploads(7)
            if purged_uploads > 0:
                self._log("INFO", f"Garbage Collector: Purged {purged_uploads} old published upload media files from uploads/")
        except Exception as e:
            self._log("WARNING", f"Garbage Collector error: {e}")

    async def _loop(self):
        """Main scheduler loop."""
        while self.running:
            # Clean up orphaned temp files at the start of each cycle
            await asyncio.to_thread(self._cleanup_temp_media)
            if not self.paused:
                try:
                    await self.run_cycle()
                except Exception as e:
                    self._log("ERROR", f"Scheduler cycle error: {e}\n{traceback.format_exc()}")

            # Get interval from settings (default 30 minutes)
            try:
                interval = int(db.get_setting("interval_minutes", "30"))
            except (ValueError, TypeError):
                interval = 30

            # Apply timing jitter: add randomized variance of up to ±15%
            jitter_seconds = 0
            try:
                enable_jitter = db.get_setting("enable_scheduler_jitter", "true").lower() == "true"
                if enable_jitter:
                    max_variance_seconds = interval * 60 * 0.15
                    jitter_seconds = int(random.uniform(-max_variance_seconds, max_variance_seconds))
            except Exception as e:
                logger.warning(f"Failed to calculate timing jitter: {e}")

            sleep_seconds = max(10, (interval * 60) + jitter_seconds)
            self._log("INFO", f"Scheduler sleeping for {sleep_seconds // 60}m {sleep_seconds % 60}s before next run (jitter: {jitter_seconds}s)")

            # Wait for next cycle
            self.current_status = "waiting"
            for _ in range(sleep_seconds):  # Check every second for stop/pause signals
                if not self.running:
                    return
                await asyncio.sleep(1)

    async def _analytics_loop(self):
        """Background analytics sync loop."""
        self._log("INFO", "Analytics sync worker started")
        while self.running:
            try:
                await self.sync_post_analytics()
            except Exception as e:
                self._log("ERROR", f"Analytics sync error: {e}")
                
            # Wait 1 hour (3600 seconds)
            for _ in range(3600):
                if not self.running:
                    return
                await asyncio.sleep(1)

    async def sync_post_analytics(self):
        """Synchronize stats for recent posts."""
        self._log("INFO", "Running analytics sync cycle...")
        posts = await asyncio.to_thread(db.get_posts_for_analytics_sync, max_age_days=7)
        if not posts:
            self._log("INFO", "No recent successful posts found for analytics sync.")
            return

        self._log("INFO", f"Syncing analytics for {len(posts)} recent post(s)")
        mock_posting = db.get_setting("mock_posting", "false")
        mock_posting = mock_posting.lower() == "true" or os.getenv("MOCK_POSTING", "false").lower() == "true"

        for post in posts:
            try:
                post_id = post["id"]
                platform = post["platform"]
                external_id = post["external_id"] or post["tweet_id"]
                account_label = post["account_label"]

                if mock_posting:
                    views = post.get("views", 0) or 0
                    likes = post.get("likes", 0) or 0
                    shares = post.get("shares", 0) or 0
                    comments = post.get("comments", 0) or 0

                    views += random.randint(50, 300)
                    likes += random.randint(5, 30)
                    shares += random.randint(1, 5)
                    comments += random.randint(0, 2)

                    await asyncio.to_thread(
                        db.update_post_analytics, post_id, views, likes, shares, comments
                    )
                else:
                    if account_label:
                        account = await asyncio.to_thread(db.get_account, account_label, platform)
                        if account:
                            publisher = get_publisher(account)
                            metrics = await publisher.fetch_analytics(external_id)
                            await asyncio.to_thread(
                                db.update_post_analytics,
                                post_id,
                                metrics.get("views", 0),
                                metrics.get("likes", 0),
                                metrics.get("shares", 0),
                                metrics.get("comments", 0)
                            )
            except Exception as e:
                logger.warning(f"Failed to sync analytics for post {post['id']}: {e}")

    async def run_cycle(self):
        """Execute one scheduler cycle: check sources, download, post."""
        if not hasattr(self, "_cycle_lock"):
            self._cycle_lock = asyncio.Lock()
            
        if self._cycle_lock.locked():
            self._log("WARN", "Scheduler cycle is already in progress. Skipping execution to prevent duplicate posting.")
            return

        async with self._cycle_lock:
            self.current_status = "running"
            self.last_run = datetime.now(timezone.utc).isoformat()
            self._log("INFO", "--- Scheduler cycle started ---")
            try:
                await self._run_cycle_internal()
            finally:
                self._log("INFO", "--- Scheduler cycle completed ---")
                self.current_status = "idle"

    async def _run_cycle_internal(self):

        # Step 1: Get all active sources
        sources = await asyncio.to_thread(db.get_sources, active_only=True)
        if not sources:
            self._log("INFO", "No active sources configured. Skipping cycle.")
            self.current_status = "idle"
            return

        self._log("INFO", f"Checking {len(sources)} active source(s)")

        # Step 2: Check each source for new videos
        new_videos = []
        for source in sources:
            try:
                self._log("INFO", f"Checking source: {source['name']} ({source['url']})")
                metadata = await asyncio.to_thread(fetch_latest_video, source["url"])

                if metadata:
                    # Get configured target platforms (default to X)
                    targets = [t.strip().lower() for t in source.get("target_platforms", "x").split(",") if t.strip()]
                    if not targets:
                        targets = ["x"]
                    
                    for platform in targets:
                        if not await asyncio.to_thread(db.is_video_posted, metadata["video_id"], platform):
                            self._log("INFO", f"New video found for platform '{platform}': {metadata['title']} (ID: {metadata['video_id']})")
                            post_id = await asyncio.to_thread(
                                db.add_post, metadata["video_id"], metadata.get("title", ""), source["id"], platform
                            )
                            # Create work item copy specific to this destination platform
                            work_item = metadata.copy()
                            work_item["post_id"] = post_id
                            work_item["source"] = source
                            work_item["platform"] = platform
                            new_videos.append(work_item)
                else:
                    self._log("INFO", f"No new videos from: {source['name']}")

                # Update last checked
                await asyncio.to_thread(db.update_source_checked, source["id"])

            except Exception as e:
                self._log("ERROR", f"Error checking source {source['name']}: {e}")

        # Step 3: Also process any existing pending posts
        pending = await asyncio.to_thread(db.get_pending_posts, 10)
        for post in pending:
            if not any(v.get("post_id") == post["id"] for v in new_videos):
                # This is a retry from a previous failed attempt
                source = None
                if post.get("source_id"):
                    source = await asyncio.to_thread(db.get_source, post["source_id"])
                new_videos.append({
                    "post_id": post["id"],
                    "video_id": post["video_id"],
                    "title": post.get("title", ""),
                    "description": "",
                    "source": source,
                    "url": None,
                    "platform": post.get("platform", "x")
                })

        # Filter new_videos: maximum 1 video per target platform per cycle to stagger postings
        processed_platforms = set()
        staggered_videos = []
        deferred_count = 0
        for video in new_videos:
            plat = video.get("platform", "x")
            if plat not in processed_platforms:
                processed_platforms.add(plat)
                staggered_videos.append(video)
            else:
                deferred_count += 1
                
        if deferred_count > 0:
            self._log("INFO", f"Staggering: deferred {deferred_count} video(s) to subsequent cycles to prevent spam detection.")
            
        new_videos = staggered_videos

        if not new_videos:
            self._log("INFO", "No new videos to process this cycle")
            self.current_status = "idle"
            return

        self._log("INFO", f"Processing {len(new_videos)} video(s)")

        # Step 4: Process each new video
        download_cache = {}
        try:
            for i, video in enumerate(new_videos):
                if not self.running or self.paused:
                    self._log("WARN", "Scheduler paused/stopped mid-cycle")
                    break

                post_id = video["post_id"]

                try:
                    await self._process_video(video, post_id, download_cache)
                except Exception as e:
                    self._log("ERROR", f"Failed to process video {video.get('title', video['video_id'])}: {e}")
                    await asyncio.to_thread(
                        db.update_post_status, post_id, "failed", error_msg=str(e)
                    )
                    # Send Webhook failure alert
                    asyncio.create_task(send_webhook_notification(post_id, "failed", {
                        "title": video.get("title", ""),
                        "platform": video.get("platform", "x"),
                        "error_msg": str(e)
                    }))

                # Random delay between posts (20-60 seconds)
                if i < len(new_videos) - 1:
                    delay = random.randint(20, 60)
                    self._log("INFO", f"Waiting {delay}s before next post (rate limit safety)")
                    await asyncio.sleep(delay)
        finally:
            self._log("INFO", f"Cleaning up raw media cache ({len(download_cache)} files)...")
            for raw_path in download_cache.values():
                if raw_path:
                    await asyncio.to_thread(cleanup, raw_path)

        pass

    async def _process_video(self, video: dict, post_id: int, download_cache: dict = None):
        """Download, transcode, generate caption, and post a single video."""
        platform = video.get("platform", "x")

        media_type = video.get("media_type", "video").lower()
        media_path = video.get("media_path")

        raw_path = None
        transcoded_path = None
        has_media = True

        if media_type == "text":
            has_media = False
            self._log("INFO", f"Processing text-only post for {platform.UPPER() if hasattr(platform, 'UPPER') else platform.upper()}")
        elif media_type == "image" and media_path and os.path.exists(media_path):
            transcoded_path = media_path
            self._log("INFO", f"Processing image post with media file: {media_path}")
        elif media_type in ("video", "url") and media_path and os.path.exists(media_path):
            raw_path = media_path
            self._log("INFO", f"Processing direct uploaded video file: {media_path}")
        else:
            # Determine the video URL for online sources
            video_url = video.get("url")
            if not video_url and str(video.get("video_id", "")).startswith("http"):
                video_url = video["video_id"]
            elif not video_url and video.get("source"):
                source = video["source"]
                if source.get("platform") == "youtube":
                    video_url = f"https://www.youtube.com/watch?v={video['video_id']}"
                else:
                    video_url = source.get("url")

            if not video_url:
                has_media = False
            else:
                # Step 1: Download / Cache Check
                video_id = video["video_id"]
                if download_cache is not None and video_id in download_cache:
                    raw_path = download_cache[video_id]
                    if raw_path and os.path.exists(raw_path):
                        self._log("INFO", f"Cache HIT (raw video): reusing downloaded file {raw_path} for {platform}")

                if not raw_path or not os.path.exists(raw_path):
                    try:
                        self._log("INFO", f"Downloading: {video.get('title', video_url)}")
                        await asyncio.to_thread(db.update_post_status, post_id, "downloading")
                        raw_path = await asyncio.to_thread(download_video, video_url)
                        self._log("INFO", f"Downloaded to: {raw_path}")
                        if download_cache is not None:
                            download_cache[video_id] = raw_path
                    except Exception as de:
                        self._log("WARN", f"Failed to download video: {de}. Checking fallback to text-only post.")
                        has_media = False

        try:
            if has_media and raw_path and not transcoded_path:
                try:
                    # Step 2: Transcode for the specific target platform
                    self._log("INFO", f"Transcoding for {platform.upper()} compatibility...")
                    await asyncio.to_thread(db.update_post_status, post_id, "transcoding")
                    transcoded_path = await transcode_for_platform(raw_path, platform)
                    self._log("INFO", f"Transcoded to: {transcoded_path}")

                    # Clean up raw file if different from transcoded AND download_cache is not active
                    if download_cache is None and raw_path != transcoded_path and not media_path:
                        await asyncio.to_thread(cleanup, raw_path)
                except Exception as te:
                    self._log("WARN", f"Failed to transcode video: {te}. Checking fallback to text-only post.")
                    has_media = False

            if not has_media and media_type != "text":
                if platform.lower() in ("instagram", "tiktok"):
                    raise ValueError(f"Platform {platform.upper()} requires a media file, but none could be downloaded/transcoded.")
                else:
                    self._log("INFO", f"No media available. Proceeding with text-only post for {platform.upper()}")

            try:
                # Step 3: Generate caption
                caption = video.get("caption")
                if not caption:
                    title = video.get("title", "")
                    description = video.get("description", "")
                    caption_template = await asyncio.to_thread(db.get_setting, "caption_template", "")
                    use_ai = await asyncio.to_thread(db.get_setting, "use_ai_captions", "true")

                    if use_ai.lower() == "true":
                        caption = await asyncio.to_thread(
                            generate_caption, title, description, caption_template or None, platform
                        )
                    else:
                        caption = title
                self._log("INFO", f"Caption: {caption}")

                # Step 4: Select account for the target platform
                account_label = video.get("account_label")
                if account_label:
                    account = await asyncio.to_thread(db.get_account, account_label, platform)
                    if not account:
                        raise ValueError(f"Selected account '{account_label}' not found on {platform}.")
                else:
                    account = await asyncio.to_thread(db.get_least_used_account, platform)
                    
                if not account:
                    raise ValueError(f"No {platform.upper()} accounts configured. Add one via the dashboard.")

                # Check rate limit
                limit = 50 if platform == "x" else (10 if platform == "instagram" else 20)
                if account["post_count_today"] >= limit:
                    raise ValueError(f"Account {account['label']} has hit the daily post limit ({limit})")

                self._log("INFO", f"Using account: {account['label']} (posts today: {account['post_count_today']})")

                # Step 5: Upload and post using get_publisher factory
                await asyncio.to_thread(db.update_post_status, post_id, "uploading")
                publisher = get_publisher(account)

                # Upload media
                media_id = await publisher.upload_media(transcoded_path)
                self._log("INFO", f"Media uploaded: {media_id}")

                # Check link placement for X (auto-reply thread)
                reply_link = None
                if platform == "x":
                    x_link_placement = await asyncio.to_thread(db.get_setting, "x_link_placement", "thread_reply")
                    if x_link_placement == "thread_reply" and video_url:
                        reply_link = f"Source video: {video_url}"
                    elif x_link_placement == "main_tweet" and video_url:
                        caption = f"{caption}\n\nSource: {video_url}"

                # Publish post
                post_data = await publisher.publish_post(caption, media_id, reply_link=reply_link)
                post_id_ext = post_data.get("id", "")
                post_url = post_data.get("url", "")
                self._log("INFO", f"Published on {platform.upper()}: {post_url}")

                # Update database
                await asyncio.to_thread(
                    db.update_post_status, post_id, "success",
                    tweet_id=str(post_id_ext), account_label=account["label"], caption=caption, external_id=post_url
                )
                await asyncio.to_thread(db.increment_post_count, account["label"], platform)

                # Send Webhook success alert
                asyncio.create_task(send_webhook_notification(post_id, "success", {
                    "title": video.get("title", ""),
                    "caption": caption,
                    "platform": platform,
                    "account_label": account["label"],
                    "external_id": post_url
                }))

            finally:
                if transcoded_path and transcoded_path != raw_path:
                    await asyncio.to_thread(cleanup, transcoded_path)

        except Exception:
            # Clean up raw file on error only if download_cache is not active
            if download_cache is None:
                await asyncio.to_thread(cleanup, raw_path)
            raise


async def send_system_notification(event_type: str, title: str, details: dict):
    """
    Send lightweight background notification via Telegram Bot API and/or Webhook.
    Zero third-party dependencies (uses urllib.request).
    """
    try:
        def _send():
            import urllib.request
            import json
            import re

            webhook_url = db.get_setting("webhook_url", "").strip()
            telegram_token = db.get_setting("telegram_bot_token", "").strip()
            telegram_chat_id = db.get_setting("telegram_chat_id", "").strip()

            def _escape_md(text: str) -> str:
                if not text:
                    return ""
                # Escape markdown special characters for plain text insertion
                return re.sub(r'([_*`\[\]])', r'\\\1', str(text))

            # 1. Custom JSON Webhook
            if webhook_url:
                try:
                    payload = json.dumps({
                        "event": event_type,
                        "title": title,
                        "details": details,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    }).encode('utf-8')
                    req = urllib.request.Request(webhook_url, data=payload, headers={'Content-Type': 'application/json'}, method='POST')
                    with urllib.request.urlopen(req, timeout=5) as res:
                        logger.info(f"Webhook notification sent ({event_type}): {res.status}")
                except Exception as we:
                    logger.warning(f"Webhook notification failed: {we}")

            # 2. Telegram Bot API
            if telegram_token and telegram_chat_id:
                try:
                    clean_title = _escape_md(title)
                    text_msg = f"🔔 *X Automation Alert: {event_type.upper()}*\n\n*Title:* {clean_title}\n"
                    for k, v in details.items():
                        if v:
                            clean_k = _escape_md(k)
                            clean_v = _escape_md(v)
                            text_msg += f"• *{clean_k}:* {clean_v}\n"

                    tg_url = f"https://api.telegram.org/bot{telegram_token}/sendMessage"
                    tg_payload = json.dumps({
                        "chat_id": telegram_chat_id,
                        "text": text_msg,
                        "parse_mode": "Markdown"
                    }).encode('utf-8')
                    req = urllib.request.Request(tg_url, data=tg_payload, headers={'Content-Type': 'application/json'}, method='POST')
                    with urllib.request.urlopen(req, timeout=5) as res:
                        logger.info(f"Telegram notification sent ({event_type}): {res.status}")
                except Exception as tge:
                    logger.warning(f"Telegram notification failed: {tge}")

        await asyncio.to_thread(_send)
    except Exception as e:
        logger.warning(f"Failed to process system notification: {e}")


# Global scheduler instance
scheduler = Scheduler()
