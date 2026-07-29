"""
routes/system.py — System Health, Settings, Logs, and Backup Endpoints
"""

import os
import json
import time
import zipfile
import asyncio
import logging
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse

import db
from scheduler import scheduler

logger = logging.getLogger("x_automation.routes.system")

router = APIRouter(tags=["System"])

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


@router.get("/api/status")
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


@router.get("/api/settings")
async def get_settings():
    settings = await asyncio.to_thread(db.get_all_settings)
    masked = {}
    for key, value in settings.items():
        if any(s in key.lower() for s in ["key", "secret", "token", "password", "webhook"]):
            masked[key] = "********" if value else ""
        else:
            masked[key] = value
    return masked


@router.post("/api/settings")
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


@router.post("/api/test-webhook")
async def test_webhook():
    """Send a test payload to the configured webhook URL."""
    try:
        from scheduler import send_system_notification
        await send_system_notification("test", "🎉 Webhook Test Notification", {
            "caption": "Your notification integration for Content Uploader Bot is configured correctly!",
            "platform": "system"
        })
        return {"status": "ok", "message": "Test notification payload sent successfully."}
    except Exception:
        raise HTTPException(status_code=500, detail="Failed to send test notification payload")


@router.get("/api/logs")
async def get_logs(limit: int = 100):
    logs = await asyncio.to_thread(db.get_logs, limit)
    return logs


@router.post("/api/run")
async def trigger_run():
    if scheduler.running and not scheduler.paused:
        asyncio.create_task(scheduler.run_cycle())
        logger.info("Manual cycle trigger received")
        return {"status": "ok", "message": "Scheduler cycle triggered"}
    else:
        return {"status": "warning", "message": "Scheduler is paused or stopped"}


@router.post("/api/pause")
async def pause_scheduler():
    scheduler.pause()
    logger.info("Scheduler paused via API")
    return {"status": "ok", "paused": True}


@router.post("/api/resume")
async def resume_scheduler():
    scheduler.resume()
    logger.info("Scheduler resumed via API")
    return {"status": "ok", "paused": False}


@router.get("/api/system/backup")
async def export_system_backup():
    """
    Generate a downloadable ZIP backup of bot.db and non-sensitive settings configuration.
    SECURITY HARDENING: .env is NEVER included in backups.
    """
    timestamp = int(time.time())
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    temp_dir = os.path.join(base_dir, "temp_media")
    os.makedirs(temp_dir, exist_ok=True)
    
    zip_name = f"content_uploader_backup_{timestamp}.zip"
    zip_path = os.path.join(temp_dir, zip_name)
    db_path = os.path.join(base_dir, "bot.db")

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
