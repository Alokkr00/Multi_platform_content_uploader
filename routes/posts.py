"""
routes/posts.py — Content Ingestion, Uploads & Approval Queue Endpoints
"""

import os
import uuid
import time
import asyncio
import logging
from datetime import datetime
from fastapi import APIRouter, HTTPException, Request, File, UploadFile

import db
from routes.sources import validate_safe_url

logger = logging.getLogger("clipflow.routes.posts")

router = APIRouter(tags=["Posts"])

# Static upload path setup
BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
UPLOADS_DIR = os.path.join(BASE_DIR, "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB limit

ALLOWED_MAGIC_HEADERS = [
    b"\x00\x00\x00",     # MP4 / MOV container ftyp
    b"RIFF",             # AVI / WEBP
    b"\x1a\x45\xdf\xa3", # WebM / MKV
    b"\x89PNG",          # PNG image
    b"\xff\xd8\xff",     # JPEG image
    b"GIF8"              # GIF image
]


@router.get("/api/history")
async def get_history(limit: int = 50):
    posts = await asyncio.to_thread(db.get_recent_posts, limit)
    return posts


@router.post("/api/quick-post")
async def quick_post(request: Request):
    """Instantly process and publish a single video URL."""
    data = await request.json()
    url = data.get("url", "").strip()
    caption_override = data.get("caption", "").strip()
    platform = data.get("platform", "x").strip().lower()

    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    # SSRF Protection
    validate_safe_url(url)

    account = await asyncio.to_thread(db.get_least_used_account, platform)
    if not account:
        raise HTTPException(status_code=400, detail=f"No active accounts configured for {platform.upper()}")

    post_id = await asyncio.to_thread(
        db.add_ingested_post,
        video_id=url,
        title="Quick Post",
        caption=caption_override,
        media_type="video",
        media_path=url,
        platform=platform,
        status="processing",
        requires_approval=0
    )
    logger.info(f"Quick post triggered ({platform}): {url}")

    async def _process():
        from downloader import fetch_metadata, download_video, transcode_for_platform, cleanup
        from caption_gen import generate_caption
        from publisher import get_publisher

        raw_path = None
        transcoded_path = None
        try:
            meta = None
            try:
                meta = await asyncio.to_thread(fetch_metadata, url)
                if meta and meta.get("title"):
                    conn = db.get_connection()
                    conn.execute("UPDATE posts_history SET title = ? WHERE id = ?", (meta["title"], post_id))
                    conn.commit()
            except Exception as me:
                logger.warning(f"Failed to fetch metadata for video ID update: {me}")

            title = meta.get("title", "") if meta else ""
            desc = meta.get("description", "") if meta else ""

            await asyncio.to_thread(db.update_post_status, post_id, "downloading")
            try:
                raw_path = await asyncio.to_thread(download_video, url)
                await asyncio.to_thread(db.update_post_status, post_id, "transcoding")
                transcoded_path = await transcode_for_platform(raw_path, platform)
            except Exception as de:
                logger.error(f"Failed to download/transcode video media: {de}")
                raise RuntimeError(f"Video download/transcode failed: {de}") from de

            if caption_override and caption_override.strip():
                generated = caption_override.strip()
            elif title and title.strip():
                generated = await asyncio.to_thread(generate_caption, title, desc, platform=platform)
            else:
                generated = ""

            if not generated or not generated.strip():
                generated = title.strip() if (title and title.strip()) else f"Check this out! {url}"

            reply_link = None
            if platform == "x":
                x_link_placement = await asyncio.to_thread(db.get_setting, "x_link_placement", "thread_reply")
                if x_link_placement == "thread_reply" and url:
                    reply_link = f"Source video: {url}"
                elif x_link_placement == "main_tweet" and url:
                    generated = f"{generated}\n\nSource: {url}"

            await asyncio.to_thread(db.update_post_status, post_id, "uploading")
            publisher = get_publisher(account)
            media_id = await publisher.upload_media(transcoded_path)
            tweet = await publisher.publish_post(generated, media_id, reply_link=reply_link)

            await asyncio.to_thread(
                db.update_post_status, post_id, "success",
                tweet_id=str(tweet["id"]), account_label=account["label"], caption=generated, external_id=tweet.get("url")
            )
            await asyncio.to_thread(db.increment_post_count, account["label"], platform)
            logger.info(f"Quick post success ({platform}): {tweet.get('url', '')}")

            from scheduler import send_system_notification
            asyncio.create_task(send_system_notification("post_success", "Post Published Successfully", {
                "title": meta.get("title", "") if meta else url,
                "caption": generated,
                "platform": platform,
                "account_label": account["label"],
                "external_id": tweet.get("url")
            }))

        except Exception as e:
            await asyncio.to_thread(db.update_post_status, post_id, "failed", error_msg=str(e))
            logger.error(f"Quick post failed ({platform}): {e}")
            from scheduler import send_system_notification
            asyncio.create_task(send_system_notification("post_failed", "Quick Post Failed", {
                "title": url,
                "platform": platform,
                "error_msg": str(e)
            }))
        finally:
            if raw_path:
                await asyncio.to_thread(cleanup, raw_path)
            if transcoded_path and transcoded_path != raw_path:
                await asyncio.to_thread(cleanup, transcoded_path)

    asyncio.create_task(_process())
    return {"status": "ok", "post_id": post_id, "message": "Processing in background"}


@router.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload media file (video/image) with size limit, UUID naming, and magic byte validation."""
    filename = os.path.basename(file.filename)
    allowed_exts = {".mp4", ".mov", ".avi", ".webm", ".png", ".jpg", ".jpeg", ".webp"}
    ext = os.path.splitext(filename)[1].lower()
    
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"Unsupported file extension: '{ext}'")

    # Read first 8 KB to validate magic bytes and size limit
    header = await file.read(8192)
    if not header:
        raise HTTPException(status_code=400, detail="Empty upload file")

    if not any(header.startswith(magic) or magic in header[:32] for magic in ALLOWED_MAGIC_HEADERS):
        raise HTTPException(status_code=400, detail="Invalid file header signature")

    # Generate secure UUID filename
    unique_filename = f"{uuid.uuid4().hex}{ext}"
    target_path = os.path.join(UPLOADS_DIR, unique_filename)

    total_size = len(header)
    try:
        with open(target_path, "wb") as f:
            f.write(header)
            while chunk := await file.read(65536):
                total_size += len(chunk)
                if total_size > MAX_UPLOAD_BYTES:
                    f.close()
                    if os.path.exists(target_path):
                        os.remove(target_path)
                    raise HTTPException(status_code=413, detail="File size exceeds maximum 50MB upload limit")
                f.write(chunk)
    except HTTPException:
        raise
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        if os.path.exists(target_path):
            os.remove(target_path)
        raise HTTPException(status_code=500, detail="File save failed")

    media_type = "video" if ext in {".mp4", ".mov", ".avi", ".webm"} else "image"
    relative_path = os.path.join("uploads", unique_filename).replace("\\", "/")
    logger.info(f"File uploaded successfully: {relative_path} ({media_type}, {total_size} bytes)")
    
    return {
        "status": "success",
        "filename": filename,
        "file_path": relative_path,
        "media_type": media_type
    }


@router.post("/api/ingest")
async def ingest_post(request: Request):
    """
    Ingest multi-format content (text, image, video, or URL) into the Approval Queue.
    """
    data = await request.json()
    content_type = data.get("content_type", "video").strip().lower()
    caption = data.get("text", "").strip() or data.get("caption", "").strip()
    title = data.get("title", "").strip() or caption[:50] or "Untitled Content"
    media_path = data.get("media_path", "").strip()
    url = data.get("url", "").strip()

    if url:
        validate_safe_url(url)

    target_platforms = data.get("target_platforms", ["x"])
    account_label = data.get("account", "").strip() or None
    requires_approval = 1 if data.get("requires_approval", True) else 0
    status = "pending_approval" if requires_approval else "approved"
    raw_scheduled_at = data.get("scheduled_at", "").strip() or None
    
    scheduled_at = None
    if raw_scheduled_at:
        try:
            dt = datetime.fromisoformat(raw_scheduled_at.replace("Z", "+00:00"))
            scheduled_at = dt.strftime("%Y-%m-%d %H:%M:%S")
        except Exception:
            scheduled_at = raw_scheduled_at

    if isinstance(target_platforms, str):
        target_platforms = [p.strip().lower() for p in target_platforms.split(",") if p.strip()]

    if not target_platforms:
        target_platforms = ["x"]

    created_ids = []
    for platform in target_platforms:
        video_id = url if content_type == "url" and url else f"ingest_{content_type}_{int(time.time())}_{platform}"
        post_id = await asyncio.to_thread(
            db.add_ingested_post,
            video_id=video_id,
            title=title,
            caption=caption,
            media_type=content_type,
            media_path=media_path or (url if content_type == "url" else None),
            platform=platform,
            status=status,
            requires_approval=requires_approval,
            scheduled_at=scheduled_at,
            account_label=account_label
        )
        created_ids.append(post_id)

    logger.info(f"Ingested {len(created_ids)} post item(s) for platforms {target_platforms} with status '{status}'")
    return {"status": "success", "post_ids": created_ids, "queue_status": status, "scheduled_at": scheduled_at}


@router.get("/api/approval-queue")
async def get_approval_queue():
    """Get drafts and posts pending human approval."""
    items = await asyncio.to_thread(db.get_approval_queue, 50)
    return items


@router.post("/api/posts/{post_id}/approve")
async def approve_post(post_id: int):
    """Approve a post in the queue for immediate or scheduled execution."""
    success = await asyncio.to_thread(db.approve_post, post_id)
    if not success:
        raise HTTPException(status_code=404, detail="Post not found")
    logger.info(f"Post #{post_id} approved for execution")
    return {"status": "success", "message": f"Post #{post_id} approved"}


@router.post("/api/posts/approve-all")
async def approve_all_posts():
    """Batch approve all posts pending review."""
    count = await asyncio.to_thread(db.approve_all_posts)
    logger.info(f"Batch approved {count} post(s)")
    return {"status": "success", "approved_count": count}


@router.delete("/api/posts/{post_id}")
async def reject_post(post_id: int):
    """Reject and delete a queued post draft."""
    success = await asyncio.to_thread(db.reject_post, post_id)
    if not success:
        raise HTTPException(status_code=404, detail="Post not found")
    logger.info(f"Post #{post_id} rejected and removed")
    return {"status": "success", "message": f"Post #{post_id} removed"}


@router.post("/api/posts/{post_id}/retry")
async def retry_failed_post(post_id: int):
    """Reset a failed post back to 'approved' status for immediate re-execution."""
    success = await asyncio.to_thread(db.retry_failed_post, post_id)
    if not success:
        raise HTTPException(status_code=404, detail="Post not found")
    logger.info(f"Post #{post_id} queued for retry execution")
    return {"status": "success", "message": f"Post #{post_id} queued for retry"}


@router.post("/api/maintenance/cleanup-uploads")
async def trigger_uploads_cleanup(days: int = 7):
    """Manually trigger purging of published upload media files older than N days."""
    count = await asyncio.to_thread(db.cleanup_old_uploads, days)
    logger.info(f"Maintenance: Purged {count} old upload media file(s)")
    return {"status": "success", "purged_count": count}
