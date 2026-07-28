"""
server.py - FastAPI REST API for X Automation Bot

Serves the web dashboard and exposes REST endpoints for
managing sources, accounts, settings, history, and logs.
"""

import asyncio
import ipaddress
import json
import logging
import os
import queue
import re
import secrets
import socket
import sys
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from urllib.parse import urlparse

import uvicorn
from fastapi import FastAPI, HTTPException, Request, File, UploadFile, Depends
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from dotenv import load_dotenv

import db
from scheduler import scheduler

load_dotenv()

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger("x_automation.server")

# ── Dashboard Auth Secret Setup ───────────────────────────────────────

def get_or_create_dashboard_secret() -> str:
    """Retrieve DASHBOARD_SECRET from environment or auto-generate a secure token."""
    secret = os.getenv("DASHBOARD_SECRET", "").strip()
    if not secret:
        secret = secrets.token_hex(16)
        os.environ["DASHBOARD_SECRET"] = secret
        env_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
        try:
            with open(env_path, "a", encoding="utf-8") as f:
                f.write(f"\nDASHBOARD_SECRET={secret}\n")
            logger.info("Generated new DASHBOARD_SECRET and saved to .env")
        except Exception as ex:
            logger.warning(f"Could not persist DASHBOARD_SECRET to .env: {ex}")
    return secret

DASHBOARD_SECRET = get_or_create_dashboard_secret()

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
            log_queue.put((record.levelname, record.getMessage()))
        except Exception:
            pass

logger_obj = logging.getLogger("x_automation")
if not any(isinstance(h, DBLogHandler) for h in logger_obj.handlers):
    db_handler = DBLogHandler()
    db_handler.setLevel(logging.INFO)
    logger_obj.addHandler(db_handler)


# ── SSRF Security Helper ──────────────────────────────────────────────

def validate_safe_url(url_str: str) -> str:
    """
    Validate that a URL uses http/https and does not target loopback,
    private, or cloud metadata IP addresses (SSRF Protection).
    """
    if not url_str:
        raise HTTPException(status_code=400, detail="URL cannot be empty")

    parsed = urlparse(url_str.strip())
    if parsed.scheme not in ("http", "https"):
        raise HTTPException(status_code=400, detail="Only http and https schemes are permitted")

    hostname = parsed.hostname
    if not hostname:
        raise HTTPException(status_code=400, detail="Invalid host in URL")

    # Block localhost / loopback string names directly
    if hostname.lower() in ("localhost", "127.0.0.1", "::1", "0.0.0.0"):
        raise HTTPException(status_code=400, detail="Access to local IP address or loopback is blocked")

    try:
        ip_addr = socket.gethostbyname(hostname)
        ip = ipaddress.ip_address(ip_addr)
        if ip.is_loopback or ip.is_private or ip.is_link_local or ip.is_multicast or ip.is_reserved:
            raise HTTPException(status_code=400, detail="Access to private or restricted network addresses is blocked")
    except socket.gaierror:
        raise HTTPException(status_code=400, detail=f"Could not resolve hostname: '{hostname}'")
    except ValueError:
        pass

    return url_str.strip()


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
                        mtime = os.path.getmtime(filepath)
                        if now - mtime > 12 * 3600:
                            logger.info(f"GC: Purging stale temp file: {filename}")
                            try:
                                os.remove(filepath)
                            except Exception as ex:
                                logger.warning(f"GC: Failed to delete {filename}: {ex}")
        except Exception as e:
            logger.error(f"Error in temp media garbage collector: {e}")
            
        await asyncio.sleep(3600)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup and shutdown events."""
    db.init_db()
    logger.info("Database initialized")

    db.reset_stuck_post_states()
    logger.info("Stuck post states reset successfully")

    gc_task = asyncio.create_task(temp_media_garbage_collector())

    await scheduler.start()
    logger.info("Scheduler started")

    yield

    gc_task.cancel()
    try:
        await gc_task
    except asyncio.CancelledError:
        pass

    await scheduler.stop()
    logger.info("Scheduler stopped")

    from downloader import kill_active_processes
    kill_active_processes()

    log_queue.put(None)
    writer_thread.join(timeout=2.0)
    logger.info("Scheduler stopped. Goodbye!")


app = FastAPI(
    title="X Automation Bot",
    description="Video Collector & Multi-Platform Auto-Publisher Engine",
    version="1.0.0",
    lifespan=lifespan
)

# Dynamic CORS setup
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:8000", "http://127.0.0.1:8000", "http://localhost:8001", "http://127.0.0.1:8001"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# HTTP Security Headers & Authentication Middleware
@app.middleware("http")
async def security_and_auth_middleware(request: Request, call_next):
    path = request.url.path

    # Authenticate API endpoints
    if path.startswith("/api/"):
        auth_header = request.headers.get("Authorization", "")
        token_param = request.query_params.get("auth_token", "")

        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        elif token_param:
            token = token_param.strip()

        if not secrets.compare_digest(token, DASHBOARD_SECRET):
            return JSONResponse(
                {"detail": "Unauthorized. Invalid or missing Bearer token."},
                status_code=401
            )

    response = await call_next(request)
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Security-Policy"] = (
        "default-src 'self'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
        "font-src https://fonts.gstatic.com; script-src 'self'; img-src 'self' data:;"
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

ALLOWED_SETTING_KEYS = {
    "interval_minutes",
    "caption_ai",
    "gemini_api_key",
    "caption_template",
    "enable_scheduler_jitter",
    "vertical_pad_mode",
    "x_link_placement",
    "webhook_url",
    "telegram_bot_token",
    "telegram_chat_id",
    "caption_template_x",
    "caption_template_instagram",
    "caption_template_tiktok",
    "mock_posting"
}

@app.get("/api/settings")
async def get_settings():
    settings = await asyncio.to_thread(db.get_all_settings)
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

    clean = {}
    for key, value in data.items():
        if key not in ALLOWED_SETTING_KEYS:
            continue
        if isinstance(value, str) and value == "********":
            continue
        clean[key] = value

    await asyncio.to_thread(db.save_all_settings, clean)
    logger.info(f"Settings updated: {list(clean.keys())}")
    return {"status": "ok", "updated": list(clean.keys())}


@app.post("/api/test-webhook")
async def test_webhook():
    """Send a test payload to the configured webhook URL."""
    try:
        from scheduler import send_system_notification
        await send_system_notification("test", "🎉 Webhook Test Notification", {
            "caption": "Your notification integration for X Automation Bot is configured correctly!",
            "platform": "system"
        })
        return {"status": "ok", "message": "Test notification payload sent successfully."}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to send test notification payload")


# ── Sources Endpoints ─────────────────────────────────────────────────

@app.get("/api/sources")
async def get_sources():
    sources = await asyncio.to_thread(db.get_sources)
    return sources


@app.post("/api/sources")
async def add_source(request: Request):
    data = await request.json()
    url = data.get("url", "")
    name = data.get("name", "")
    platform = data.get("platform", "other")
    target_platforms = data.get("target_platforms", "x")

    if not url:
        raise HTTPException(status_code=400, detail="URL is required")

    # SSRF Protection
    validate_safe_url(url)

    source_id = await asyncio.to_thread(db.add_source, url, name, platform, target_platforms)
    if source_id is None:
        raise HTTPException(status_code=409, detail="Source URL already exists")

    logger.info(f"Source added: {name} ({url}) -> targets: {target_platforms}")
    return {"status": "ok", "id": source_id}


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
    for acct in accounts:
        acct.pop("credentials_enc", None)
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
        raise ValueError(f"Credentials validation failed")


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
    
    creds = data.get("credentials")
    if not creds or not isinstance(creds, dict):
        creds = {}
        for k, v in data.items():
            if k not in ("label", "platform", "auth_mode", "proxy_url", "user_agent"):
                creds[k] = v

    if not label:
        raise HTTPException(status_code=400, detail="Label is required")

    try:
        try:
            await validate_account_credentials(platform, auth_mode, creds, proxy_url, user_agent)
        except Exception:
            raise HTTPException(status_code=400, detail="Account credentials verification failed")

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
        raise HTTPException(status_code=500, detail="Internal server error adding account")


@app.delete("/api/accounts/{label}")
async def remove_account(label: str, platform: str = None):
    await asyncio.to_thread(db.delete_account, label, platform)
    logger.info(f"Account removed: {label} ({platform or 'all'})")
    return {"status": "ok"}


# ── History & Logs Endpoints ─────────────────────────────────────────

@app.get("/api/history")
async def get_history(limit: int = 50):
    posts = await asyncio.to_thread(db.get_history, limit)
    return posts


@app.get("/api/logs")
async def get_logs(limit: int = 100):
    logs = await asyncio.to_thread(db.get_logs, limit)
    return logs


# ── Manual Trigger Endpoints ──────────────────────────────────────────

@app.post("/api/run")
async def trigger_run():
    if scheduler.running and not scheduler.paused:
        asyncio.create_task(scheduler.run_cycle())
        logger.info("Manual cycle trigger received")
        return {"status": "ok", "message": "Scheduler cycle triggered"}
    else:
        return {"status": "warning", "message": "Scheduler is paused or stopped"}


@app.post("/api/pause")
async def pause_scheduler():
    scheduler.pause()
    logger.info("Scheduler paused via API")
    return {"status": "ok", "paused": True}


@app.post("/api/resume")
async def resume_scheduler():
    scheduler.resume()
    logger.info("Scheduler resumed via API")
    return {"status": "ok", "paused": False}


@app.post("/api/quick-post")
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
        from downloader import fetch_metadata, download_video, transcode_for_x, cleanup
        from caption_gen import generate_caption
        from publisher import get_publisher

        raw_path = None
        transcoded_path = None
        try:
            meta = None
            try:
                meta = await asyncio.to_thread(fetch_metadata, url)
                if meta and meta.get("title"):
                    await asyncio.to_thread(db.update_post_title, post_id, meta["title"])
            except Exception as me:
                logger.warning(f"Failed to fetch metadata for video ID update: {me}")

            title = meta.get("title", "") if meta else ""
            desc = meta.get("description", "") if meta else ""

            await asyncio.to_thread(db.update_post_status, post_id, "downloading")
            try:
                raw_path = await asyncio.to_thread(download_video, url)
                await asyncio.to_thread(db.update_post_status, post_id, "transcoding")
                transcoded_path = await asyncio.to_thread(transcode_for_x, raw_path, platform=platform)
            except Exception as de:
                logger.warning(f"Failed to download/transcode video media: {de}")
                transcoded_path = None

            if caption_override:
                generated = caption_override
            elif title:
                generated = await asyncio.to_thread(generate_caption, title, desc, platform=platform)
            else:
                generated = caption

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


# ── Multi-Format Ingestion & Upload Hardening ────────────────────────

MAX_UPLOAD_BYTES = 50 * 1024 * 1024  # 50 MB limit

ALLOWED_MAGIC_HEADERS = [
    b"\x00\x00\x00",     # MP4 / MOV container ftyp
    b"RIFF",             # AVI / WEBP
    b"\x1a\x45\xdf\xa3", # WebM / MKV
    b"\x89PNG",          # PNG image
    b"\xff\xd8\xff",     # JPEG image
    b"GIF8"              # GIF image
]

@app.post("/api/upload")
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
    Generate a downloadable ZIP backup of bot.db and non-sensitive settings configuration.
    SECURITY HARDENING: .env is NEVER included in backups.
    """
    import zipfile
    
    timestamp = int(time.time())
    temp_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "temp_media")
    os.makedirs(temp_dir, exist_ok=True)
    
    zip_name = f"x_automation_backup_{timestamp}.zip"
    zip_path = os.path.join(temp_dir, zip_name)

    db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.db")

    try:
        # Create non-sensitive settings export
        settings = await asyncio.to_thread(db.get_all_settings)
        export_settings = {}
        for k, v in settings.items():
            if not any(s in k.lower() for s in ["key", "secret", "token", "password"]):
                export_settings[k] = v

        settings_json_path = os.path.join(temp_dir, f"settings_export_{timestamp}.json")
        with open(settings_json_path, "w", encoding="utf-8") as sf:
            json.dump(export_settings, sf, indent=2)

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            if os.path.exists(db_path):
                zipf.write(db_path, arcname="bot.db")
            if os.path.exists(settings_json_path):
                zipf.write(settings_json_path, arcname="settings_config.json")
                
        if os.path.exists(settings_json_path):
            os.remove(settings_json_path)

        logger.info(f"System backup generated (excluding secrets): {zip_name}")
        return FileResponse(
            zip_path,
            media_type="application/zip",
            filename=zip_name
        )
    except Exception as e:
        logger.error(f"Failed to generate backup ZIP: {e}")
        raise HTTPException(status_code=500, detail="Backup creation failed")


# ── Entry Point ───────────────────────────────────────────────────────

if __name__ == "__main__":
    host = os.getenv("HOST", "127.0.0.1")
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
