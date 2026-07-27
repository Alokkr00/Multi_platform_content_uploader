"""
server.py - FastAPI REST API for X Automation Bot

Serves the web dashboard and exposes REST endpoints for
managing sources, accounts, settings, history, and logs.
"""

import asyncio
import logging
import os
import sys
import time
from contextlib import asynccontextmanager

import uvicorn
from fastapi import FastAPI, HTTPException, Request, File, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import db
from scheduler import scheduler

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("x_automation.server")

import queue
import threading

log_queue = queue.Queue()

def _async_log_writer():
    """Background thread that writes logs to the database from the queue."""
    while True:
        try:
            item = log_queue.get()
            if item is None:  # Sentinel value to exit
                break
            levelname, message = item
            db.add_log(levelname, message)
            log_queue.task_done()
        except Exception:
            pass

# Start the log writer thread
writer_thread = threading.Thread(target=_async_log_writer, daemon=True)
writer_thread.start()

# Database-backed log handler
class DBLogHandler(logging.Handler):
    def emit(self, record):
        try:
            # Put logging record into the thread-safe queue to keep it non-blocking
            log_queue.put((record.levelname, record.getMessage()))
        except Exception:
            pass

# Add DB handler to root logger if not already present
logger_obj = logging.getLogger("x_automation")
if not any(isinstance(h, DBLogHandler) for h in logger_obj.handlers):
    db_handler = DBLogHandler()
    db_handler.setLevel(logging.INFO)
    logger_obj.addHandler(db_handler)


# ── App Lifecycle ─────────────────────────────────────────────────────

async def temp_media_garbage_collector():
    """Background task that runs every 1 hour and purges files in temp_media/ older than 12 hours."""
    from downloader import TEMP_DIR
    
    logger.info("Starting background temp media garbage collector")
    while True:
        try:
            if os.path.exists(TEMP_DIR):
                now = time.time()
                for filename in os.listdir(TEMP_DIR):
                    filepath = os.path.join(TEMP_DIR, filename)
                    if os.path.isfile(filepath):
                        # Get last modified time
                        mtime = os.path.getmtime(filepath)
                        # Check if older than 12 hours (12 * 3600 seconds)
                        if now - mtime > 12 * 3600:
                            logger.info(f"GC: Purging stale temp file: {filename}")
                            try:
                                os.remove(filepath)
                                logger.info(f"GC: Purged stale temp file: {filename}")
                            except Exception as ex:
                                logger.warning(f"GC: Failed to delete {filename}: {ex}")
        except Exception as e:
            logger.error(f"Error in temp media garbage collector: {e}")
            
        # Wait 1 hour (3600 seconds)
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    # Startup
    db.init_db()
    logger.info("Database initialized")

    # Reset stuck active post states
    db.reset_stuck_post_states()
    logger.info("Stuck post states reset successfully")

    # Start garbage collector task
    gc_task = asyncio.create_task(temp_media_garbage_collector())

    # Auto-start scheduler if not paused
    await scheduler.start()
    logger.info("Scheduler started")

    yield

    # Shutdown
    # Cancel GC task
    gc_task.cancel()
    try:
        await gc_task
    except asyncio.CancelledError:
        pass

    await scheduler.stop()
    logger.info("Scheduler stopped")

    # Clean kill all currently running ffmpeg/ffprobe subprocesses
    from downloader import kill_active_processes
    kill_active_processes()

    # Shutdown logging thread writer
    log_queue.put(None)
    writer_thread.join(timeout=2.0)
    logger.info("Scheduler stopped. Goodbye!")


app = FastAPI(
    title="X Automation Bot",
    description="Video Collector & Auto-Publisher for X/Twitter",
    version="1.0.0",
    lifespan=lifespan
)

# Restrict CORS to localhost
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8001", "http://127.0.0.1:8001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTP Security Headers Middleware
@app.middleware("http")
async def add_security_headers(request: Request, call_next):
    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self' 'unsafe-inline' 'unsafe-eval' "
        "https://fonts.googleapis.com https://fonts.gstatic.com;"
    )
    return response

# Static & Upload files
STATIC_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
if os.path.isdir(STATIC_DIR):
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

UPLOADS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "uploads")
os.makedirs(UPLOADS_DIR, exist_ok=True)
app.mount("/uploads", StaticFiles(directory=UPLOADS_DIR), name="uploads")


# ── Dashboard Serve ───────────────────────────────────────────────────

@app.get("/", include_in_schema=False)
async def serve_dashboard():
    index_path = os.path.join(STATIC_DIR, "index.html")
    if os.path.exists(index_path):
        return FileResponse(index_path)
    return JSONResponse({"error": "Dashboard not found. Place index.html in static/"}, status_code=404)


# ── Status Endpoint ───────────────────────────────────────────────────

@app.get("/api/status")
async def get_status():
    stats = await asyncio.to_thread(db.get_stats)
    return {
        "scheduler": {
            "running": scheduler.running,
            "paused": scheduler.paused,
            "status": scheduler.current_status,
            "last_run": scheduler.last_run,
        },
        "stats": stats,
    }


# ── Settings Endpoints ────────────────────────────────────────────────

@app.get("/api/settings")
async def get_settings():
    settings = await asyncio.to_thread(db.get_all_settings)
    # Mask sensitive values securely
    masked = {}
    for key, value in settings.items():
        if any(s in key.lower() for s in ["key", "secret", "token", "password", "webhook"]):
            masked[key] = "********" if value else ""
        else:
            masked[key] = value
    return masked


@app.post("/api/settings")
async def save_settings(request: Request):
    data = await request.json()
    if not isinstance(data, dict):
        raise HTTPException(status_code=400, detail="Expected JSON object")

    # Don't overwrite sensitive fields with placeholder values
    clean = {}
    for key, value in data.items():
        if isinstance(value, str) and value == "********":
            continue  # Skip unchanged placeholder values
        clean[key] = value

    await asyncio.to_thread(db.save_all_settings, clean)
    logger.info(f"Settings updated: {list(clean.keys())}")
    return {"status": "ok", "updated": list(clean.keys())}


@app.post("/api/test-webhook")
async def test_webhook():
    """Send a test payload to the configured webhook URL."""
    try:
        from scheduler import send_webhook_notification
        # Use a dummy post ID like 9999
        await send_webhook_notification(9999, "success", {
            "title": "🎉 Webhook Test Notification",
            "caption": "Your webhook integration for X Automation Bot is configured correctly!",
            "platform": "x",
            "account_label": "test_account",
            "external_id": "https://localhost:8001"
        })
        return {"status": "ok", "message": "Test webhook payload sent successfully."}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to send test webhook: {e}")


# ── Sources Endpoints ─────────────────────────────────────────────────

@app.get("/api/sources")
async def get_sources():
    sources = await asyncio.to_thread(db.get_sources)
    return sources


@app.post("/api/sources")
async def add_source(request: Request):
    data = await request.json()
    url = data.get("url", "").strip()
    name = data.get("name", "").strip()
    platform = data.get("platform", "other").strip().lower()
    
    # Destination platforms (default to X only)
    destinations = data.get("destinations", ["x"])
    if not isinstance(destinations, list):
        destinations = ["x"]
    target_platforms = ",".join(destinations)

    if not url:
        raise HTTPException(status_code=400, detail="URL is required")
    if not name:
        name = url

    # Auto-detect platform
    if platform == "other":
        if "youtube.com" in url or "youtu.be" in url:
            platform = "youtube"
        elif "tiktok.com" in url:
            platform = "tiktok"
        elif "instagram.com" in url:
            platform = "instagram"

    try:
        source_id = await asyncio.to_thread(db.add_source, url, name, platform, target_platforms)
        logger.info(f"Source added: {name} ({url}) -> targets: {target_platforms}")
        return {"status": "ok", "id": source_id}
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail="Source URL already exists")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/sources/{source_id}")
async def remove_source(source_id: int):
    await asyncio.to_thread(db.delete_source, source_id)
    logger.info(f"Source removed: ID {source_id}")
    return {"status": "ok"}


@app.post("/api/sources/{source_id}/toggle")
async def toggle_source(source_id: int, request: Request):
    data = await request.json()
    active = data.get("active", True)
    await asyncio.to_thread(db.toggle_source, source_id, active)
    return {"status": "ok"}


# ── Accounts Endpoints ────────────────────────────────────────────────

@app.get("/api/accounts")
async def get_accounts():
    accounts = await asyncio.to_thread(db.get_accounts, decrypt=False)
    return accounts


async def validate_account_credentials(platform: str, auth_mode: str, creds: dict, proxy_url: str = None, user_agent: str = None):
    """Test account credentials before saving to the database. Supports mock posting mode."""
    mock_posting = (await asyncio.to_thread(db.get_setting, "mock_posting", "false")).lower() == "true" or os.getenv("MOCK_POSTING", "false").lower() == "true"
    if mock_posting:
        logger.info(f"Pre-flight verification skipped (MOCK_POSTING is active) for platform {platform}")
        return True

    logger.info(f"Running pre-flight credentials verification for platform {platform} ({auth_mode})")
    try:
        if platform == "x":
            from publisher import XPublisher
            pub = XPublisher(
                label="verification_test",
                auth_mode=auth_mode,
                api_key=creds.get("api_key"),
                api_secret=creds.get("api_secret"),
                access_token=creds.get("access_token"),
                access_token_secret=creds.get("access_token_secret"),
                cookie_auth_token=creds.get("cookie_auth_token"),
                cookie_ct0=creds.get("cookie_ct0"),
                proxy_url=proxy_url,
                user_agent=user_agent
            )
            if auth_mode == "api":
                await asyncio.to_thread(pub.client.get_me)
            elif auth_mode == "cookie":
                await pub.twikit_client.get_user_by_screen_name("Twitter")
        elif platform == "instagram":
            from instagram_publisher import InstagramPublisher
            pub = InstagramPublisher(
                label="verification_test",
                auth_mode=auth_mode,
                credentials={
                    **creds,
                    "proxy_url": proxy_url,
                    "user_agent": user_agent
                }
            )
            if auth_mode == "cookie":
                await asyncio.to_thread(pub._login_instagrapi)
        return True
    except Exception as e:
        logger.warning(f"Pre-flight verification failed: {e}")
        raise ValueError(f"Credentials validation failed: {e}")


@app.post("/api/accounts")
async def add_account(request: Request):
    data = await request.json()
    label = data.get("label")
    label = label.strip() if label else ""
    
    platform = data.get("platform")
    platform = platform.strip().lower() if platform else "x"
    
    auth_mode = data.get("auth_mode")
    auth_mode = auth_mode.strip().lower() if auth_mode else "api"
    
    proxy_url = data.get("proxy_url")
    proxy_url = proxy_url.strip() if proxy_url else None
    
    user_agent = data.get("user_agent")
    user_agent = user_agent.strip() if user_agent else None
    
    # Extract credentials dictionary
    creds = data.get("credentials")
    if not creds or not isinstance(creds, dict):
        # Fallback to flat dictionary for backward compatibility
        creds = {}
        for k, v in data.items():
            if k not in ("label", "platform", "auth_mode", "proxy_url", "user_agent"):
                creds[k] = v

    if not label:
        raise HTTPException(status_code=400, detail="Label is required")

    try:
        # Pre-flight validation
        try:
            await validate_account_credentials(platform, auth_mode, creds, proxy_url, user_agent)
        except Exception as ve:
            raise HTTPException(status_code=400, detail=str(ve))

        account_id = await asyncio.to_thread(
            db.add_account, 
            label=label, 
            platform=platform,
            auth_mode=auth_mode, 
            credentials=creds,
            proxy_url=proxy_url,
            user_agent=user_agent
        )
        logger.info(f"Account added: {label} (platform: {platform}, mode: {auth_mode})")
        return {"status": "ok", "id": account_id}
    except HTTPException:
        raise
    except Exception as e:
        if "UNIQUE" in str(e):
            raise HTTPException(status_code=409, detail=f"Account '{label}' already exists on {platform}")
        raise HTTPException(status_code=500, detail=str(e))


@app.delete("/api/accounts/{label}")
async def remove_account(label: str, platform: str = None):
    await asyncio.to_thread(db.delete_account, label, platform)
    logger.info(f"Account removed: {label} ({platform or 'all'})")
    return {"status": "ok"}


# ── History Endpoint ──────────────────────────────────────────────────

@app.get("/api/history")
async def get_history(limit: int = 50):
    history = await asyncio.to_thread(db.get_recent_posts, limit)
    return history


# ── Logs Endpoint ─────────────────────────────────────────────────────

@app.get("/api/logs")
async def get_logs(limit: int = 100):
    logs = await asyncio.to_thread(db.get_logs, limit)
    return logs


# ── Manual Actions ────────────────────────────────────────────────────

@app.post("/api/run")
async def trigger_run():
    """Trigger an immediate scheduler cycle."""
    if scheduler.paused:
        return {"status": "error", "message": "Scheduler is paused. Resume it first."}

    logger.info("Manual run triggered")
    asyncio.create_task(scheduler.run_cycle())
    return {"status": "ok", "message": "Scheduler cycle triggered"}


@app.post("/api/pause")
async def toggle_pause():
    """Toggle scheduler pause state."""
    if scheduler.paused:
        scheduler.resume()
        return {"status": "ok", "paused": False, "message": "Scheduler resumed"}
    else:
        scheduler.pause()
        return {"status": "ok", "paused": True, "message": "Scheduler paused"}


# ── Quick Post (manual one-off) ──────────────────────────────────────

@app.post("/api/quick-post")
async def quick_post(request: Request):
    """Post a single video URL immediately without going through the scheduler."""
    data = await request.json()
    url = data.get("url", "").strip()
    caption = data.get("caption", "").strip()
    account_label = data.get("account", "").strip()
    platform = data.get("platform", "x").strip().lower()

    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    # Get account
    if account_label:
        account = await asyncio.to_thread(db.get_account, account_label, platform)
    else:
        account = await asyncio.to_thread(db.get_least_used_account, platform)

    if not account:
        raise HTTPException(status_code=400, detail=f"No {platform.upper()} account available")

    # Create post record with 'downloading' status directly to prevent scheduler race conditions
    post_id = await asyncio.to_thread(db.add_post, url, caption or url, platform=platform, status="downloading")
    logger.info(f"Quick post triggered ({platform}): {url}")

    # Process in background
    async def _process():
        raw_path = None
        transcoded_path = None
        try:
            from publisher import get_publisher
            from downloader import download_video, transcode_for_platform, cleanup, fetch_metadata

            # Fetch metadata to extract actual short video ID
            meta = {}
            has_media = True
            try:
                meta = await asyncio.to_thread(fetch_metadata, url)
                video_id = meta.get("video_id")
                if video_id:
                    await asyncio.to_thread(db.update_post_video_id, post_id, video_id)
            except Exception as me:
                logger.warning(f"Failed to fetch metadata for video ID update: {me}")
                has_media = False

            if has_media:
                try:
                    raw_path = await asyncio.to_thread(download_video, url)
                    await asyncio.to_thread(db.update_post_status, post_id, "transcoding")
                    transcoded_path = await transcode_for_platform(raw_path, platform)
                except Exception as de:
                    logger.warning(f"Failed to download/transcode video media: {de}")
                    has_media = False

            if not has_media:
                if platform.lower() in ("instagram", "tiktok"):
                    raise ValueError(f"Platform {platform.upper()} requires a video file, but no media could be downloaded/transcoded.")
                else:
                    logger.info(f"No media available. Proceeding with text-only post for {platform.upper()}")

            if not caption:
                from caption_gen import generate_caption
                generated = await asyncio.to_thread(generate_caption, meta.get("title", ""), meta.get("description", ""), None, platform)
            else:
                generated = caption

            # Check X reply link placement
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
            tweet = await publisher.post_tweet(generated, media_id, reply_link=reply_link)

            await asyncio.to_thread(
                db.update_post_status, post_id, "success",
                tweet_id=str(tweet["id"]), account_label=account["label"], caption=generated, external_id=tweet.get("url")
            )
            await asyncio.to_thread(db.increment_post_count, account["label"], platform)
            logger.info(f"Quick post success ({platform}): {tweet.get('url', '')}")

            # Send Webhook success notification
            from scheduler import send_webhook_notification
            asyncio.create_task(send_webhook_notification(post_id, "success", {
                "title": meta.get("title", "") if meta else url,
                "caption": generated,
                "platform": platform,
                "account_label": account["label"],
                "external_id": tweet.get("url")
            }))

        except Exception as e:
            await asyncio.to_thread(db.update_post_status, post_id, "failed", error_msg=str(e))
            logger.error(f"Quick post failed ({platform}): {e}")
            from scheduler import send_webhook_notification
            asyncio.create_task(send_webhook_notification(post_id, "failed", {
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


# ── Multi-Format Ingestion & Approval Queue Endpoints ────────────────

@app.post("/api/upload")
async def upload_file(file: UploadFile = File(...)):
    """Upload media file (video/image) to persistent uploads/ directory."""
    allowed_exts = {".mp4", ".mov", ".avi", ".webm", ".png", ".jpg", ".jpeg", ".webp"}
    ext = os.path.splitext(file.filename)[1].lower()
    
    if ext not in allowed_exts:
        raise HTTPException(status_code=400, detail=f"Unsupported file extension: '{ext}'")

    media_type = "video" if ext in {".mp4", ".mov", ".avi", ".webm"} else "image"
    safe_name = f"{int(time.time())}_{file.filename.replace(' ', '_')}"
    target_path = os.path.join(UPLOADS_DIR, safe_name)

    try:
        contents = await file.read()
        with open(target_path, "wb") as f:
            f.write(contents)
    except Exception as e:
        logger.error(f"Failed to save uploaded file: {e}")
        raise HTTPException(status_code=500, detail=f"File save failed: {e}")

    relative_path = os.path.join("uploads", safe_name).replace("\\", "/")
    logger.info(f"File uploaded successfully: {relative_path} ({media_type})")
    
    return {
        "status": "success",
        "filename": file.filename,
        "file_path": relative_path,
        "media_type": media_type
    }


@app.post("/api/ingest")
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
    target_platforms = data.get("target_platforms", ["x"])
    account_label = data.get("account", "").strip() or None
    requires_approval = 1 if data.get("requires_approval", True) else 0
    status = "pending_approval" if requires_approval else "approved"
    raw_scheduled_at = data.get("scheduled_at", "").strip() or None
    
    # Format scheduled_at ISO timestamp
    scheduled_at = None
    if raw_scheduled_at:
        try:
            # Parse datetime string from datetime-local input
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

    logger.info(f"Ingested {len(created_ids)} post item(s) for platforms {target_platforms} with status '{status}' (scheduled_at={scheduled_at})")
    return {"status": "success", "post_ids": created_ids, "queue_status": status, "scheduled_at": scheduled_at}


@app.get("/api/approval-queue")
async def get_approval_queue():
    """Get drafts and posts pending human approval."""
    items = await asyncio.to_thread(db.get_approval_queue, 50)
    return items


@app.post("/api/posts/{post_id}/approve")
async def approve_post(post_id: int):
    """Approve a post in the queue for immediate or scheduled execution."""
    success = await asyncio.to_thread(db.approve_post, post_id)
    if not success:
        raise HTTPException(status_code=404, detail="Post not found")
    logger.info(f"Post #{post_id} approved for execution")
    return {"status": "success", "message": f"Post #{post_id} approved"}


@app.post("/api/posts/approve-all")
async def approve_all_posts():
    """Batch approve all posts pending review."""
    count = await asyncio.to_thread(db.approve_all_posts)
    logger.info(f"Batch approved {count} post(s)")
    return {"status": "success", "approved_count": count}


@app.delete("/api/posts/{post_id}")
async def reject_post(post_id: int):
    """Reject and delete a queued post draft."""
    success = await asyncio.to_thread(db.reject_post, post_id)
    if not success:
        raise HTTPException(status_code=404, detail="Post not found")
    logger.info(f"Post #{post_id} rejected and removed")
    return {"status": "success", "message": f"Post #{post_id} removed"}


@app.post("/api/posts/{post_id}/retry")
async def retry_failed_post(post_id: int):
    """Reset a failed post back to 'approved' status for immediate re-execution."""
    success = await asyncio.to_thread(db.retry_failed_post, post_id)
    if not success:
        raise HTTPException(status_code=404, detail="Post not found")
    logger.info(f"Post #{post_id} queued for retry execution")
    return {"status": "success", "message": f"Post #{post_id} queued for retry"}


@app.post("/api/maintenance/cleanup-uploads")
async def trigger_uploads_cleanup(days: int = 7):
    """Manually trigger purging of published upload media files older than N days."""
    count = await asyncio.to_thread(db.cleanup_old_uploads, days)
    logger.info(f"Maintenance: Purged {count} old upload media file(s)")
    return {"status": "success", "purged_count": count}


@app.get("/api/accounts/health")
async def get_accounts_health():
    """Get account health monitoring signals and warnings."""
    health_data = await asyncio.to_thread(db.get_account_health_summary)
    return health_data


@app.get("/api/system/backup")
async def export_system_backup():
    """
    Generate a downloadable ZIP backup of bot.db and .env configuration.
    Zero external dependencies (uses built-in zipfile module).
    """
    import zipfile
    
    timestamp = int(time.time())
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_media")
    os.makedirs(temp_dir, exist_ok=True)
    
    zip_name = f"x_automation_backup_{timestamp}.zip"
    zip_path = os.path.join(temp_dir, zip_name)

    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")
    env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")

    try:
        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            if os.path.exists(db_path):
                zipf.write(db_path, arcname="bot.db")
            if os.path.exists(env_path):
                zipf.write(env_path, arcname=".env")
                
        logger.info(f"System backup generated: {zip_name}")
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=zip_name
        )
    except Exception as e:
        logger.error(f"Failed to generate backup ZIP: {e}")
        raise HTTPException(status_code=500, detail=f"Backup creation failed: {e}")


# ── Entry Point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    from dotenv import load_dotenv
    load_dotenv()

    host = os.getenv("HOST", "0.0.0.0")
    port = int(os.getenv("PORT", "8000"))

    print(f"\n  X Automation Bot starting...")
    print(f"  Dashboard: http://localhost:{port}")
    print(f"  API Docs:  http://localhost:{port}/docs\n")

    uvicorn.run(
        "server:app",
        host=host,
        port=port,
        reload=False,
        log_level="info"
    )
